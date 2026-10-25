# H-MEM in Pricise: Phase 1A report

Persistent shopper memory (preferences, card or offer names, trusted or blocked stores) on the Meterless H-MEM
reference engine, inside the existing engine sidecar. Phase 1A = engine, sidecar routes, Python client, tests.
Nothing in the product reads or writes memory yet: that is Phase 1B.

## Read this first

- **The embedder is a deterministic hash** (FNV-1a per token into 64 dimensions, from the reference). Retrieval is
  keyword-style matching, not semantic search: "refurbished" finds "Refurbished is OK", but "used is fine" does not.
- **Conformance proves spec compliance, not product quality.** The 13 checks cover the spec's exact contracts
  (formulas, deltas, gates, never-happens rules). They say nothing about how useful the notes are to a shopper, and
  nothing about privacy. The privacy rules are proved by Pricise's own tests (below).
- Only notes the user typed are stored (origin `user_typed`). User-approved notes and chat "remember ..." proposals
  come with Scout Phase 1B approvals.

## Conformance result

Engine: `backend/engines/world_model_ts/hmem/src/` = Meterless `engines/hmem/reference/src/` at commit `1742dec`
(2026-10-19), 10 files, byte-identical. Run on 2026-10-25 with Node v24.11.1, Windows 11.

```
H-MEM conformance: implementation = <repo>/backend/engines/world_model_ts/hmem/index.ts

  record-validation
    PASS rv-001 a record with no source is rejected by construction
    PASS rv-002 a record with a source is accepted

  feedback-deltas
    PASS fd-001 helpful adds exactly +0.05 confidence and +1 accessCount
    PASS fd-002 not_helpful subtracts exactly 0.03
    PASS fd-003 wrong subtracts exactly 0.20 and adds the review tag
    PASS fd-004 confidence clamps at 0 (wrong at 0.1)
    PASS fd-005 confidence clamps at 1 (helpful at 0.98)

  ranking-order
    PASS ro-001 superseded penalty dominates: a superseded record ranks below a fresh one with identical content relevance
    PASS ro-002 confidence multiplies the whole score: identical records rank by confidence

  sleep-guardrails
    PASS sg-001 old low-access plain record archives; dream-derived record with identical age does not

  conflict-gate
    PASS cg-001 auto-resolve marks the loser supersededBy and deletes nothing

  ledger-audit
    PASS la-001 create then feedback reconstructs: actions in order, previous and new confidence present

  dream-boundary
    PASS db-001 dream proposals never materialize before approval; approval creates a long_term record with lineage

  13/13 checks passed
```

Exit code: 0.

### Run it yourself (Windows)

The suite only prints to the terminal; it saves no file. It needs the Meterless clone for `runner.ts` and its vectors,
and the sidecar's own `tsx` (no install in the clone needed). Use forward slashes and an absolute path in `HMEM_IMPL`.

Git Bash:

```bash
cd "<meterless-clone>/engines/hmem/conformance"
HMEM_IMPL="<repo>/backend/engines/world_model_ts/hmem/index.ts" \
  npx --prefix "<repo>/backend/engines/world_model_ts" tsx runner.ts
```

PowerShell:

```powershell
cd "<meterless-clone>\engines\hmem\conformance"
$env:HMEM_IMPL = "<repo>/backend/engines/world_model_ts/hmem/index.ts"
npx --prefix "<repo>\backend\engines\world_model_ts" tsx runner.ts
```

## How it is built

| Piece | File | What it does |
|---|---|---|
| Engine | `world_model_ts/hmem/src/*.ts` | Meterless reference, unchanged (Apache-2.0, listed in `NOTICE`) |
| Pricise layer | `world_model_ts/hmem/pricise.ts` | Note rules, own persistence, content-free audit, rebuild after delete |
| Conformance target | `world_model_ts/hmem/index.ts` | Re-exports `HMEM` and the Pricise layer |
| Routes | `world_model_ts/server.ts` | `/hmem/notes`, `/hmem/notes/{id}`, `/hmem/forget`, `/hmem/query` |
| Python client | `backend/engines/hmem.py` | Same rules + `.env` check, outbox fallback, one warning per outage |
| Data | `<repo>/.pricise-data/memory/` | `state.json`, `audit.jsonl` (sidecar); `outbox.jsonl`, `snapshot.json` (client). Git-ignored. `HMEM_DATA_DIR` moves it |

