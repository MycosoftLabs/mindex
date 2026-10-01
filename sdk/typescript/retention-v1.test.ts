import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { RetentionClient, RetentionError, validateArtifactReceipt } from "./retention-v1.ts";

const id = "11111111-1111-4111-8111-111111111111";
const tenant = "22222222-2222-4222-8222-222222222222";
const project = "33333333-3333-4333-8333-333333333333";
const payload = new TextEncoder().encode('{"synthetic":true}');
const row = {contract_version:"retention.v1",artifact_id:id,job_id:id,task_id:id,tenant_id:tenant,
  project_id:project,kind:"dataset",classification:"private",sha256:createHash("sha256").update(payload).digest("hex"),
  byte_length:payload.length,media_type:"application/json",state:"verified",source_event_at:null,
  received_at:"2026-10-01T00:00:00Z",available_at:"2026-10-01T00:01:00Z",retention_until:"2026-11-01T00:00:00Z",
  durability:"postgres_committed",archive_verified:true,learned:false};
function client(transport: typeof fetch) {
  return new RetentionClient("http://127.0.0.1:55999", {accessToken:"fixture.original.jwt",tenantId:tenant,projectId:project}, transport);
}
function response(value: unknown, status=200) { return new Response(JSON.stringify(value),{status}); }
function content(bytes=payload) {
  return new Response(bytes,{headers:{"X-Artifact-Id":id,"X-Artifact-SHA256":row.sha256,"X-Retention-Contract":"retention.v1"}});
}
test("exact authorized content bytes are hashed and metadata rechecked after transfer", async()=>{
  const paths:string[]=[];
  const c=client(async(url,init)=>{
    paths.push(String(url));
    assert.equal(new Headers(init?.headers).get("authorization"),"Bearer fixture.original.jwt");
    assert.equal(new Headers(init?.headers).get("x-project-id"),project);
    assert.equal(init?.cache,"no-store"); assert.equal(init?.redirect,"error");
    return String(url).endsWith("/content")?content():response(row);
  });
  const result=await c.getContent(id);assert.deepEqual(result.bytes,payload);assert.equal(paths.length,3);
});
test("pending admission cannot become verified content",async()=>{
  let calls=0;const c=client(async()=>{calls++;return response({...row,state:"pending",archive_verified:false,available_at:null});});
  await assert.rejects(()=>c.getContent(id),(e:RetentionError)=>e.status===409);assert.equal(calls,1);
});
test("scope-mismatched receipt fails closed",async()=>{
  const c=client(async()=>response({...row,project_id:id}));
  await assert.rejects(()=>c.getArtifact(id),/receipt_scope_mismatch/);
});
test("wrong artifact returned for guessed identifier is rejected",async()=>{
  const c=client(async()=>response({...row,artifact_id:tenant}));
  await assert.rejects(()=>c.getArtifact(id),/receipt_identifier_mismatch/);
});
test("same length corrupt bytes fail hash verification",async()=>{
  const c=client(async(url)=>String(url).endsWith("/content")?content(new Uint8Array(payload.length)):response(row));
  await assert.rejects(()=>c.getContent(id),/artifact_integrity_failed/);
});
test("revocation after content fetch denies delivery",async()=>{
  let calls=0;const c=client(async()=>{calls++;return calls===1?response(row):calls===2?content():response({error:"membership_required"},403);});
  await assert.rejects(()=>c.getContent(id),(e:RetentionError)=>e.status===403);
});
test("oversized response is bounded before return",async()=>{
  const c=client(async(url)=>String(url).endsWith("/content")?content(new Uint8Array(payload.length+1)):response(row));
  await assert.rejects(()=>c.getContent(id),/response_too_large/);
});
test("admission sends exact raw body with stable idempotency",async()=>{
  const c=client(async(url,init)=>{
    assert.equal(init?.method,"POST");assert.equal(init?.body,payload);
    const h=new Headers(init?.headers);assert.equal(h.get("idempotency-key"),"stable-key");assert.equal(h.get("x-artifact-kind"),"dataset");
    return response({...row,state:"pending",archive_verified:false,available_at:null,created:true},202);
  });
  const admitted=await c.admit(payload,{idempotencyKey:"stable-key",kind:"dataset",mediaType:"application/json"});
  assert.equal(admitted.archive_verified,false);
});
test("no raw upstream secret/error body is exposed",async()=>{
  const c=client(async()=>response({error:"private bearer source secret"},503));
  await assert.rejects(()=>c.getArtifact(id),(e:RetentionError)=>e.status===503&&!e.message.includes("secret"));
});
test("remote plaintext and credential-bearing origins are rejected",()=>{
  for(const origin of ["http://private.example","https://user:password@example.test","https://example.test?token=bad"])
    assert.throws(()=>new RetentionClient(origin,{accessToken:"test",tenantId:tenant,projectId:project}),/invalid_retention_origin/);
});
test("verified state needs valid committed timestamps and no learned claim",()=>{
  for(const patch of [{available_at:null},{learned:true},{archive_verified:false},{byte_length:0},{sha256:"not-hash"}])
    assert.throws(()=>validateArtifactReceipt({...row,...patch}),/invalid_receipt/);
});
test("list is bounded and never claims a stable snapshot",async()=>{
  const c=client(async()=>response({contract_version:"retention.v1",items:[row]}));
  const listing=await c.list("dataset",1);assert.equal(listing.snapshot_complete,false);assert.equal(listing.cursor,null);
  await assert.rejects(()=>c.list("",101),/invalid_query/);
});
