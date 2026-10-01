/** Canonical MINDEX retention.v1 client. Server/BFF use only; never persist JWTs.
 * No browser owner field, service key, cache or alternate database is an identity.
 */
export const RETENTION_CONTRACT = "retention.v1" as const;
export type ArtifactKind = "dataset" | "chart" | "artifact";
export type ArtifactState = "pending" | "archiving" | "verified" | "quarantined" | "cancelled" | "deleted";
export interface ScopeCredentials { accessToken: string; tenantId: string; projectId: string }
export interface Principal {
  contract_version: typeof RETENTION_CONTRACT;
  issuer: string; subject: string; tenant_id: string; project_id: string;
}
export interface ArtifactReceipt {
  contract_version: typeof RETENTION_CONTRACT;
  artifact_id: string; job_id: string; task_id: string;
  tenant_id: string; project_id: string; kind: ArtifactKind;
  classification: "private"; sha256: string; byte_length: number; media_type: string;
  state: ArtifactState; source_event_at: string | null; received_at: string;
  available_at: string | null; retention_until: string;
  durability: "postgres_committed"; archive_verified: boolean; learned: false;
  created?: boolean; last_error_code?: string | null;
}
export interface MemoryReceipt {
  contract_version: typeof RETENTION_CONTRACT;
  memory_id: string; artifact_id: string; summary: string;
  artifact_sha256: string; reference_verified: true; learned: false;
}
export interface AdmissionOptions {
  idempotencyKey: string; kind?: ArtifactKind; mediaType?: string; sourceEventAt?: string;
}
export class RetentionError extends Error {
  constructor(public readonly code: string, public readonly status: number) { super(code); }
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const STATES = new Set(["pending", "archiving", "verified", "quarantined", "cancelled", "deleted"]);
function identifier(value: string): string {
  if (!UUID.test(value)) throw new RetentionError("invalid_identifier", 422);
  return value;
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new RetentionError("invalid_receipt", 502);
  return value as Record<string, unknown>;
}
export function validateArtifactReceipt(value: unknown): ArtifactReceipt {
  const r = object(value);
  if (r.contract_version !== RETENTION_CONTRACT || r.classification !== "private" ||
      !UUID.test(String(r.artifact_id)) || !UUID.test(String(r.job_id)) || r.task_id !== r.job_id ||
      !UUID.test(String(r.tenant_id)) || !UUID.test(String(r.project_id)) ||
      !DIGEST.test(String(r.sha256)) || !Number.isSafeInteger(r.byte_length) ||
      Number(r.byte_length) < 1 || Number(r.byte_length) > 16 * 1024 * 1024 ||
      !STATES.has(String(r.state)) || !["dataset", "chart", "artifact"].includes(String(r.kind)) ||
      typeof r.media_type !== "string" || r.durability !== "postgres_committed" || r.learned !== false ||
      r.archive_verified !== (r.state === "verified") ||
      typeof r.received_at !== "string" || !Number.isFinite(Date.parse(r.received_at)) ||
      typeof r.retention_until !== "string" || !Number.isFinite(Date.parse(r.retention_until)) ||
      (r.state === "verified" && (typeof r.available_at !== "string" || !Number.isFinite(Date.parse(r.available_at))))) {
    throw new RetentionError("invalid_receipt", 502);
  }
  return r as unknown as ArtifactReceipt;
}

/** A client instance represents exactly one request's identity/scope. Do not share
 * instances across users. Re-resolve credentials for a later user request. */
export class RetentionClient {
  private readonly base: string;
  constructor(baseUrl: string, private readonly credentials: ScopeCredentials,
              private readonly transport: typeof fetch = fetch) {
    const parsed = new URL(baseUrl);
    if (parsed.username || parsed.password || parsed.search || parsed.hash ||
        (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)))) {
      throw new RetentionError("invalid_retention_origin", 503);
    }
    this.base = baseUrl.replace(/\/$/, "") + "/api/mindex/retention/v1";
    identifier(credentials.tenantId); identifier(credentials.projectId);
    if (!credentials.accessToken || credentials.accessToken.length > 16384 || /[\r\n]/.test(credentials.accessToken)) {
      throw new RetentionError("authentication_required", 401);
    }
  }
  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.credentials.accessToken}`);
    headers.set("X-Tenant-Id", this.credentials.tenantId);
    headers.set("X-Project-Id", this.credentials.projectId);
    let response: Response;
    try {
      response = await this.transport(this.base + path, { ...init, headers, cache: "no-store",
        redirect: "error", signal: AbortSignal.timeout(35000) });
    } catch { throw new RetentionError("retention_unavailable", 503); }
    if (!response.ok) {
      // Never surface upstream bodies, stack traces, source bytes or credentials.
      throw new RetentionError(response.status === 409 ? "retention_conflict_or_pending" : "retention_request_failed", response.status);
    }
    return response;
  }
  private async json(response: Response): Promise<unknown> {
    const bytes = await this.bounded(response, 256 * 1024);
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
    catch { throw new RetentionError("invalid_receipt", 502); }
  }
  private async bounded(response: Response, maximum: number): Promise<Uint8Array> {
    const stated = response.headers.get("content-length");
    if (stated !== null && (!/^\d+$/.test(stated) || Number(stated) > maximum)) {
      await response.body?.cancel(); throw new RetentionError("response_too_large", 502);
    }
    const reader = response.body?.getReader();
    if (!reader) throw new RetentionError("empty_response", 502);
    const chunks: Uint8Array[] = []; let total = 0;
    try {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        total += value.byteLength;
        if (total > maximum) { await reader.cancel(); throw new RetentionError("response_too_large", 502); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const output = new Uint8Array(total); let offset = 0;
    for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength; }
    return output;
  }
  private scoped(value: unknown): ArtifactReceipt {
    const receipt = validateArtifactReceipt(value);
    if (receipt.tenant_id !== this.credentials.tenantId || receipt.project_id !== this.credentials.projectId) {
      throw new RetentionError("receipt_scope_mismatch", 502);
    }
    return receipt;
  }
  async principal(): Promise<Principal> {
    const result = object(await this.json(await this.request("/principal")));
    if (result.contract_version !== RETENTION_CONTRACT || typeof result.issuer !== "string" ||
        !UUID.test(String(result.subject)) || result.tenant_id !== this.credentials.tenantId ||
        result.project_id !== this.credentials.projectId) throw new RetentionError("invalid_principal", 502);
    return result as unknown as Principal;
  }
  async admit(payload: Uint8Array, options: AdmissionOptions): Promise<ArtifactReceipt> {
    if (!payload.byteLength || payload.byteLength > 16 * 1024 * 1024) throw new RetentionError("payload_size_invalid", 413);
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(options.idempotencyKey)) throw new RetentionError("invalid_idempotency_key", 422);
    const headers: Record<string, string> = { "Idempotency-Key": options.idempotencyKey,
      "Content-Type": options.mediaType ?? "application/octet-stream", "X-Artifact-Kind": options.kind ?? "artifact" };
    if (options.sourceEventAt) headers["X-Source-Event-At"] = options.sourceEventAt;
    return this.scoped(await this.json(await this.request("/artifacts", {method: "POST", headers,
      body: payload as unknown as BodyInit})));
  }
  async getArtifact(artifactId: string): Promise<ArtifactReceipt> {
    const receipt = this.scoped(await this.json(await this.request(`/artifacts/${identifier(artifactId)}`)));
    if (receipt.artifact_id !== artifactId) throw new RetentionError("receipt_identifier_mismatch", 502);
    return receipt;
  }
  async getContent(artifactId: string): Promise<{ receipt: ArtifactReceipt; bytes: Uint8Array }> {
    const receipt = await this.getArtifact(artifactId);
    if (!receipt.archive_verified) throw new RetentionError("artifact_not_verified", 409);
    const response = await this.request(`/artifacts/${identifier(artifactId)}/content`);
    if (response.headers.get("x-artifact-id") !== artifactId || response.headers.get("x-artifact-sha256") !== receipt.sha256 ||
        response.headers.get("x-retention-contract") !== RETENTION_CONTRACT) throw new RetentionError("content_receipt_mismatch", 502);
    const bytes = await this.bounded(response, receipt.byte_length);
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as BufferSource)), x => x.toString(16).padStart(2, "0")).join("");
    if (bytes.byteLength !== receipt.byte_length || digest !== receipt.sha256) throw new RetentionError("artifact_integrity_failed", 502);
    // Re-authorize after download and before returning any private bytes to caller.
    const current = await this.getArtifact(artifactId);
    if (!current.archive_verified || current.sha256 !== digest) throw new RetentionError("artifact_unavailable", 409);
    return { receipt: current, bytes };
  }
  async list(query = "", limit = 50): Promise<{items: ArtifactReceipt[]; snapshot_complete: false; cursor: null}> {
    if (query.length > 200 || !Number.isInteger(limit) || limit < 1 || limit > 100) throw new RetentionError("invalid_query", 422);
    const result = object(await this.json(await this.request(`/artifacts?${new URLSearchParams({query, limit: String(limit)})}`)));
    if (result.contract_version !== RETENTION_CONTRACT || !Array.isArray(result.items) || result.items.length > limit) throw new RetentionError("invalid_receipt", 502);
    return {items: result.items.map(value => this.scoped(value)), snapshot_complete: false, cursor: null};
  }
  async getJob(jobId: string): Promise<ArtifactReceipt> {
    const receipt = this.scoped(await this.json(await this.request(`/jobs/${identifier(jobId)}`)));
    if (receipt.job_id !== jobId) throw new RetentionError("receipt_identifier_mismatch", 502);
    return receipt;
  }
  async cancel(jobId: string): Promise<unknown> { return this.json(await this.request(`/jobs/${identifier(jobId)}/cancel`, {method: "POST"})); }
  async delete(artifactId: string): Promise<unknown> { return this.json(await this.request(`/artifacts/${identifier(artifactId)}`, {method: "DELETE"})); }
  async remember(artifactId: string, summary: string): Promise<MemoryReceipt> {
    if (!summary || summary.length > 2000) throw new RetentionError("invalid_memory_summary", 422);
    const value = await this.json(await this.request("/memories", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({artifact_id: identifier(artifactId), summary})}));
    return this.memory(value, artifactId);
  }
  private memory(value: unknown, artifactId?: string): MemoryReceipt {
    const r = object(value);
    if (r.contract_version !== RETENTION_CONTRACT || !UUID.test(String(r.memory_id)) || !UUID.test(String(r.artifact_id)) ||
        (artifactId && r.artifact_id !== artifactId) || !DIGEST.test(String(r.artifact_sha256)) ||
        r.reference_verified !== true || r.learned !== false || typeof r.summary !== "string") throw new RetentionError("invalid_memory_receipt", 502);
    return r as unknown as MemoryReceipt;
  }
  async getMemory(memoryId: string): Promise<MemoryReceipt> {
    const r = this.memory(await this.json(await this.request(`/memories/${identifier(memoryId)}`)));
    if (r.memory_id !== memoryId) throw new RetentionError("receipt_identifier_mismatch", 502);
    return r;
  }
}
