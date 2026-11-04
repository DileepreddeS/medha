# Medha — Build Spec (v1, hackathon edition)

> **Medha** (Sanskrit: retentive intelligence). A self-improving agent that turns recurring tasks into **verified, versioned, executable skills**, then reuses, repairs and composes them, so it gets cheaper, faster and more reliable with use.
>
> Built on Meta's stack: **Muse Glimmer** (open weights, self-hosted with vLLM on Modal), **Muse Spark 1.3** (Meta Model API judge), **Modal Sandboxes**. Built during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026).

---

## 0. Thesis (why this matters, with evidence)

- **The problem.** Agents start every task from zero. They regenerate the same code, repeat the same mistakes, and cost the same on day 100 as on day 1.
- **The obvious fix doesn't work as-is.** SkillsBench (Li et al., 2026) tested 84 tasks across 7 agent/model setups. Human-curated skills raised pass rates by +16.2pp, but **skills the agent wrote for itself gave no benefit on average (−1.3pp)**. Other findings point the same way:
  - Self-improving loops **hack their own evaluators**. The Darwin Gödel Machine removed its own hallucination markers to raise its score.
  - Self-made skills are a **security surface**. Snyk's ToxicSkills audit found prompt injection or malware in 13.4% of public skills, with critical issues.
- **Medha's bet:** self-generated skills fail because they are **unverified, unversioned and never revisited**. Medha makes every skill:
  1. a **falsifiable contract**: explicit pre- and postconditions (following Skill-V, 2026)
  2. checked by an **isolated verifier**: the test writer never sees the code, and holdout tests are hidden from the author (CoEvoSkills, 2026; DGM evaluator separation)
  3. **revised only under non-regression on replayed history** (Skill-V)
  4. **executed as pure code in a locked-down sandbox**, with no LLM tokens spent on reuse
- **The demo proves it.** MedhaBench compares (A) plain agent, (B) unverified self-made skills (the SkillsBench failure mode), (C) Medha cold, and (D) Medha warm.

**Who benefits (the Impact criterion):** people who repeat digital chores but don't code: small business owners, office and ops staff, students. **Demo persona:** Priya runs a café. Every week she builds a sales report from a POS CSV export, extracts totals from supplier invoice PDFs, and writes a summary. Medha learns each chore once, then does it in seconds forever, and repairs itself when the POS export format changes.

**Rubric map:**

| Criterion | Weight | How Medha addresses it |
|---|---|---|
| Impact | 30% | The chores user story, with measured time and cost savings |
| Use of Meta tooling | 25% | Muse Glimmer as the brain (open-weights, self-hosted on Modal), Muse Spark 1.3 as a cross-model judge, Muse Code as the builder |
| Technical depth | 20% | Contracts, verifier isolation, replay non-regression, Beta-posterior routing, sandbox security |
| Demo | 25% | Live learn → reuse → repair → compose, plus the MedhaBench chart |

**Bonus targets:** Best Agentic Use-Case, Best Build on Meta Open Weights (Muse Glimmer self-hosted on Modal), Most Viral (X post), Community Vote.

---

## 1. Scope

**In scope**
- Web chat interface with task input and file upload (CSV, XLSX, PDF, TXT, JSON).
- The full loop: route → learn → verify → execute → reward → repair → compose.
- Skill library: versioned, with trust levels, rollback, lineage.
- User corrections that update skills. Lessons log.
- Approval gate for capabilities beyond local file processing.
- MedhaBench with 4 configurations, a repair suite and a composition suite, plus generated charts.
- Deployment on Modal, with a public GitHub repo.

**Out of scope** (mention these as future work only)
- Fine-tuning or RL on traces.
- Sharing skills across users. **No sharing of any kind.**
- Email, payments, deleting user data, or browser automation.
- Mobile app, glasses, Meta AI Connectors.
- Multi-tenant authentication. The demo uses a single bearer token.

---

## 2. Stack and models

