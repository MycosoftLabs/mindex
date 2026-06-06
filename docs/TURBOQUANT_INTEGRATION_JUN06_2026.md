# TurboQuant in MINDEX Vector Search — Design

**Date:** June 06, 2026
**Status:** Design / RFC (research-first)
**Scope:** MINDEX GPU vector search (cuVS) + pgvector source of truth
**Companion:** `mycosoft-mas/docs/TURBOQUANT_NEMOTRON_INTEGRATION_JUN06_2026.md`
(canonical algorithm + validated reference codec at
`mycosoft-mas/mycosoft_mas/memory/turboquant.py`)

---

## 1. Why
TurboQuant (Google Research) is a **data-oblivious** two-stage vector quantizer —
**PolarQuant** (random rotation → radius + fixed-grid direction, zero scale overhead)
plus **QJL** (1-bit JL sign sketch with an unbiased inner-product estimator). It hits
~3 bits/dim with near-zero accuracy loss and **no codebook training**, so a single codec
serves every MINDEX index regardless of dimension or distribution. See the MAS doc for
the full algorithm and reproduced benchmarks (10.4× vs fp32, 5.2× vs fp16, 0.98 recon
cosine, ≥0.8 two-stage recall).

For MINDEX this means **bigger indexes fit in GPU VRAM** and faster load, while pgvector
stays the full-precision source of truth (chain-of-custody / MICA proofs intact).

## 2. Where it plugs in
From the codebase map (`mindex_api/gpu/cuvs_index.py`):

| Concern | Location | Today |
|---------|----------|-------|
| Indexes | `cuvs_index.py:63-97` | `fci_signals` 768-d, `nlm_nature` 16-d, `image_similarity` 512-d, COSINE |
| Load from pgvector | `_load_vectors_from_db()` ~L168 | full fp32 into memory |
| Build GPU index | `_build_cuvs_index()` ~L181 | cuVS IVF-PQ (pq_dim 64, pq_bits 8 → ~12.5×), IVF-Flat, CAGRA |
| Streaming upsert | `add_vectors()` L423-449 | appends fp32 |
| Search | `search()` L283-313 | cuVS → NumPy → pgvector fallback chain |
| API | `gpu/router.py:120-176` `POST /gpu/search` | returns backend, latency, root_hash |

**Existing compression:** cuVS **IVF-PQ already** gives ~12.5× on `fci_signals` but is
**data-dependent** (trained codebooks per index) and only on the GPU path; the NumPy and
pgvector fallbacks carry full fp32.

## 3. Design
TurboQuant is **complementary**, not a replacement for cuVS:

1. **Compress the in-memory cache** (the fp32 array kept after `_load_vectors_from_db()`,
   ~L186). Replace it with `QuantizedVector` codes. The **NumPy brute-force fallback**
   (L357-388) then runs on TurboQuant: QJL estimate for a shortlist, PolarQuant
   reconstruction for re-rank — removing the fp32 memory blow-up on GPU-less hosts.
2. **Keep cuVS IVF-PQ/CAGRA** as the primary GPU ANN. TurboQuant becomes the
   **data-oblivious fallback + cold-start** path (no codebook training needed → instant
   index availability before cuVS finishes building).
3. **pgvector unchanged** — full precision, source of truth, final verification.

### Two-stage search (fallback path)
```
shortlist = argtop(QJL_estimate(query, codes), k=8·K)   # 1-bit, cheap
results   = argtop(PolarQuant_score(query, codes[shortlist]), K)  # 3-bit, accurate
```
This is exactly `TurboQuantCodec.rerank()` in the MAS reference module.

### cuVS interplay
For the GPU path, prefer cuVS-native ANN (CAGRA/IVF-PQ). TurboQuant's role on GPU is
**KV-cache-style memory reduction for very large indexes** and a **codebook-free** option
for new/low-volume indexes (e.g. `nlm_nature` 16-d where PQ codebooks are overkill).

## 4. Proposed module
Add `mindex_api/gpu/turboquant.py` — a thin port of the MAS reference codec
(numpy/cupy), so MINDEX has no cross-repo import dependency. Same algorithm, same seed
discipline (rotation/JL regenerated from seed; nothing stored). Wire into:
- `_load_vectors_from_db()` → encode on load (flagged `MINDEX_TURBOQUANT=1`).
- NumPy fallback in `search()` → two-stage rerank.
- `add_vectors()` → encode on append.
- `gpu/router.py` response → add `compression_ratio` / `backend="turboquant"` telemetry.

## 5. Rollout
| Phase | Action | Gate |
|-------|--------|------|
| 0 | This design doc | review |
| 1 | Port codec to `gpu/turboquant.py` + unit tests | recall parity vs NumPy fp32 on a labeled set |
| 2 | Flagged TurboQuant cache for NumPy fallback on `image_similarity` | memory ↓, recall ≥ fp32 − 2% |
| 3 | Cold-start TurboQuant for new indexes before cuVS build | availability ↑ |
| 4 | Large-index VRAM mode alongside cuVS | VRAM ↓ |

All phases env-flag gated; pgvector remains untouched and authoritative.

## 6. Constraints (MINDEX)
- **Never delete vectors or tables** — TurboQuant codes live *alongside* pgvector, never
  replace it.
- **Proofs intact:** `root_hash` / MICA verification continues to run over the
  full-precision pgvector source, not the quantized cache.
- **Dimension-agnostic:** works as-is for 768/512/16-d indexes; no per-index tuning.

## 7. References
- Google Research — *TurboQuant: Redefining AI efficiency with extreme compression.*
- QJL: Zandieh et al., *1-bit Quantized JL for KV-cache.*
- MAS canonical codec + benchmarks: `mycosoft-mas/mycosoft_mas/memory/turboquant.py`,
  `mycosoft-mas/docs/TURBOQUANT_NEMOTRON_INTEGRATION_JUN06_2026.md`.
