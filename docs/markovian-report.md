# Markovian in Pricise: Phase 1A report

Status: in progress. Done: the wishlist watch (step i) and the vendored engine with conformance (step ii). Still to come:
the sidecar bridge and Python client (step iii), and the measured before/after run (step iv).

## Conformance result

Engine: `backend/engines/world_model_ts/markovian/src/` = Meterless `engines/markovian/reference/src/` at commit
`1742dec` (2026-10-19), 9 files, byte-identical, Apache-2.0. `markovian/index.ts` only re-exports it.
Run on 2026-10-25 with Node v24.11.1, Windows 11.

```
Markovian conformance: implementation = <repo>/backend/engines/world_model_ts/markovian/index.ts

  PASS eff-001 efficiency model reproduces the documented worked example: N=20 -> 86% (within 0.5%)
  PASS snap-001 run records carry a mandatory chunkConfig snapshot
  PASS snap-002 historical efficiency is computed against the run's own snapshot, not current config
  PASS ovl-001 overlap contract: implement [OVERLAP] or reject non-zero; accept-and-ignore fails
  PASS lab-001 token numbers are labeled: runs carry estimated or measured
  PASS ref-001 reflection failure does not fail the run
  PASS mrk-001 markers are cleaned from display content and drive completion
  PASS cfg-001 config bounds reject out-of-range values with a typed error, never silent truncation

  8/8 checks passed
```

Exit code: 0. Conformance proves the engine follows the spec's exact contracts (formulas, config bounds, markers,
labels). It does not prove anything about the wishlist watch: the watch's own tests do that.

### Run it yourself (Windows)

The suite prints only to the terminal. It needs the Meterless clone for `runner.ts` and the sidecar's own `tsx` (no
install in the clone needed). Use forward slashes and an absolute path in `MARKOVIAN_IMPL`.

Git Bash:

```bash
cd "<meterless-clone>/engines/markovian/conformance"
MARKOVIAN_IMPL="<repo>/backend/engines/world_model_ts/markovian/index.ts" \
  npx --prefix "<repo>/backend/engines/world_model_ts" tsx runner.ts
```

PowerShell:

```powershell
cd "<meterless-clone>\engines\markovian\conformance"
$env:MARKOVIAN_IMPL = "<repo>/backend/engines/world_model_ts/markovian/index.ts"
npx --prefix "<repo>\backend\engines\world_model_ts" tsx runner.ts
```