| Layer | Choice |
|---|---|
| Language | Python 3.12, `uv`, Pydantic v2, FastAPI, SSE |
| Storage | SQLite (WAL) on a Modal Volume. Skill files on the same Volume. |
| Retrieval | BM25 over the **full skill text** (`rank-bm25`), plus an LLM reranker. No vector DB is needed at this scale. |
| Sandbox | Modal Sandboxes, one per verification or execution run |
| Frontend | React + Vite + TypeScript + Tailwind. Served as static files by FastAPI, or from Vercel. |
| Deploy | `modal deploy` (ASGI app), with secrets in Modal Secrets |
| Charts | matplotlib PNGs for the README, plus JSON for the UI (Recharts) |

**Model roles.** All IDs live in `.env`. Verify base URLs and model IDs from the docs and record them in `docs/DECISIONS.md`.

| Role | Default model | Reasoning effort | Notes |
|---|---|---|---|
| Planner / spec writer | Muse Glimmer (self-hosted vLLM on Modal) | medium | Turns the user task into a `TaskSpec` |
| Router reranker + applicability check | Muse Glimmer | low | Short prompts, cache-friendly |
| Skill author | Muse Glimmer | high | Writes `skill.py` + `SKILL.md` |
| Test writer (isolated) | Muse Glimmer, **separate call with no author context** | high | Sees only the contract and input samples |
| Judge (semantic postconditions) | **Muse Spark 1.3 via the Meta Model API** | medium | A different model than the author, to reduce self-preference bias |
| Repairer | Muse Glimmer | high | Gets the failure diff and the replay set |
| Escalation (after 2 failed authoring attempts) | Muse Spark 1.3 | high | Behind a config flag. Logged as an escalation. |
| **Reuse of a known skill** | **No LLM** | — | Pure code in the sandbox. This is where cost falls toward zero. |

- The Meta Model API accepts OpenAI Chat Completions and Anthropic Messages formats.
- Self-hosted Glimmer costs GPU-seconds (scale-to-zero, ~2 min idle); only judge/escalation calls bill Meta API tokens. Both are logged per call.
- Implement one `LLMClient` interface with two providers, so the provider is configured, not hardcoded.

**Budget guards (required):**
- Every LLM call records tokens and cost in the event log.
- `MAX_TOKENS_PER_TASK`, `DAILY_SPEND_CAP_USD`: refuse new LLM work above the cap and surface that in the UI.
- Put stable system prompts first and dynamic content last, to get prompt-cache pricing.
- `DEMO_MODE=1` replays recorded LLM responses from `fixtures/llm_cache/`, so the video and live demo are deterministic and free.

---

## 3. Architecture

```
                ┌──────────── React UI (chat · timeline · skills · evidence) ────────────┐
                │   "Built with Meta Muse Glimmer"          approvals modal               │
                └───────────────▲───────────────────────────────┬─────────────────────────┘
                        SSE events │                            │ REST
┌──────────────────────────────────┴────────────────────────────▼─────────────────────────┐
│ FastAPI on Modal  (auth token · rate limit · upload allowlist · size caps)              │
│                                                                                          │
│  Orchestrator (explicit state machine, every transition = event)                        │
│   ├─ Planner ── TaskSpec                                                                 │
│   ├─ Router ── BM25(full text) → LLM rerank → Applicability filter → Beta-score pick     │
│   ├─ Learner ─┬─ Author  (skill.py, SKILL.md, contract)                                 │
│   │           └─ TestWriter (ISOLATED: contract + samples only) → visible + holdout     │
│   ├─ Verification Gate  static → visible → holdout → outcome → judge → [replay]         │
│   ├─ Executor ── Modal Sandbox (no network, ro code, cpu/mem/time caps)                 │
│   ├─ Reward ── Beta(α,β) per skill version, cost/latency EMA                            │
│   ├─ Repairer ── drift detected → candidate vN+1 → gate + replay non-regression → swap  │
│   ├─ Composer ── DAG of existing skills → composite skill → gate                        │
│   ├─ Lessons ── structured, event-cited, scanned; never raw tool text                   │
│   └─ Approval ── capability policy × trust level                                        │
│                                                                                          │
│  Store: SQLite (events, skills, versions, runs, cases, lessons) + Volume (skill files)  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
        │ LLM calls                                   │ sandbox runs
        ▼                                             ▼
 Muse Glimmer (self-hosted vLLM)              Modal Sandboxes (prebuilt image)
 Muse Spark 1.3 (Meta Model API)
```

