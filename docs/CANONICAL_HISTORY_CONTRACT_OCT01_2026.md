# MINDEX canonical history contract and delivery gates

Date: 2026-10-01. This document separates the owner's current requirements, source implementation, dated runtime evidence, and work still required. This local change has not been deployed. Historical deployment instructions in older documents are references, not authorization to run them.

## Authoritative intended architecture

The owner requires MINDEX to be the canonical retained, queryable data history for **all applications**. MYCA, search, NLM, FormSpace and Earth Simulator must answer historical questions from internal MINDEX records, whether the question concerns minutes or months, taxonomy, genetics, chemistry or location. A query must not fetch an internet substitute when internal history is absent or unavailable.

Continuous acquisition from internet/API, ETL, n8n, devices and sensor streams is a separate path. It must preserve each source response/event durably with provenance, source and receipt time, ownership and stable identity, then produce a normalized representation with categorization, title, tags, classification and derivation lineage. Live Earth display may fetch fresh data directly to minimize latency, but every response/event must enter the durable acquisition path immediately. Rendering a response is not an ingestion acknowledgement. Previously retained information is not automatically incorporated into trained model weights.

AWS primary, verified backup and a tested local fallback are intended architecture, not established current readiness. Container, building and AWS operational consoles are internal operator surfaces. Authenticated WorldView customers receive only authorized catalog/data views; internal operations, customer-private records and military telemetry are not automatically public.

Cryptographic provenance is required for valuable science, telemetry and genetics: Bitcoin, Solana and Hypergraph proof receipts, DNA/IP asset association, optional Ordinals, and an operator/SOC pending workflow. Keep private data offchain. Hash commitments also require privacy review: a public deterministic hash can reveal equality or permit guessing low-entropy content. These requirements do not authorize transactions, publication, signatures, credentials, spend or deployment.

## Implemented bounded read repair

`GET /api/mindex/unified-search` reads retained PostgreSQL records or the internal cache. It no longer imports live scrapers, launches provider searches, uses a request session in a background ingestion task, syncs search results to Supabase, or schedules agent work. The earth/nearby functions in this same router also no longer schedule agent tasks. Their selected-domain failure isolation remains intact.

The new cache policy key `internal-only-v1` prevents an old live-capable cached response from entering the canonical read path. Current-record reads retain the existing maximum 120-second cache TTL; this is not a completeness guarantee. Historical reads bypass all cache methods.

Supply `since` and/or `until` only to the main unified endpoint. Each provided bound must be an ISO datetime with an explicit UTC offset. Bounds normalize to UTC; date-only/naive/invalid values and equal/reversed intervals return structured 422 errors. The interval is **[since, until)**. Open bounds are allowed. Unknown domain names, unsupported historical domains and unsupported content filters return 422 before SQL. A supported historical location requires both finite WGS84 coordinates and a positive finite radius.

| Qualified event-time domain | Physical source | Semantics and limits |
|---|---|---|
| `observations` | `obs.observation`, left join `core.taxon` | Filters `observed_at` before ordering and LIMIT. Taxon labels are current catalog labels, not historical taxonomic reconstruction. This bounded slice excludes `species.sightings`. |
| `crep_entities` | `crep.unified_entities` | Filters retained `observed_at`; query text and location predicates are conjunctive. No claim all upstream entity updates were preserved. |

The SQL uses fixed table/column allowlists and bound values. Historical output identifies each record table. A `coverage` object explicitly returns `internal_only`, `external_fallback=false`, current/event-time mode, queried tables, empty domains, `completeness=unverified`, and a null ingestion watermark. Zero matches means no retained matches, not no real-world events. A selected database failure still returns 503 with partial results and domain errors; it does not trigger acquisition.

This is not a general as-of database, a tenant migration, an ingestion deployment or an authenticated history export API. Current taxonomy/chemistry/genetics rows cannot yet reconstruct earlier versions. The existing current observations branch still references legacy `core.observation`; the new historical branch follows the actual observations router's `obs.observation` schema. That legacy current-query defect remains a separate repair. `/earth` and `/nearby` do not advertise temporal parameters; clients needing event-time semantics must use the main endpoint.