Not exposed: sleep, dreaming and mining (sleep can archive a note the user did not delete).

### Why Pricise does its own persistence

The reference's `persistDir` writes `ledger.jsonl` with a full snapshot of every record, content included, and the
ledger is append-only; sleep backups also hold content. With it, a deleted note would stay on disk. So the engine runs
in memory only, and `pricise.ts`:

- writes `state.json` (the notes only) atomically: temp file, fsync, rename;
- writes `audit.jsonl` with time, action, id and kind, never the text (no hash either: a short note can be guessed back
  from a hash);
- after a delete or "forget everything", builds a new engine from the remaining notes, so the old ledger and backups
  (which hold the text) are gone from memory too.

Spec feedback for Meterless: section 9.1 (append-only ledger with `previousState`) conflicts with a user's right to
delete. A content-free ledger option would help privacy-first products.

## Privacy rules and where they are enforced

| Rule | Enforcement | Proved by |
|---|---|---|
| Only text the user typed | `origin` must be `user_typed` (client and sidecar). No code path writes page text or model output | `test_only_user_typed_notes_are_accepted`, `test_the_sidecar_enforces_the_same_rules` |
| No memory poisoning | The above; Scout guard on the note text (pass only); every sidecar route refuses requests with an `Origin` header (403) and POST/PUT that are not `application/json` (415), so a web page cannot send a blind cross-site POST to 127.0.0.1 | `test_hidden_instruction_page_cannot_write_memory`, `test_hmem_routes_refuse_browser_and_non_json_requests`, `test_every_route_refuses_browser_and_non_json_requests` |
| No secrets or card numbers | Luhn on 13-19 digits, any 12+ digit run, CVV, `sk-`/`ghp_`/`AKIA`/`xox`, JWT, Bearer, `password:`, private keys, high-entropy words, any `.env` value or secret-named env value (client), 200 characters and one line max. The reason never echoes the text | `test_policy_rejects_secrets_cards_prompts_and_hidden_instructions` (17 cases) |
| List and delete for real | `GET`, `DELETE`, `forget`; text gone from `state.json`, audit, engine, outbox and snapshot; still gone after a restart | `test_delete_and_forget_really_remove_the_text`, `test_delete_and_forget_remove_the_text_from_disk` |
| Survives a restart | `state.json` reloaded at start | `test_notes_survive_a_restart` |
| Sidecar down | Outbox (fsynced), replay in order by id, local list and keyword query, ONE warning | `test_sidecar_down_keeps_every_note_with_one_warning`, `test_outbox_replays_after_an_outage_without_duplicates` |
| Local only | Loopback sidecar, git-ignored folder | `test_memory_folder_is_git_ignored` |

## Test results (2026-10-25)

| Run | Result |
|---|---|
| `backend/tests/test_hmem.py` (no sidecar) | 32 passed |
| `test_hmem_sidecar.py` + `test_scout_sidecar.py` + `test_world_model_dedupe.py` (real sidecars on free ports) | 34 passed |
| Full suite, nothing on 127.0.0.1:8788 | 326 passed, 2 skipped |
| Full suite, new sidecar running on 127.0.0.1:8788 | 326 passed, 2 skipped |

The 2 skips are the hidden-browser Deal tests (Playwright Chromium not installed for pytest on this machine).
`world_model.py` and `scout.py` still work against the real sidecar with the new transport rule (both already send
`Content-Type: application/json` and no `Origin`).

## Notes for other owners (Phase 1B, not started)

- `stub_server.py`: memory routes for the panel will come as a router (`backend/memory_api.py`) to include. Its
  `CORSMiddleware(allow_origins=["*"])` lets any web page call `:8787`; the memory router will check the extension's
  Origin, but the wildcard should be narrowed.
- `extension/` side panel: "Memory: N notes" with view, delete and "forget everything".
- `agents/chat.py`: read the top notes into the prompt inside `meterless.untrusted()`; never write memory from a reply.
- Price Agent: use "refurbished is OK" and card offers when ranking.
- `contracts.py`: no change needed.