### 3.1 Skill package (on disk, immutable once registered)

```
skills/<skill_id>/v<N>/
  SKILL.md          # frontmatter: name, description, when_to_use, when_not_to_use; body: procedure
  contract.json     # the falsifiable contract (below)
  skill.py          # def run(inputs: dict, workdir: pathlib.Path) -> dict
  tests/visible/    # tests the author may see
  provenance.json   # source task/event ids, models used, parent version, composed_of, created_at
verify_store/<skill_id>/v<N>/holdout/   # hidden tests; outside the author's reach (firewall)
```

`contract.json` (Pydantic model `SkillContract`):

```json
{
  "skill_id": "weekly_sales_report",
  "version": 3,
  "intent": "Summarize a POS sales CSV into weekly revenue by category with top items",
  "inputs": {"sales_csv": {"type": "file", "formats": ["csv","xlsx"]},
             "week_start": {"type": "date", "optional": true}},
  "outputs": {"report_md": {"type": "file", "format": "md"},
              "totals": {"type": "object", "schema_ref": "totals.schema.json"}},
  "preconditions": ["input has a date column", "input has an amount-like numeric column",
                    "input has an item or category column"],
  "postconditions_deterministic": [
     "sum(totals.by_category.values()) == totals.grand_total ± 0.01",
     "every category in totals.by_category appears in report_md"],
  "postconditions_semantic": ["report states the date range covered"],
  "capabilities": ["read_input", "write_output"],
  "applicability_notes": "Not for invoices or bank statements",
  "trust": "verified"
}
```

- Deterministic postconditions are compiled to Python checks by the verify module, not by the author.
- Semantic postconditions go to the Judge.

### 3.2 Core loop (state machine)

```
RECEIVED → PLANNED(TaskSpec)
  → ROUTED
      ├─ HIT  → APPLICABILITY_OK → EXECUTE → OUTCOME_CHECK
      │                               ├─ pass → REWARD(+) → DONE
      │                               └─ fail → REWARD(−) → REPAIR → (re-execute | FALLBACK_LEARN)
      ├─ COMPOSE (≥2 skills cover the spec) → COMPOSITE_CANDIDATE → GATE → EXECUTE …
      └─ MISS → LEARN: AUTHOR ∥ TESTWRITE(isolated) → GATE → REGISTER(v1) → EXECUTE …
  → AWAIT_FEEDBACK (accept | correct | reject)
      └─ correct → CORRECTION → new version via REPAIR path (correction = new test case)
```

- Each transition is one event.
- At most 3 authoring attempts. After 2 failures, escalate to Muse Spark 1.3 if `ALLOW_ESCALATION=1`.
- After 3 failures, fall back to "solve once without saving a skill" and tell the user honestly.

### 3.3 Router (grounded in SkillRouter, 2026: the skill body matters more than name and description)

1. Run BM25 over a concatenation of `SKILL.md`, `contract.intent`, `preconditions`, and the `skill.py` docstrings and identifiers. Take the top 8.
2. **LLM rerank plus applicability check** in one structured call. Input: the `TaskSpec` plus each candidate's contract. Output, per candidate: `{relevance 0–1, applicable: yes|no|unsure, reason}`.
3. Drop `applicable=no`. Rank the rest by `relevance × E[p_success] − λ·norm_cost`.
4. If the best relevance is below `ROUTE_MIN=0.6`, it's a MISS. If 2 or more candidates jointly cover the spec's sub-goals, go to COMPOSE.
5. Before executing, run a **cheap precondition probe** in the sandbox: a generated check over the input file's schema. On failure, route to REPAIR or LEARN, never to a silent wrong answer.

### 3.4 Reward and selection