## Integration matrix and gaps

| Layer / consumer | Source evidence | Current gap / next bounded change |
|---|---|---|
| Unified search | `mindex_api/routers/unified_search.py`, `history_query.py` | This repair removes hidden acquisition and implements two temporal domains. Expand each domain only after confirming immutable/event-time storage and indexes. |
| Other MINDEX search/detail | `scrape_pipeline.py`, `routers/genetics.py` | Generic scrape-on-miss pipeline and accession-miss GenBank fetch/store remain elsewhere. Route canonical consumers to internal-only read services; expose acquisition separately, not as a silent detail lookup. |
| Website search / MYCA | `app/api/search/unified-v2/route.ts` in website; MAS `consciousness/search_orchestrator.py` | MAS/provider fallbacks, live observations, Exa/weather/AI and fire-and-forget grafting are not a canonical historical contract. Propagate a strict history intent to an internal API; remove remote fallback for that intent. Do not report queued counts without durable receipts. |
| Location / Earth history | Website `app/api/search/location/route.ts`, `app/api/earth-simulator/inaturalist/route.ts` | Direct iNaturalist responses and TTL memory cache do not provide durable historical evidence. Existing observed-on queries are external at query time. Separate live display from internal history and report missing coverage. |
| Live CREP / OEI | Website `lib/crep/mindex-ingest.ts`, `lib/oei/mindex-ingest.ts` | Normalized HTTP batch success and optional disabled writeback are not raw durable retention. Require durable receipt, retry outbox and linked normalization before advancing source watermarks. |
| ETL | `mindex_etl/scheduler.py`, `jobs/sync_earth_data.py` | Jobs target configured PostgreSQL; scheduler state is largely process-local, not an AWS durable execution/acknowledgement plane. Fifteen-minute scheduler polling limits shorter configured cadences. Several latest-state upserts replace earlier versions. Add per-source committed checkpoints, leases and immutable event identities. |
| Stub collectors / n8n | `jobs/s3_collector.py`, `jobs/device_storage_collector.py`, `docs/ALL_LIFE_ETL_MAY02_2026.md` | Collector stubs do not populate MINDEX. n8n run-all code node was documented as needing a secure runner; workflow existence is not execution proof. Require signed execution destination, task ID and committed receipt. |
| Raw public capture | `source_capture.py`, `source_capture_s3.py`, `routers/source_capture.py`, `docs/DURABLE_SOURCE_CAPTURE.md` | Existing isolated slice is **public/non-customer only**, disabled by default. Atomic PostgreSQL receipt/raw bytes, idempotency, fenced archival worker and version/hash verified S3 exist. GET-by-ID is not a searchable normalized historical catalog. Do not route private uploads into it. |
| Library / classification | `routers/library.py`, `services/sine_acoustic/`, library docs | Acoustic blob labels, source catalogs, model/output evidence are implemented. Archive bytes, catalog rows and a searchable taxon are different artifacts. Gas/chemical catalog expansion in older docs is planned. Dated SINE model qualification does not establish current all-modality inference or full NLM training. |
| NLM / FormSpace customer artifacts | Website authenticated BFFs; MAS FormSpace and NLM routes | Unsigned forwarded user-ID headers are not proof of identity. Require verified issuer/sub, server-derived organization/project membership and ownership checks on datasets, jobs, memories and artifacts. Existing service/API-key gates do not establish tenant isolation. |
| Memory / recall | MAS memory coordinator and MINDEX graph adapter | Durable graph/table existence does not establish per-tenant recall authorization, evidence lineage or cross-app query completeness. Reference MINDEX artifact IDs; avoid a second canonical data silo. |
| WorldView | `routers/worldview/search.py`, `auth.py`, `main.py` | Public domain allowlist and API-key caller exist. This adapter currently flattens search output; it does not propagate all new coverage/time metadata. Add a versioned public history envelope with allowed sources, ownership policy and coverage before claiming customer history support. |
| AWS primary | Website `lib/mindex-base-url.ts`; read-only AWS receipt below | Website default remains LAN MINDEX. No running AWS MINDEX primary was found in the checked region/services. Primary routing/failover must follow restoration and data-plane qualification, not a placeholder endpoint. |

