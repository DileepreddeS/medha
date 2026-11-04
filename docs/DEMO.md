# Medha demo pack (read me in the editor)

Live pipeline, verified end to end: 5 presets run in **60 s** from an empty
library (timed 2026-11-04, DEMO_MODE cache replay + real Modal Sandbox runs).

## 1. Start commands

One server serves API + UI (the React build in `web/dist/`). No separate UI
process needed. Prereqs: `.env` with keys (see `.env.example`), `uv sync`,
`npm run build --prefix web` (already built in this workspace).

### DEMO_MODE (cached 60 s replay — use this on camera)

```powershell
# from the repo root, with a FRESH demo dir (routes depend on library state)
Remove-Item -Recurse -Force .demo-live -ErrorAction SilentlyContinue
$env:DEMO_MODE = '1'
$env:LLM_AUTHOR_PROVIDER = 'meta'   # cache was recorded on Spark; keys are provider-free
$env:MEDHA_DB_PATH = './.demo-live/medha.db'
$env:MEDHA_SKILLS_DIR = './.demo-live/skills'
$env:MEDHA_VERIFY_DIR = './.demo-live/verify'
$env:MEDHA_TOKEN = 'demo-day-token'
uv run uvicorn medha.api.app:create_app --factory --port 8000
```

Open **http://127.0.0.1:8000** in a browser. Paste `demo-day-token` into the
token box (top right). Stay on the **Demo** tab.

Network stays on: skills still execute in Modal Sandboxes; only the LLM calls
replay from `fixtures/llm_cache/` (29 files, ~110 KB — gitignored, ships with
this workspace, not with a fresh clone).

### Live mode (real LLM calls — NOT for camera)

```powershell
# from the repo root
Remove-Item Env:DEMO_MODE -ErrorAction SilentlyContinue
$env:LLM_AUTHOR_PROVIDER = 'meta'   # or 'glimmer' (the default); judge is always Spark
$env:MEDHA_TOKEN = 'demo-day-token'
uv run uvicorn medha.api.app:create_app --factory --port 8000
```

Same URL. Uses `./medha_data/` unless you override `MEDHA_DB_PATH` etc.
Expect minutes per task on Spark, 15–50 min per task on Glimmer, plus real
Modal cold-starts. Dev-UI alternative: `cd web; pnpm i; pnpm dev` (proxies
/api to :8000) — unnecessary, dist is built and served.

## 2. Click-by-click 60 s script

On the **Demo** tab (token pasted):

1. Click **1 · Learn a skill**, then **Run task** (~16 s). The pipeline steps
   Route → Learn → Test → Judge, then the approval modal pops. Click
   **Approve & run** → Execute goes green, `report.md` appears for download.
2. Click **2 · Reuse it**, then **Run task** (~4 s). No approval, no
   authoring: the **⚡ Reused skill** badge pops — the same library skill
   answers a new CSV with 0 author calls.
3. Click **3 · Learn stock alerts**, then **Run task** (~12 s). A second
   skill is learned; approve when asked.
4. Click the **Library** tab: two skill cards with trust badges, success
   bars, version counts. Click the sales card — the **diff** tab is empty
   (v1 only, so far). Click **contract** and **code** to show them.
5. Back on **Demo**, click **4 · Survive drift**, then **Run task** (~16 s).
   Headers drifted upstream; the probe misses, the repairer revises, v2
   registers — no approval needed (already-trusted family).
6. Click **Library** again: the sales card flashes **repaired — replay
   passed**. Open it → **diff v1→v2** shows exactly what the repair changed.
7. Back on **Demo**, click **5 · Compose a pack**, then **Run task** (~12 s).
   The router admits partial coverage from both skills and the composer fuses
   them; approve when asked → `weekly_ops_pack.md`.
8. Click **Evidence**: headline numbers count up; pass-rate bars A/B/C·Spark/
   C·Glimmer; tokens-per-task drops on every reuse; latency; library growth
   climbs; repair outcomes 3/3.

Total runtime ≈ 60 s plus your clicks and narration.

## 3. Final numbers

Mini MedhaBench, 12 tasks, 6 families. A = single-shot baseline,
B = single-shot + markers, C = full Medha. Judge always Spark 1.3.

| config | pass | note |
|---|---|---|
| A single-shot | 11/12 (92%) | strong once, pays full price every time |
| B + markers | 9/12 (75%) | markers alone don't help |
| C Medha/Spark | 10/12 (83%) | misses learn once, reuses forever |
| C Medha/Glimmer | 7/12 (58%) | all 5 fails were Modal 503 cold-starts |

Reuse economics (each family 3x; repeats = 2nd/3rd sight):

| repeats (12) | tokens | latency | pass |
|---|---|---|---|
| A re-solves | 14,965 | 119 s | 11/12 |
| C HIT reuse (10/12) | 5,704 | 36 s | 10/10 |

2.6x fewer tokens, 3.3x faster, 100% vs 92% pass. The 2 non-reused repeats
learned fresh (owner_v2/v3) after owner_v1 failed on a forced-HIT.

Consistency (same task 5x): A identical-output rate 0.2 (5 distinct
outputs); C rate 1.0 (5 identical outputs via the verified skill).

Repair (renamed column, date format, currency symbols): 3/3 both; C at
6.6k tokens / 48 s per drift vs A 15.4k / 125 s (2.3x / 2.6x).

Glimmer note: 3/12 → 7/12 after the D25/D26 gate hardening, zero gate
escapes; 3 passes via registered skills (incl. a genuine HIT reuse), 4 via
the unverified single-shot fallback. Regenerate everything:
`uv run python -m bench.final_report`.

## 4. README status

[README.md](../README.md) has: Quickstart, Results (tables above + 5 charts
in `docs/charts/`), Architecture (pipeline diagram + module map + firewall),
and Security model (short). Full build spec: `docs/MEDHA_SPEC.md`; pinned
decisions: `docs/DECISIONS.md`.

## 5. Things to avoid on camera

- Do one dry run on the camera machine first: no live browser was available
  here, so the render/console check is `tsc` + a 25-check API contract sweep
  (green), not an observed page load.
- Each Run shows "Working…" ~12–16 s (real sandbox execution) *before* the
  pipeline animates — normal, narrate over it; the stages then play back on
  a beat.
- Never run live on Glimmer on camera (slow, 503-prone). Never rename
  presets or fixtures (cache keys hash the exact prompt text).
- If routes look wrong (HIT on preset 1, etc.), the DB isn't fresh: stop,
  delete `.demo-live/`, start again.
- Evidence intentionally shows C·Glimmer 7/12 beside C·Spark 10/12 —
  labeled bars; frame as the open-weights author-vs-gate story.
- Port busy: 8001/8002 were scratch servers (stopped); pick any free port.
- Evidence reads `bench/results/*/results.json` — present here, gitignored
  elsewhere. Same for the LLM cache: the demo needs this workspace.