- For each skill version, keep `α` (successes) and `β` (failures). `E[p] = (α+1)/(α+β+2)`.
- **Per-run reward:**
  - 1.0 if deterministic checks and the Judge pass, and the user accepts (or gives no feedback within the session)
  - 0.5 if the user corrected the result
  - 0 if checks failed or the user rejected
  - Fractional rewards update α and β proportionally.
- Track a cost and latency EMA per version.
- **Trust levels** (following *Skills as Verifiable Artifacts*, 2026):
  - `candidate`: not yet through the gate. Never auto-runs.
  - `verified`: passed the gate.
  - `trusted`: 5 or more runs with `E[p] ≥ 0.9` and no failures in the last 5 runs.
  - Approval policy depends on trust level (§5.3).
- **Repair triggers:** two consecutive failures; `E[p] < 0.7` with at least 3 runs; a failed precondition probe on input that resembles past successful inputs (drift); or contract checks disagreeing with the user's outcome.

### 3.5 Verification gate (the heart of Medha)

Run in order and stop at the first failure. Every stage emits an event with evidence.

1. **Static checks** (host-side, before any execution):
   - The code parses (AST).
   - Imports are on the allowlist: `pandas, numpy, openpyxl, pdfplumber, pypdf, matplotlib, json, csv, re, math, statistics, datetime, pathlib, decimal, collections, itertools, typing, dataclasses`.
   - Banned: `os.system`, `subprocess`, `socket`, `ctypes`, `importlib`, `eval`, `exec`, `compile`, `__import__`, `open` outside `workdir`, `pickle`, `marshal`, environment variable access.
   - File size limits.
   - Secret-pattern scan.
   - `SKILL.md` scanned for prompt-injection patterns: instruction overrides, role impersonation, base64 or Unicode smuggling, URLs.
2. **Visible tests** run in the sandbox. The test writer produced them from the contract and input samples only.
3. **Holdout tests** run in the sandbox. Same test writer, separate call, stored in `verify_store`, **never shown to the author**.
   - Overfitting check: if the visible tests pass but holdout pass rate is below `HOLDOUT_MIN=0.8`, reject. This is the reward-hacking signal, following SpecBench.
4. **Outcome check** on the real user input: compiled deterministic postconditions.
5. **Judge** on semantic postconditions (Muse Spark 1.3, rubric output in JSON). The judge sees the contract and outputs, never the author's reasoning.
6. **Replay non-regression** (revisions only): the new version must pass all stored `cases` for this skill, meaning past successful inputs and accepted outputs, minus any cases the revision intentionally supersedes.

The **evaluator firewall** is enforced in code:
- `verify/` and `bench/checkers/` are mounted read-only into sandboxes.
- The author and repairer prompts are built by functions that cannot import `verify.holdout`.
- A unit test asserts this through an import-graph check.

### 3.6 Self-repair

- **Input:** the failing run's evidence: precondition probe output, stack trace, failed checks, and an input schema diff against the last successful case.
- The repairer proposes `vN+1`, which goes through the full gate, including replay.
- **On pass:** atomically switch the active version; the old version stays for rollback.
- **On fail:** keep `vN`, mark the run as failed honestly, and offer "learn fresh".
- **Auto-rollback:** if `vN+1`'s first 3 live runs underperform `vN`'s posterior, restore `vN`.

### 3.7 Composition

- The Composer turns a `TaskSpec` with sub-goals into a DAG over existing skills, with typed edges from output keys to input keys.
- It generates a thin composite `skill.py` that calls child `run()` functions in order. Child packages are copied read-only into the sandbox.
- The composite gets its own contract, made from the union of child preconditions plus end-to-end postconditions, and goes through the full gate.
- Lineage is recorded in `provenance.composed_of`, and the UI shows it as a small graph.

### 3.8 Corrections and lessons

- **A correction** ("show amounts in INR", "exclude refunds") becomes:
  1. a new test case (expected property), and
  2. a repair request.
  The resulting version must satisfy the new case plus the replay set.
- **Lessons** are short, structured records: `{scope: skill_id|global, trigger, guidance, evidence_event_ids}`.
  - Created only by the Lessons module from structured failure and correction events.
  - Never copied from raw file content or tool output.
  - Limited to 300 characters, scanned for injection, and visible and deletable in the UI.
  - Injected into author and repair prompts as *data*.
  - This defends against memory poisoning (the ClawHavoc pattern).