## Durable acquisition and query contract to implement next

1. Define a principal `(issuer, subject)` and server-resolved tenant/project, source allowlist, sensitivity, license and retention policy. Separate public, tenant-private and operator-only schemas/views. Test cross-tenant ID, search, download, cache and job access denial.
2. Persist a capture envelope before acknowledgement: capture ID, stable source event/version key, raw hash/bytes or verified object version, source event time (nullable if unknown), receipt time, owner, content type, source URL/reference and acquisition task ID. A missing event timestamp must not silently become receipt time. Idempotent retry returns the same receipt; changed payload under the same key is a conflict.
3. A leased worker normalizes committed captures into versioned domain records, preserving raw capture ID, transformation/version, units, taxonomy/category/title/tags, uncertainty and lineage. One stable identity may have many immutable event/version records. Latest-state indexes are derived views. Do not equate content deduplication with event deduplication.
4. Persist per-source acquisition/normalization checkpoints, event-time coverage intervals, known gaps, quarantines and last committed receipt. Late arrivals and reclassification must not erase prior history. Query responses include requested/available ranges, watermark, missing sources and truncation/pagination; unavailable is distinct from a healthy empty slice.
5. Link training sets, splits, model/artifact hashes, validation receipts, tool results and MYCA memory/task references to these records. Retained evidence and model updates remain different lifecycle states. User preferences may use existing Supabase RLS; canonical artifacts remain in MINDEX.
6. An explicit live-display path can render promptly while showing retention state. Durable handoff must be acknowledged, retried and observable; it cannot be a swallowed promise. On a failed handoff the UI must not claim retained history.

## Cryptographic provenance: existing source versus intended proof

| Capability | Implemented source | Missing qualification / required next state |
|---|---|---|
| Content hash / local DAG | `ledger/bitcoin_ordinals.py` SHA-256, `hypergraph.py`, `dag.py`, `routers/ip_assets.py` | Hashing and local rows exist. DAG insertion uses an empty parent list; hash lookup is not a verified ancestry proof. Canonical serialization, signed source provenance and complete parent lineage need explicit contracts. |
| Hypergraph | `hypergraph_client.py` optional HTTP POST; `anchor_service.py` local DAG and anchor update | A 2xx submission is not finality. Local anchor can succeed without a configured remote endpoint. Require authenticated idempotent submission, returned transaction identity, independent inclusion/finality verification and failure/retry states. |
| Solana | Read-only health/slot/token helpers; `record_solana_binding` database insert | `anchor_service.py` writes `solana:binding:...` / `solana:pending:...` into `solana_signature`. These are local markers, not cryptographic signatures or confirmed transactions. No mint/transfer/finality proof established. |
| Bitcoin / optional Ordinals | Read-only Core/mempool status; OP_RETURN payload metadata; local ordinal rows | OP_RETURN and an Ordinals inscription are distinct. Generated `op_return:...` / `btc:ordinal:...` identifiers are not verified onchain inscription IDs. No funded signing, broadcast, inclusion or confirmation proof established. |
| DNA / IP | `ip.ip_asset`, Hypergraph anchors, ordinal references, Solana bindings | Asset existence checks and supplied metadata do not establish biological sample chain of custody, ownership rights, token issuance or legal exclusivity. Tie consent/license and sample evidence to private artifacts; public proof requires explicit authorization. |
| SOC pending / military evidence | Anchor list/SSE, `ip_review` marker, optional Platform One correlation | Latest-row polling is not a durable pending queue. Add registered → validated → approved → submitted → confirmed/finalized states, rejected/failed/reorg states, signed actor/time receipts and operator authorization. A generic health check is not military compliance certification. |

