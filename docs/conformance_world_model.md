# World Model conformance

Engine: `backend/engines/world_model_ts/src/` = Meterless `engines/world-model/reference/src/` at commit `1742dec`
(2026-10-19), 6 files, byte-identical (SHA-256 compared on 2026-10-26), Apache-2.0. `server.ts` imports it unchanged.
Run on 2026-10-26 with Node v24.11.1, Windows 11. Exit code: 0.

```
World Model conformance: implementation = <repo>/backend/engines/world_model_ts/src/index.ts

  PASS sid-001 stable ids: diacritics and NBSP normalize identically (NFKD + strip marks)
  PASS sid-002 stable ids: distinct names stay distinct (no over-normalization)
  PASS band-001 merge bands at the boundaries: 0.85 auto, 0.84 review, 0.82 review, 0.81 new
  PASS prov-001 writes without provenance are rejected
  PASS idem-001 ingest idempotency: same batch twice produces an empty diff
  PASS idem-002 the idempotency check can fail: a mutated batch changes the shape
  PASS bitemp-001 bitemporal edges: a superseding edge closes the old one; point-in-time queries respect validity
  PASS audit-001 every mutation writes an audit entry in order

  8/8 checks passed
```

## What this proves, and what it does not

- It proves the engine follows the spec's exact contracts: ids, merge bands, provenance, idempotency, bitemporal edges
  and the audit log. Since the engine is the unchanged reference, this is expected.
- It does not test Pricise's use of it: how findings are keyed (`world_model.finding_key`), the sidecar routes, or the
  fallback when the sidecar is down. Those are tested in `backend/tests/test_world_model_dedupe.py` and
  `backend/tests/test_scout_sidecar.py`.
- Absolute retrieval scores depend on the embedder and are not checked by the suite.

## Run it yourself

The suite prints only to the terminal; this file is its saved output. It needs the Meterless clone for `runner.ts` and
the sidecar's own `tsx` (no install in the clone needed). Use forward slashes and an absolute path.

Git Bash:

```bash
cd "<meterless-clone>/engines/world-model/conformance"
WORLD_MODEL_IMPL="<repo>/backend/engines/world_model_ts/src/index.ts" \
  npx --prefix "<repo>/backend/engines/world_model_ts" tsx runner.ts
```

PowerShell:

```powershell
cd "<meterless-clone>\engines\world-model\conformance"
$env:WORLD_MODEL_IMPL = "<repo>/backend/engines/world_model_ts/src/index.ts"
npx --prefix "<repo>\backend\engines\world_model_ts" tsx runner.ts
```