### 3.9 Event log and storage

Use an append-only `events` table as the single source of truth:

```
events(id, ts, task_id, type, actor, skill_id, version, payload_json,
       tokens_in, tokens_out, cost_usd, latency_ms)
```

Other tables:
- `skills(id, active_version, trust, created_at)`
- `versions(skill_id, version, path, alpha, beta, cost_ema, latency_ema, status)`
- `runs(id, task_id, skill_id, version, outcome, reward, …)`
- `cases(id, skill_id, input_ref, expected_props_json, source: accepted|correction|holdout)`
- `lessons(…)`
- `approvals(…)`

The UI timeline, the dashboard and MedhaBench all read from `events`.

---

## 4. Security model

| Threat | Control |
|---|---|
| Generated code escapes or abuses the host | Execute only in Modal Sandboxes: `block_network=True` by default, 1 CPU, 1 GiB, 60 s timeout, non-root, code mounted read-only, `/work` scratch only. Never execute generated code on the API container. |
| Data exfiltration | No network in sandboxes by default. No secrets in sandbox env. Network skills are out of scope for the MVP; any future network capability requires approval and a domain allowlist (verify Modal's egress options and record them in DECISIONS.md). |
| Prompt injection from user files | File content is wrapped as `<untrusted_data>` with an explicit system rule. Only schema previews (headers plus 20 rows, truncated) go to the LLM. Sandbox outputs are also treated as data. |
| Malicious or poisoned skill text | Static scan of `SKILL.md` and code. No skills are imported from outside Medha, and there is no sharing. |
| Memory poisoning | Lessons are structured, cite events, are length-capped and scanned, and can be deleted by the user (§3.8). |
| Reward hacking by the author | Evaluator firewall, isolated test writer, holdout gap check, cross-model judge, replay non-regression. |
| Open public endpoint abused as a free sandbox | Bearer token, per-IP rate limit, upload type allowlist, 10 MB cap, daily spend cap, sandbox concurrency cap. |
| Secret leakage | Secrets only in Modal Secrets or `.env`. A logging filter redacts key patterns. `.env` is in `.gitignore`. A pre-commit `gitleaks` hook is suggested to the user. |
| Supply chain | Sandbox image pinned (`uv.lock`, exact versions). No runtime `pip install` inside sandboxes. |

### 4.1 Approval gate

Capabilities: `read_input`, `write_output` (auto-approved), `network`, `side_effect` (both blocked in the MVP, with the UI explaining why).

The policy is a function of `trust × capability`:
- **`candidate`:** never runs on user data until it passes the gate.
- **`verified`:**
  - The user sees a one-click "Use new skill?" approval the **first time** a newly learned skill runs.
  - Optional per-run approval for composites.
- **`trusted`:** runs automatically.

---

## 5. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/tasks` | Multipart: `text` plus files → `{task_id}` |
| GET | `/api/tasks/{id}/events` | SSE stream of events |
| POST | `/api/tasks/{id}/feedback` | `{kind: accept\|correct\|reject, text?}` |
| POST | `/api/approvals/{id}` | `{decision: approve\|deny}` |
| GET | `/api/skills` | List with trust, E[p], cost, versions |
| GET | `/api/skills/{id}` | Contract, SKILL.md, versions, lineage, recent runs |
| POST | `/api/skills/{id}/rollback` | `{to_version}` |
| GET/DELETE | `/api/lessons[/{id}]` | View or delete lessons |
| GET | `/api/stats` | Spend, tokens, reuse rate, library growth |
| GET | `/api/bench` | Latest MedhaBench results JSON |
| GET | `/api/health` | Liveness check |

All routes except `/api/health` require `Authorization: Bearer $MEDHA_TOKEN`.

---

## 6. Frontend (React)

- **Chat:** task input, file drop, and a **live timeline** of steps: Routed → Learned / Reused / Repaired / Composed → Verified (visible, holdout, judge, replay ticks) → Executed. Each step shows tokens, $ and ms. The result appears with download links.
- **Skill card:** name, intent, trust badge, E[p] bar, runs, cost per run, a version history with code diffs, a "Rollback" button, and a lineage graph for composites.
- **Approval modal:** "Medha learned a new skill: *Weekly sales report*. It will read your CSV and write a report. Approve?"
- **Evidence tab:** MedhaBench charts (pass rate by config; cost per task over the task sequence; repair success) plus live stats.
- **Footer:** "Built with Meta Muse Glimmer".
- **Design:** clean and calm. Keep the dashboard secondary.

---

## 7. Repo layout

```
medha/
  config.py            # pydantic-settings, all env
  deploy.py            # modal app: ASGI, volume, secrets
  smoke.py             # P0 connectivity checks
  api/                 # routes, auth, rate limit, sse
  llm/client.py        # LLMClient + providers (glimmer, meta_api), cost accounting, demo cache
  llm/prompts/         # one file per role; stable prefix first
  core/                # orchestrator, planner, router, learner, author, composer, repair, reward, lessons
  verify/              # static.py, testgen.py, gate.py, judge.py, postconditions.py   (FIREWALLED)
  sandbox/             # image.py (pinned), runner.py
  store/               # db.py, models.py, events.py
  security/            # approval.py, scan.py, untrusted.py, redact.py
bench/
  tasks/               # YAML task specs
  fixtures/            # inputs (incl. drifted variants)
  checkers/            # deterministic checkers (FIREWALLED)
  run.py  report.py
web/                   # React app
fixtures/llm_cache/    # DEMO_MODE recordings
docs/MEDHA_SPEC.md  docs/DECISIONS.md
tests/
```

---

## 8. MedhaBench

**Tasks: 36** (3 domains × 4 families × 3 variants). Every task has a fixture and a deterministic checker in `bench/checkers/`.
- **Data:** sales summary, top-N items, category share, week-over-week change
- **Documents:** invoice total extraction, line-item table to CSV, supplier comparison across PDFs, receipt categorization
- **Reports:** weekly owner summary, price-change alert list, low-stock list from inventory CSV, monthly roll-up

Variants differ in surface details (column names, date formats, values) but share the underlying skill.

**Configurations:**
- **A Baseline:** Muse Glimmer with sandboxed code execution per task, no library.
- **B Unverified skills:** Medha with the gate reduced to "runs without error". This reproduces SkillsBench's self-generated condition.
- **C Medha cold:** full Medha, starting from an empty library, tasks in sequence.
- **D Medha warm:** the C library run on 12 **held-out variants** not seen before.

**Suites:**
- **Repair (6 drift cases):** renamed column, new date format, extra header rows, currency symbols in amounts, PDF layout shift, file encoding change.
- **Composition (6):** tasks that need 2–3 learned skills chained together.

**Metrics:**
- pass rate
- $ per task
- latency
- LLM tokens
- reuse rate
- consistency (5 reruns, same output)
- repair success
- regression rate
- library size over time

**Headline charts:**
1. Pass rate for A, B, C, D (shows that verification is what makes self-made skills useful)
2. Cumulative $ and $ per task over the task sequence, C vs A
3. Repair suite outcomes

**Reproducibility:**
- `uv run python -m bench.run --config all` writes `bench/results/<timestamp>.json` plus PNGs.
- Fixed seeds and the lowest-variance settings the endpoint supports.

---

## 9. Build phases (each ends with tests green, files staged, and a proposed commit message)

| Phase | Target date | Build | Acceptance checks |
|---|---|---|---|
| **P0 Foundations** | Oct 28 AM | uv project, config, `.env.example`, both LLM providers, sandbox runner hello-world, DECISIONS.md from the docs, demo cache | `uv run python -m medha.smoke` gets replies from Muse Glimmer (self-hosted vLLM) and Muse Spark 1.3 (Meta API), and the sandbox prints output with the network blocked (an outbound request fails) |
| **P1 Skill substrate** | Oct 28 PM | Skill package format, SQLite store, event log, static scanner, sandbox executor for packages | A hand-written skill registers and runs in the sandbox with events logged; the scanner rejects 10 malicious samples (tests); the firewall import test passes |
| **P2 Learn + Gate** | Oct 29 | Planner, author, isolated test writer, gate stages 1–5, registration | 3 MedhaBench tasks learned from an empty library; an injected overfit skill is rejected by the holdout gap; the judge is a different model from the author (asserted) |
| **P3 Route + Reward + Feedback** | Oct 30 | BM25 + rerank + applicability, Beta reward, trust levels, approval gate, corrections → cases, lessons | A variant task routes to the existing skill with 0 author calls and $0 LLM execution cost; a correction yields v2 that passes replay; a lesson is created from a failure and is deletable |
| **P4 Repair + Compose** | Oct 31 | Drift detection, repair, replay non-regression, auto-rollback, composer, lineage | Repair suite: 6 of 6 either repaired or safely held; 4 of 6 composition tasks pass; rollback works through the API |
| **P5 API + UI + Deploy** | Nov 1 | Routes, SSE, auth and limits, React UI (chat, timeline, skills, approvals, evidence), Modal deploy | The full demo flow works on the deployed URL in a logged-out browser using the token; "Built with Meta Muse Glimmer" is visible |
| **P6 MedhaBench** | Nov 2 | Task YAMLs, fixtures, checkers, runner, report, charts, README results | One command reproduces results; charts are embedded in the README; the ablation (B vs C) is reported honestly |
| **P7 Polish + Ship** | Nov 3 | DEMO_MODE recordings, README (problem, architecture, security, results, research basis), video, submission | Checklist in §10 complete |
| Buffer | Nov 4 | Fixes only, X post | Submitted before 9 PM ET |

**If time runs short, cut in this order:** escalation model → lineage graph → lessons UI → composition (keep at least 2 demo cases). **Never cut:** gate, firewall, sandbox, repair demo, benchmark A vs C.

---

## 10. Demo video (3:00) and submission checklist

**Video:**
1. **0:00 Hook:** "Agents forget. Research shows skills they write for themselves don't help. Medha fixes that."
2. **0:15 Problem:** Priya's weekly chores take 30 minutes.
3. **0:30 Learn:** upload the POS CSV. Medha writes the skill; the isolated tests, holdout and judge pass; Priya approves.
4. **1:00 Reuse:** next week's CSV is done in about 3 s for $0.00 in LLM cost.
5. **1:20 Compose:** "Report plus invoice totals plus an owner summary" chains learned skills into a composite.
6. **1:50 Repair:** the POS export changes format. The probe fails, Medha repairs, replay passes, and v2 goes live.
7. **2:20 Proof:** MedhaBench charts for A/B/C/D and cost over time.
8. **2:45 Close:** the Meta stack, open weights on Modal, security, and the vision.

**Checklist:**
- [ ] Public repo opens logged-out; README has a GIF, results, architecture and a security section.
- [ ] `.env.example` only; no secrets in git history.
- [ ] The video shows only work built during the event.
- [ ] Submitted on the CV platform.
- [ ] X post with the demo for the Most Viral prize.

---

## 11. Research basis (cite in the README)

- SkillsBench (Li et al., 2026): curated skills +16.2pp, self-generated −1.3pp. This is the problem Medha targets.
- Skill-V (Ma et al., 2026): versioned falsifiable contracts, outcome-driven revision, non-regression on replay, applicability filtering.
- CoEvoSkills (Zhang et al., 2026): a skill generator paired with an isolated surrogate verifier.
- Darwin Gödel Machine (Zhang et al., 2025): objective hacking when evaluators are visible, which motivates the evaluator firewall.
- SpecBench (2026): the visible vs held-out test gap as a measure of reward hacking.
- SkillRouter (Zheng et al., 2026): the full skill body matters for routing.
- Skills as Verifiable Artifacts (2026): trust levels drive human-in-the-loop policy.
- Snyk ToxicSkills (Feb 2026) and Skill-Inject (2026): the skill supply chain and prompt-injection threats.
- Voyager (Wang et al., 2023): the original lifelong skill-library agent.