The current anchor orchestrator returns `ok=true` / `anchored_to_mindex` after local database commit. That must not be presented as Bitcoin/Solana/Hypergraph confirmation. Existing mount-level internal authentication is distinct from per-asset tenant authorization. No ledger service was contacted or transaction submitted during this review.

## AWS, backup and restore evidence

Fresh read-only control-plane observation **2026-10-01 22:40:02 UTC**, scoped to the configured account and **us-east-1**: website staging and GPU qualification EC2 instances were stopped; RDS DB instance and ECS cluster lists were empty. No running MINDEX primary found in those checked services. This is not an all-region/all-service inventory.

The exact existing S3 physical backup object version still exists, 1,215,188,709 bytes, KMS encrypted. Prior backup checksum/readback receipt verified SHA-256 `2db906809fb1d87574de6fb5c9c892dd9f91f327b7858263b3d0a6c232891c71`; this review rechecked metadata/version, not the complete bytes again. Separate synthetic 139-byte capture-adapter and 158-byte storage round trips qualified transport, not production ingestion.

Prior reviewed restoration imported seven exact artifact versions and verified 656 assets / 15,222,862,016 logical bytes. Its receipt explicitly says `database_restore=false`, `app_started=false`, `primary_ready=false`. The subsequent isolated website smoke failed `/healthz`, `/`, and `/api/health` with URL errors. The physical database restoration plan identifies PostgreSQL/PostGIS/pgvector binary compatibility; a compatible candidate was prepared but a successful production-backup database restore was not evidenced. A successful small fixture pg_dump/restore is not that restore.

Backup presence is therefore qualified; AWS application/database primary and tested local fallback are **not qualified**. The old zero-quota `aws-migration-readiness.md` is stale. Do not use it to describe current quota or the above completed archive work. No infrastructure was changed in this task.

## Verification and limits

55 offline tests passed across `test_history_query_contract.py`, `test_batch5_search_contract.py` and `test_worldview_search_contract.py`. Tests cover timezone/invalid ranges, SQL predicates before LIMIT, half-open boundaries in a real SQLite predicate fixture, unsupported domains/filters, no acquisition on hits/misses, prior-cache exclusion, sequential sessions, partial failures and WorldView adaptation. Network connects are prohibited in new tests except the Windows asyncio implementation's private socketpair wakeup pipe; configured cache is local-only in fixtures.

These tests do not qualify PostgreSQL/PostGIS schema, index plans, actual tenant policies, ingestion watermarks, deployed API behavior or external provider/ledger availability. The retained Sept30 observation-query preflight timed out around five seconds; a query performance gate remains required. No live database writes, full service build, deploy, commit or push was performed.

Two obsolete regression inputs previously treated date-only `since`/`until` on taxa as accepted cache options. They were removed from the cache-identity parameterization and replaced with explicit invalid-time and unsupported-history tests; cache isolation and domain failure assertions remain. Unsafe side-effect helper monkeypatches were removed because those functions/imports were removed from the router.

## Architecture document inventory and interpretation

The audit inventory records 46 tracked Markdown files (43 under docs plus three root/API documents), their Git blob IDs, line counts, headings and architecture-related excerpts. It covers every tracked architecture document in this checkout, not every document in all Mycosoft repositories. Full source/docs review was concentrated on the contracts cited above; inventory scanning is not a claim that every historical deployment statement was revalidated.

Older README/status/integration files contain conflicting counts, localhost/LAN examples and June health claims. The June Earth Simulator handoff explicitly promotes live iNaturalist fallback and fire-and-forget persistence; the current owner's canonical-history requirement supersedes that behavior for historical reads. April data zones remain useful separation intent. BLOCKS' June scheduling scope explicitly uses JSON/Supabase without MINDEX. The May library document records a bind mount and failed NAS access; it is historical, not current NAS health. The June SINE completion document describes a particular verified CPU artifact and modest validation result, not all-modality readiness. The April compliance report concerns a dated Supabase hardening effort and open work; it is not proof of current MINDEX/WorldView/defense certification.

