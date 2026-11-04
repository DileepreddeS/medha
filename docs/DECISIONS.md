# Medha — Decisions log

Pinned facts and choices. Read before changing providers, models, or infra.

## 2026-11-02 — Brain model: Muse Glimmer (self-hosted), judge: Muse Spark 1.3

User directive (2026-11-02): do NOT use Muse Spark Open (Modal shared
endpoints aren't covered by their credits). All author-side roles move to
**Muse Glimmer**, self-hosted on Modal. Judge + escalation stay on
**Muse Spark 1.3** via the Meta Model API.

### D1. Glimmer model identity (verified, not guessed)

- Hugging Face repo: `meta-models/Muse-Glimmer-30B`
- License: Apache 2.0. Authors: Meta Superintelligence Lab, Aug 2026.
- 30B dense causal LM (+ perception encoder), distilled from Muse Spark,
  built for agentic tasks; fits a single 80GB GPU in BF16.
- Sources: https://huggingface.co/meta-models/Muse-Glimmer-30B (model card),
  https://dev.meta.ai/docs/muse-glimmer/* (official docs section),
  press corroboration (WebProNews, Verdict, SingularityByte, 2026-08).
- Kept in env as `GLIMMER_MODEL_ID` (default `meta-models/Muse-Glimmer-30B`).

### D2. Glimmer serving on Modal (from installed Modal skill, v1.6.3)

Pattern: `examples/llm_inference.md` — `@app.server` running
`vllm serve` (OpenAI-compatible `/v1/chat/completions`), model weights cached
in Modal Volumes. Implemented in `medha/glimmer.py`.

- Image: `nvidia/cuda:12.9.0-devel-ubuntu22.04` + `vllm==0.33.0` +
  `transformers==5.20.2` (verified 2026-11-03 on PyPI:
  v0.28.0 was the first release with native `muse_glimmer`
  support; 0.33.0 is the latest and requires
  `transformers>=5.16.1,<5.21.0`, so 5.20.2 is the newest allowed;
  PyPI latest transformers is 5.22.1 but vLLM 0.33.0 excludes it).
  The old `vllm==0.21.0` pin (transformers 4.57) did not recognize
  `muse_glimmer` at all. `HF_XET_HIGH_PERFORMANCE=1`
  only — `HF_HUB_ENABLE_HF_TRANSFER` is deliberately unset (hf_transfer
  isn't installed; the flag crashed the config download at boot).
- Serve flags: `--served-model-name glimmer` kept; `--async-scheduling`
  REMOVED — it was a V0-engine opt-in and newer V1-default servers
  reject the explicit flag at startup (argparse error = boot crash).
  `--enforce-eager` kept for fast scale-to-zero cold boots.
- GPU: single `A100-80GB` (verified valid string in `guide/gpu.md`;
  `H100` also valid — prefer H100 when available, it's faster per $ for
  30B BF16). BF16 via `--dtype bfloat16`.
- Weights cache: Modal Volume `medha-hf-cache` at
  `/root/.cache/huggingface`; vLLM compile cache `medha-vllm-cache`.
- Scale to zero with ~2 min idle: `scaledown_window=120`
  (seconds; llm example uses `15 * MINUTES` seconds).
- `startup_timeout=600`, `target_concurrency=4` (low: single replica,
  hackathon traffic), `port=8000`.
- Auth: vLLM API key (`GLIMMER_API_KEY`, generated locally, stored in
  `.env` + `medha-secrets`), exported as `VLLM_API_KEY` in the container
  (NEVER `--api-key` on argv — vLLM logs non-default args in plain text,
  which leaked the first key into Modal logs; rotated 2026-11-03).
  Clients send standard `Authorization: Bearer`.
  The OpenAI SDK does this natively, so no custom header plumbing is
  needed anywhere. Modal layer is `unauthenticated=True` (per the skill
  example) with the vLLM key guarding `/v1/*` — strictly stronger than
  the example's open endpoint. Deliberately NOT Modal Proxy Auth: per
  `guide/servers.md` that would need Proxy Tokens
  (`Authorization: Bearer <id>.<secret>` or `Modal-Key`/`Modal-Secret`
  headers) minted at the workspace level — broader blast radius and more
  moving parts for zero demo benefit. The server startup refuses to boot
  without the key, and the key is redacted from the launch log line.
- Client: OpenAI SDK pointed at `GLIMMER_BASE_URL` (set after deploy from
  `modal deploy` output / `Server.get_url`). Served model name `glimmer`.
- Deploy: `uv run modal deploy medha/glimmer.py`, then set
  `GLIMMER_BASE_URL` in `.env` and in the `medha-secrets` Modal Secret.

### D3. Judge / escalation: Muse Spark 1.3 via Meta Model API (verified)

Canonical config from the official cookbook
(meta-models/meta-model-cookbook, `01_api_fundamentals/README.md`),
corroborated by 10+ independent sources (2026-09/10):

- SDK: OpenAI Python SDK (`from openai import OpenAI`). Do NOT use the
  outdated official Model API client package.
- Base URL: `https://api.meta.ai/v1` (OpenAI Chat Completions compatible).
- Model ID: `muse-spark-1.3`. Context 1,048,576 tokens.
- Auth: `Authorization: Bearer <key>`, key format `LLM|{id}|{secret}`.
  Docs call the var `MODEL_API_KEY`; our env name is `META_API_KEY`
  (passed explicitly to the SDK, same value).
- Consistent with spec §2 ("accepts OpenAI Chat Completions and Anthropic
  Messages formats") — we use Chat Completions only.

### D4. Secrets

- Modal Secret name: `medha-secrets` (already created by the user).
  Keys: `META_API_KEY`, `HF_TOKEN`, `MEDHA_TOKEN`, `GLIMMER_MODEL_ID`,
  `DAILY_SPEND_CAP_USD`. Same keys in local `.env` (never committed).
- Attached with `modal.Secret.from_name("medha-secrets")`
  (verified signature in skill `api/Secret.md`: `from_name(name, ...)`).
- `HF_TOKEN` is used only at Glimmer deploy/image-build time for gated
  weight downloads; never injected into code-execution sandboxes.

### D5. Sandbox API facts (from Modal skill)

- `modal.Sandbox.create(app, image, block_network=True, cpu=1, memory=1024,
  timeout=60, volumes={...}, workdir="/work")` — signature verified in
  `api/Sandbox.md`. `block_network` default False → we always pass True.
- Run code with `sb.exec("python", "/work/run.py", timeout=55)`; read
  `process.stdout.read()` / `stderr`; `process.returncode` via `wait()`.
  Pattern from `guide/sandbox-spawn.md`.
- File transfer: `sb.filesystem.write_text(content, path)` /
  `read_text(path)` / `copy_from_local` / `copy_to_local` / `list_files`
  (verified in `guide/sandbox-files.md`; no binary API, so binary fixtures
  go over as base64 text and are decoded inside by our `_medha_run.py`).
  Skill code + fixtures are written fresh per run (no stale state).
- `sb.exec("python", script, timeout=...)` returns a ContainerProcess with
  `.wait()`, `.stdout.read()`, `.stderr.read()` (verified `exec` signature
  in `api/Sandbox.md` + `guide/sandbox-spawn.md`).
- Future network skills: `outbound_domain_allowlist` exists on
  `Sandbox.create` (verified in `api/Sandbox.md`) — the hook for a domain
  allowlist, but network stays blocked for the MVP per spec §4.
- The verifier firewall (§6 of spec, AGENTS.md rule 2) is enforced by
  import structure + `tests/test_firewall.py`, not by mounts.

### D6. UI footer + rule conflict note

- Footer text is now **"Built with Meta Muse Glimmer"** (user directive).
- This supersedes AGENTS.md rule 8 and spec §6/§9-P5
  ("Built with Muse Spark Open"), which were tied to the Spark Open license.
  AGENTS.md itself is standing rules and was intentionally NOT edited;
  the human owner issued the override directly, so no scope-stop was needed.

### D7. Environment blocker (2026-11-02)

- This build session has no working shell: every `powershell` call fails in
  sandbox setup (`ACL publication lock ... timed out`), and an escalated
  (unsandboxed) run was declined by the user. So `uv run pytest`,
  `ruff`, `mypy`, `modal deploy`, and `git add` could NOT be run here.
- Mitigation: code written carefully against the Modal skill refs and the
  Meta cookbook; firewall holds by construction; tests included for every
  module. The human must run the verification gate in `docs/PROGRESS.md`
  (exact commands listed) before committing or demoing.

### D8. TEMPORARY: Spark 1.3 covers author roles until Glimmer boots (2026-11-03)

- New config flag `LLM_AUTHOR_PROVIDER` (`"glimmer" | "meta"`, default
  `"meta"` for tonight). Author-side roles (planner/router/author/
  testwriter/repairer/composer) follow the flag; judge/escalation stay
  `"meta"` either way (`ROLE_PROVIDER` untouched).
- Consequences while active: (1) author and judge are the SAME model
  (evaluator/author split collapses — fine for loop verification, NOT for
  reported bench numbers); (2) author calls bill Meta tokens against
  `DAILY_SPEND_CAP_USD` instead of $0 self-hosted.
- `medha/smoke.py`'s Glimmer check bypasses role routing (direct
  `OpenAIBackend` on `GLIMMER_BASE_URL`) so it keeps probing the real
  endpoint under either flag value.
- REVERT PLAN (do all three once Glimmer serves traffic): set
  `LLM_AUTHOR_PROVIDER=glimmer` in local `.env` AND in `medha-secrets`
  (deployed `medha/deploy.py` reads env from the secret), flip the
  `medha/config.py` default back to `"glimmer"`, and update
  `tests/test_config.py::test_defaults` + this entry.

### D9. Glimmer live-fix batch (2026-11-03, verified serving on vLLM 0.33.0)

- Channel separation: `--reasoning-parser muse_glimmer` +
  `--tool-call-parser muse_glimmer`, per the official vLLM recipe
  (recipes.vllm.ai/meta-models/Muse-Glimmer-30B). BOTH are required:
  they key off the same `to=self` / `assistant to=user` framing, and
  without both, reasoning collapses into message.content (the observed
  leak). Reasoning now lands in `message.reasoning_content`.
- Deliberately NOT added from the recipe: `--generation-config auto`
  (would apply the model's temperature 1.0 server-side; our client
  always sends an explicit per-request temperature) and
  `--enable-auto-tool-choice` (we make no tool calls).
- Client safety net: `strip_channel_tags()` in `medha/llm/client.py`,
  applied on the Glimmer backend only (`reasoning_guard=True`).
- Sampling: `ROLE_TEMPERATURE` — author 0.2, testwriter 0.2, judge 0.0
  (deterministic verdicts); other roles keep effort defaults.
- Warm-up hits the deployed app: `modal.Server.from_name(
  "medha-glimmer", "GlimmerServer").get_url()` — verified in installed
  Modal 1.6.3 (`_server.py`: `from_name(app_name, name)`, name defaults
  to class name). Never starts an ephemeral -dev server; prints the URL
  for `GLIMMER_BASE_URL`. Fails loudly if nothing is deployed.
- Cache persistence: `HF_HOME`, `VLLM_CACHE_DIR`/`VLLM_CACHE_ROOT`,
  `TRITON_CACHE_DIR`, `FLASHINFER_WORKSPACE_BASE`,
  `VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR` all on the two Volumes (TRITON/
  FLASHINFER vars must be explicit: vLLM redirects them only on the
  torch.compile path, which `--enforce-eager` disables). A daemon
  thread commits both volumes once `/health` passes (the 193 s
  re-download was missing `Volume.commit()` — verified in
  `volume.py`: commits must happen in-container), plus a best-effort
  commit in `@modal.exit` to capture serving-time autotune artifacts.

### D10. Bench empty-content failure (2026-11-03)

- Symptom: both `one.yaml` tasks failed with `planner JSON failed
  schema TaskSpec ... input_value=''`; summary showed 0 tokens.
- Ruled OUT by the data: config loading, `.env`/`META_API_KEY`,
  `DEMO_MODE`, spend cap (calls went through — ~46 s latency; a config
  or cap failure raises `LLMError`/`BudgetExceeded`, not `SchemaFailed`).
  The 0-token figure was a reporting artifact: usage is logged to the
  event log only after a successful call.
- Root cause: reasoning model vs `max_tokens` budget. Smoke evidence:
  Spark 1.3 spent 299 completion tokens on a 1-word reply, i.e. it
  thinks at length. The planner's 2048-token medium budget was almost
  certainly exhausted by reasoning (`finish_reason=length`), leaving
  empty content. The code previously swallowed `finish_reason`, so the
  failure presented as a bare schema error on `''`.
- Fixes: (1) `OpenAIBackend` raises `EmptyContent` (carries
  finish_reason + token counts + reasoning tokens) on blank replies;
  `complete_json` surfaces these in the final `SchemaFailed`. (2)
  Budgets raised with reasoning headroom: low 2048 / medium 8192 /
  high 12288. (3) The `complete_json` retry now drops `response_format`
  and relies on the ONLY-JSON instruction, so a systematic failure of
  one mechanism (e.g. a provider mishandling `json_object` mode — the
  alternate theory) does not doom both attempts.
- Bench now prints one PASS/FAIL line per task plus the error text and
  the event-type sequence on failure (`_report_row` in `bench/run.py`).
- `tests/test_bench.py::test_learn_path_records_llm_calls` fails if a
  learn-path task records zero `llm.*` events / zero tokens.
- Open risk: `META_PRICE_*` are still 0.0, so real Meta-side spend is
  untracked. Set real prices before any large bench run.

### D11. Prompts must carry exact JSON skeletons (2026-11-03)

- Root cause of the `sub_goals.0.name` bench failure: the planner prompt
  said "matching the requested schema" but never showed any schema, so
  the live model improvised sub-goal keys (`id`/`desc`/`order`/`goal`)
  while `SubGoal` requires `name`. (`extra="ignore"` tolerates extras;
  the failure was the missing required key.)
- Rule from here on: every role prompt carries an exact JSON skeleton
  with required keys marked; skeletons are contract-tested by
  `tests/test_prompts.py` so prompt and schema can't silently drift.
- Same-batch gaps fixed proactively (each would have failed on the
  next frontier): author/composer `contract` shape was referenced but
  never specified → shared `CONTRACT_SHAPE` in
  `medha/llm/prompts/_shared.py` (single source, both prompts embed
  it); router never specified the `{ranking: [...]}` wrapper →
  specified; repairer/testwriter keys quoted for uniformity.
- Judge `pass` alias audited: prompt says `pass`, model declares
  `Field(alias="pass")`, nothing constructs `Verdict(passed=...)` in
  Python, gate reads attributes — consistent, no change.
- Bench error detail widened `[:200]`→`[:800]` so multi-error pydantic
  messages survive to the console and `results.json`.

### D12. Skills always receive logical-keyed inputs (2026-11-03)

- Symptom: every visible test died with `KeyError: 'sales_csv'` —
  the learn path passed filename-keyed files to the sandbox while
  the author/testwriter contracts promise logical names.
- Fix: one shared pure `map_inputs` in `medha/store/models.py`
  (exact-name match first, so already-logical keys pass through;
  then format match; None on mismatch). The gate maps once up
  front (new loud `inputs` failure stage + `gate.inputs` event),
  which fixes learner, repair, and composer together; `solve_once`,
  correction cases, and bench A/B map by `draft.contract`
  (deleted `_map_by_spec`, which mapped by the TaskSpec whose names
  the author's contract need not keep).

### D13. `output NAME exists` resolves result keys (2026-11-03)

- A correct skill was rejected because its file was named
  `report.md` while its result key was `report_md` — the check
  only matched literal filenames. It now also follows
  `result[NAME]` when it is a path string resolving into the
  output files. This aligns the outcome stage with the isolated
  tests (whose `test_report_md_exists` had passed), so it is a
  consistency fix, not a weakening.

### D14. Judge sees input samples (2026-11-03)

- Semantic postconditions about inputs ("sums amount by category
  from sales_v1") are unverifiable from outputs alone; the judge
  honestly failed them ("no input CSV to verify sums") and killed
  correct skills twice. The judge now receives the input schema
  samples and is instructed to verify input-claims against them.
  Samples are task data, not author reasoning: no firewall issue,
  no spec deviation (spec §3.5 names contract+outputs and forbids
  only author reasoning).

### D15. LLM budgets: low 4096, high 16384 (2026-11-03)

- A router rerank call length-capped at 2048 (635 in, 2048 out,
  `finish_reason=length`); low (router-only) is now 4096 with a
  brevity line in the router prompt. A repair-path testwriter call
  then rambled 12288 tokens with zero answer tokens; high
  (author/testwriter/repairer/composer) is now 16384, and the
  testwriter is capped at 4–6 focused tests to bound outputs.

### D16. Schema-role probe before HIT execution (2026-11-03)

- A `kind`-header drift produced a silent wrong answer: the skill
  miscategorized by item while deterministic checks passed on
  consistent-but-wrong output (sums reconcile, categories appear).
  The old probe only matched file formats.
- Fix: registration records resolved column roles per logical
  input in provenance (`record_input_roles`); every HIT
  re-resolves them on the current files (`router.schema_probe`)
  and misses to repair when a recorded role no longer resolves.
  Renames that preserve roles (`cat`/`total_usd`) still reuse;
  unresolvable roles (`kind`) repair. The alias table lives in
  `medha/store/models.py` and in the author prompt, pinned in sync
  by `test_author_prompt_matches_role_table` so probe and skill
  resolve identically.
- Deviation note: the probe runs host-side with stdlib `csv` on
  OUR code, not in a sandbox — the spec's "generated check in the
  sandbox" (§3.3.5) targets generated code, and parsing headers
  with the standard library is safe (same precedent as the
  existing host-side `schema_preview_csv`). Intent honored: never
  a silent wrong answer. The author prompt's alias examples are
  generic POS vocabulary, not fixture contents.

### D17. Test/deploy hygiene from the live session (2026-11-03)

- `/api/bench` read CWD-relative `bench/results/*.json`, so a stale
  `latest.json` broke `test_api` (and would break on Modal where
  CWD has no bench dir). Now reads `MEDHA_BENCH_RESULTS` setting
  (default `bench/results`); tests point it at tmp.
- `ruff format --check .` panics on ruff 0.17.3 when scanning
  `bench/results` (generated code), `.agents` (vendored skill
  refs), or `.pytest_cache` — all excluded in `pyproject.toml`.
  Upstream bug; scoped sets were always clean.
- `mypy medha` ran green for the first time: 41 pre-existing
  `**common` unpack errors fixed with 2 annotations (no behavior
  change).
- `task.execute_failed` event now logs execution detail before
  repair attempts (previously lost when repair raised).

### D18. Mini-C1 prompt guards: coverage, fidelity, types (2026-11-03)

- First mini-C scored 7/12 with three LLM-judgment failure classes:
  (a) router HIT a partially-covering skill (owner→sales, missing
  date range); (b) router HIT across a rule change (receipts_v2's
  new vendor rules, invisible to any schema probe); (c) author
  returned a file path where the goal asked for a list, and tests
  plus judge blessed the file while the result value was garbage.
- Guards (all contract-tested in `test_learned_prompt_guards_*`):
  router applicability requires output coverage of EVERY success
  criterion ("never force a fit"); author must match the goal's
  result schema exactly ("data stays data"); testwriter must assert
  result key types, not just file contents.
- D18b: the coverage prompt moved reasons but not verdicts (three
  more forced HITs named the gap yet voted yes, relevance
  0.75–0.8). Enforced in code instead: `contradicts_yes` drops a
  yes-vote whose reason carries a contrastive gap marker
  (`GAP_MARKERS`), downgrading to miss → learn fresh. A downgrade
  costs a learn; a forced HIT fails the task. Tested by
  `test_contradicted_yes_downgrades_to_miss`.

### D19. Judge verifies the task goal, not just the contract (2026-11-03)

- A stock skill returning `low_stock` as a list of dicts (goal:
  list of names) passed all five gate stages: author, tests, and
  judge all verified contract-consistency while none saw the
  goal's prescribed schema. The judge now receives the task goal
  + success criteria and must fail outputs that miss them. The
  TaskSpec is task data (planner output), not author reasoning:
  no firewall issue. C-only change; A (no router) and B (no
  judge) unaffected.

### D21. Guard vetoes any non-"no" gap-naming vote (2026-11-03)

- receipts_v2 forced-HIT a third time with reason "...specifies Whole
  Foods/Shell rules instead of required Trader Joes/BP rules" even
  though "instead of" is a GAP_MARKER: the rerank voted
  applicable="unsure" (rel 0.75), and the guard only vetoed "yes"
  votes, so the unsure vote scored and HIT. The prompt already told
  the model "a known gap plus yes is a contradiction" — the model
  complied (unsure), the code betrayed it. Fix: any non-"no" vote
  whose reason carries a marker is downgraded to miss + learn fresh.
  Tested by `test_unsure_gap_reason_downgrades_to_miss`.
- `route.candidates` now also logs per-candidate votes
  (applicable/relevance/reason): the sales_v2 "all inapplicable" miss
  was undiagnosable without the rerank reasons.

### D22. "sale"/"sales" are amount aliases (2026-11-03)

- top_v2.csv headers are (product, sales); the probe failed the amount
  role and routed to repair instead of a direct HIT. The repairer
  independently patched the same aliases into the skill. POS exports
  universally call revenue "sales", so the global table + author
  prompt now include sale/sales (kept in sync by
  `test_author_prompt_matches_role_table`). Repair stays the backstop
  for genuinely unknown headers.

### D23. Owner tasks pin date-format echo (2026-11-03)

- owner_v1 expects ISO dates, owner_v2 MM/DD/YYYY — the input CSVs'
  own formats — but the goal only said "stating the date range".
  Both A and C failed owner_v2 (C normalized to ISO; the judge
  honestly passed it per the goal as written). Spec gap, not a model
  bug: the goal now says "Write the dates exactly as they appear in
  the input CSV (same format)". Finals tables keep the honest FAILs.

### D24. Glimmer is the default author; import-block prompt (2026-11-03)

- one.yaml on Glimmer: 2/2 PASS (v1 learned on attempt 3, v2 HIT with
  0 author calls, 4025 vs 55833 tokens). Resolves D2: default flipped
  to `LLM_AUTHOR_PROVIDER="glimmer"` (code default + .env); judge and
  escalation stay on meta/Spark 1.3 (evaluator/author split).
- Glimmer failed twice with `NameError: pathlib not defined` (missing
  import despite correction quoting the error). The author prompt now
  shows a literal import block skill.py must start with (all names
  allowlisted by the static scanner). Spark authors are unaffected.

### D25. Testwriter sees the task goal; judge schema-exactness (2026-11-03)

- top_v1 learned `return {"result": path}` for a goal prescribing
  `Return {"top": [...]}` — and the full gate PASSED it: the contract
  declared result-as-path, the testwriter tested only the contract
  (even writing `test_result_value_is_file_not_list`), the outcome
  check was vacuous ("output result exists"), and the judge blessed
  the file's contents. Same disease hit econ-C's receipts
  (path-return) and invoice (`{"total": {"total": N}}`) learns, with
  HITs amplifying each bad learn onto later variants.
- Fix (D19's mirror): `write_tests` takes the TaskSpec and the goal +
  criteria outrank the contract ("test the GOAL's schema and let the
  skill fail"). The goal is planner output (task data), so testwriter
  isolation holds. Judge gains a schema-exactness rule: a path where
  the task wants data FAILS even if the file is correct.
- Proven by `bench/tasks/top.yaml`: 2/2 (v1 learns goal-conformant,
  v2 HITs). Tested by `test_write_tests_includes_task_goal` +
  prompt pins.

### D26. Outcome stage machine-checks planner-declared result shape (2026-11-03)

- D25 narrowed the escape (right key) but econ-C's top learn still
  registered `{"top": "/work/out/top.json"}` — path string under the
  right key — and the goal-primed suite + judge blessed the file's
  contents again. Prompt-strengthening has diminishing returns
  against Spark's file-indirection habit; the third occurrence gets
  a deterministic check.
- The planner now declares every Return key with a data type
  (list/dict/number/string/boolean/file, file only for
  goal-described paths). `check_spec_shape` asserts presence +
  isinstance at the outcome stage and fails loud (`result['top']
  is list ... got str`), feeding the correction loop instead of
  registering garbage. Unknown type names are skipped, never
  failed; empty outputs keep old specs a no-op.
- This converts silent garbage into loud attempt-failure: the task
  still fails if no attempt complies, but nothing bad registers and
  HITs cannot amplify it.
- Proven by `bench/tasks/top.yaml` rerun: 2/2, outcome payload shows
  `result['top'] is list ... got list` (planner emitted the type,
  author complied, gate verified mechanically).

### D27. Bare "never" is a gap marker (2026-11-03)

- econ-C #3's only fail (owner_v1, 17/18) forced-HIT the sales skill
  with reason "...but contract never guarantees date range or exact
  date format." The marker list had "never mention" but not bare
  "never", so the guard missed. Empirical check across all 11 HIT
  reasons in the run: "never" appeared only in this fail; the 10
  passing HITs state plain coverage. Replaced "never mention" with
  "never " (trailing space, same style as "without "). Per the
  tie-break-toward-miss rule, a false downgrade costs only a learn.
- The fail was loud and contained: nothing registered, owner_v2/v3
  learned fresh and passed. No rerun: 17/18 stands with the lesson.

### D28. Repair shape-checks against the contract, not the task spec (2026-11-04)

- Demo recording: the kind-drift repairer fixed the alias and passed
  visible/holdout 5/5, then failed outcome on `result has
  markdown_report` — the repair task's planner named the report key
  `markdown_report` while v1's contract (and skill) use `report_md`.
  Planner key names vary run to run; the repair was correct.
- A repair revises vN into vN+1 of the SAME contract, so the outcome
  shape check now takes the contract's outputs for repairs
  (`shape_outputs` gate param; judge still verifies task fitness).
  Learn keeps planner-spec enforcement (the contract is untrusted
  there). Tested by repair + gate override tests.

### D29. "omit" is a gap marker; honest partials can compose (2026-11-04)

- Demo recording: the compose preset forced-HIT the sales skill with
  reason "...omits reorder list..." — "omit" was missing from
  GAP_MARKERS, so the guard missed and one skill claimed a two-goal
  task. Added "omit" (same bare-word style as "never ").
- Downgrading to miss would strand genuinely partial coverage, so
  "unsure" + gap-marker votes now become *honest partials*: they pool
  with strong candidates (PARTIAL_MIN 0.4, pool gate >= 2) for
  `compose` on multi-goal tasks, and can never HIT alone. Tested by
  compose-from-partials + single-partial-miss router tests.