Machine-readable supporting evidence is retained outside the repo in the task's `work/mindex-canonical-readiness/`: `document-inventory.json`, `aws-readonly-current.json`, before snapshots and the final repair receipt. The repository-relative inventory below allows later reviewers to locate each original document without importing workstation artifacts.


| Document | Lines | Git blob |
|---|---:|---|
| `docs/ACOUSTIC_CLASSIFIER_SCOPE_MAY27_2026.md` | 80 | `5ab4f4015198851baa3d95500793ae6214861198` |
| `docs/AI_MODELS.md` | 425 | `33d0951bbd1e194e076b2ae11339722161cce749` |
| `docs/ALL_LIFE_ETL_MAY02_2026.md` | 22 | `b02cfaaa3837217d57cef71037925597b79908ef` |
| `docs/BLOCKS_SCHEDULER_MINDEX_SCOPE_JUN11_2026.md` | 35 | `5356db841363052aee8f9a2e92fc4edcc07fb7ff` |
| `docs/BOUNDING_BOX_CONTRACT_BATCH9.md` | 36 | `1a8737af0d725d7ce363f140efbe373baa28b1e3` |
| `docs/CODEX_HANDOFF_EARTH_SIMULATOR_WEBSITE_APPS_JUN10_2026.md` | 296 | `f5692041bc0ab9030b9cbdf42e5425c4050854d3` |
| `docs/ETL_SCHEDULER_ENABLEMENT_FEB10_2026.md` | 121 | `554f925953898c724bf3fc9f3c7b6570a9ac6c32` |
| `docs/ETL_SYNC_GUIDE.md` | 230 | `debf69f382d9770506ee8a2d6cb0bdbdb809b8ad` |
| `docs/FOR_NATUREOS_TEAM.md` | 90 | `a6cbaedf8df413945d2d2dc0e6450e3ca574365c` |
| `docs/HQ_MEDIA_IMPLEMENTATION_COMPLETE.md` | 303 | `07daddc8c66613fe8ba2fb35270005fe4c681871` |
| `docs/HQ_MEDIA_SYSTEM_MAP.md` | 264 | `e55df5fd0da54554eb7b41eb4e9d72e8e14d2182` |
| `docs/INAT_COMMITTED_CHECKPOINTS_BATCH8.md` | 52 | `69859a37a1dbc9e390471f67db49a7f65d38b6a7` |
| `docs/INTEGRATION_COMPLETE.md` | 132 | `953491e298c8347a9172199fd0dea5e48a8e0b12` |
| `docs/INTEGRATION_SUMMARY.md` | 190 | `58cf4027fd84862b2b6c89a36fbb04b0c31662b2` |
| `docs/MINDEX_AUDIT_REPAIRS_SEP29_2026.md` | 58 | `fbaaa708e6f83190ba9a0bade07b5010054d72eb` |
| `docs/MINDEX_ETL_FULL_AUDIT_JUN10_2026.md` | 342 | `0251c995ecf769d226db06901c5d096083ba2676` |
| `docs/MINDEX_ETL_REMEDIATION_COMPLETE_JUN10_2026.md` | 123 | `b9a591d09292d571a35f9cd479172da8b71a057c` |
| `docs/MINDEX_LIBRARY_NAS_MOUNT_MAY27_2026.md` | 129 | `4d76b7f5f79c37703a001e69325cc6a259cee7c4` |
| `docs/MINDEX_MISSING_TAXA_DIAGNOSIS_JUN10_2026.md` | 233 | `9a83c630c4a1ec4ed2504ce464256d2789e8a02a` |
| `docs/MINDEX_MYCA_MYCODAO_DATA_ZONES_APR14_2026.md` | 53 | `d9fa68759e4c18505385f295cf4ed1a3f03c2695` |
| `docs/MINDEX_PROXMOX_MIGRATION_GUIDE.md` | 453 | `c34a10a78b74128955c1aeb8c1da1f778944d0d1` |
| `docs/MINDEX_SINE_ACOUSTIC_VM_DEPLOY_COMPLETE_JUN04_2026.md` | 122 | `421d2304877bd9bac79b380606fc22f62aee664a` |
| `docs/MINDEX_SYSTEM_STATUS.md` | 274 | `bd13bfcb39b5fb9cac5f31b645a7f0a220582c8f` |
| `docs/MINDEX_TAXA_REMEDIATION_COMPLETE_JUN10_2026.md` | 92 | `6cf8f6932b1d369ba97d44155414194aee82dcb8` |
| `docs/MINDEX_VM_NAS_EFFICIENCY_MAY27_2026.md` | 44 | `3e3748751905aa80600f66d4ae998f5cf9fbf780` |
| `docs/MINDEX_WAVE_ANNOTATIONS_BACKEND_COMPLETE_JUN04_2026.md` | 54 | `2563d7cde55f6124b16c2f05530a09fe747ec047` |
| `docs/MYCOBRAIN_FULL_REPORT.md` | 0 | `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` |
| `docs/MYCOBRAIN_INTEGRATION.md` | 751 | `772e2d0e1ef5672ec510f26bd68e5c2d5f8ae16d` |
| `docs/NATUREOS_API_INTEGRATION.md` | 156 | `51463083fcc7e6430fe58832cf849b434e534457` |
| `docs/NATUREOS_INTEGRATION_GUIDE.md` | 777 | `4fb2b22da5df2e98e236f40d6bec04ea4d99ace6` |
| `docs/NATUREOS_INTEGRATION_QUICKSTART.md` | 148 | `e1c2d1ceb3e9dbb330d0a5ac76d44ef84bf71a36` |
| `docs/NLM_AUDIO_INGEST_STARTED_MAY27_2026.md` | 33 | `89e45c2ed24aa08b05a6f372d208c2b151133201` |
| `docs/NLM_LIBRARY_CATALOG_LABELS_MAY27_2026.md` | 87 | `f3ab92a2e8e88a5117d7515e106a0b15a1fbcedb` |
| `docs/NLM_TRAINING_DATA_SOURCES.md` | 1336 | `c022edd041098ff9e49b40d9b60d1c9103414ff7` |
| `docs/NOTION_MYCOBRAIN_KB_TEMPLATE.md` | 70 | `c0df706f814feb2a4834a38c0e2323707d8970ea` |
| `docs/OBSERVATION_SCHEMA_COMPATIBILITY_FEB11_2026.md` | 22 | `2c84bd132474209f13bb1e789818ba515a171cc1` |
| `docs/README_NATUREOS_INTEGRATION.md` | 87 | `93c5f2cdff55ffe8b9b6625854c3f2083ae77687` |
| `docs/SINE_ACOUSTIC_BACKEND_MAY27_2026.md` | 92 | `15127e6fd05afe3703e3ca76840ff8673acdf597` |
| `docs/SINE_MINDEX_NAS_STACK_COMPLETE_MAY27_2026.md` | 48 | `261fbba464d2f245cad229ce1e66de54aa44f331` |
| `docs/SINE_REAL_AI_BACKEND_COMPLETE_JUN11_2026.md` | 76 | `2cb6906ce1c6e9f83a008946e3b206442987ff2a` |
| `docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md` | 1280 | `9ea68f6e16b1ce124c758570c160120b216900f3` |
| `docs/SUPABASE_MIGRATIONS_AND_CI_APR15_2026.md` | 42 | `f76b47ece5581794dffc0f53a8c6292a30c319d0` |
| `docs/mycosoft_cmmc_nist_compliance_report.md` | 225 | `6bc8b1b6a4216d86a3c003a0f09e15700faa354c` |
| `README.md` | 485 | `45286f978024e37aa86cd3c0ff68d505c8f9883f` |
| `mindex_api/README.md` | 83 | `a3a33a946a11f739a702bf745d0cc3daf38d8b86` |
| `mindex_api/TASKS.md` | 263 | `7a9d84a8fdec9d69d0aa90f62521c9b29bbdec20` |
