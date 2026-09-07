# Corvée Skill Audit — Findings Ledger

Audit date: 2026-09-06
Scope: `/home/kader/Code/skills/corvee` (v0.1.0, HEAD `25dd29d`)
Method: full source review of `scripts/`, `references/`, `SKILL.md`, tests, CI;
independent verification of path confinement, write-protection, packaging,
lint, and the test suite (134/143 pass; 9 fail only on sandbox socket bind).

## Summary

The skill is well-engineered with a coherent security model: the delegate
cannot execute commands, writes are path-confined and `.git`/`reports` are
protected after symlink resolution, credentials are separated from config with
mode-0600 enforcement, redirects are refused, and a dotenv cannot redirect an
externally configured key. Findings below are real but low severity; none
represent an exploitable escape from the documented trust model.

## Findings

- [x] **F1 — `redact()` only strips the literal API key, not other secrets.**
  Fixed: `SKILL.md` now states explicitly that every tool result is persisted
  verbatim to `checkpoint.json`, that only the API key is redacted, and that a
  sanitized checkout is the only complete mitigation for secret-bearing repos.
  This was a documentation gap; the runner already disclaims being a secret
  scanner, and the new text makes the disk-persistence consequence explicit.

- [x] **F2 — `--command-result` is injected into the conversation with no
  content-type or structure validation.** Fixed: the injected command output is
  now wrapped in `----- BEGIN COMMAND OUTPUT -----` / `----- END COMMAND OUTPUT
  -----` delimiters with an anti-injection header stating the output is
  untrusted, that the delegate must not follow any directives it contains, and
  must not treat its claims (pass/fail, file contents) as verified. Test added:
  `test_command_result_is_injected_behind_a_delimiter`.

- [x] **F3 — `--run-dir` has no confinement check; a new run can write its
  artifact tree anywhere the invoking user can create.** Fixed with two changes:
  (1) `--run-dir` is now confined to be inside the `--cwd` repository (a path
  outside is refused with a clear error); (2) `_protect_run_state` now writes a
  `.gitignore` at the run-dir's parent when no `.codex` ancestor is found, so a
  custom run-dir's content-embedding checkpoint is still protected from
  accidental commits. Tests added: `RunDirConfinementTest` (two cases) and the
  updated `test_a_custom_run_dir_outside_codex_gets_a_parent_gitignore`.

- [ ] **F4 — `select MODEL_ID` validates against the catalog but does not run
  `verify_credential`.** Saving a model via `select` calls
  `checked_settings` → `fetch_models` (which *does* send the key in the
  Authorization header to list models) but skips `verify_credential` (the
  one-token inference test). The models endpoint may accept a key that
  `/chat/completions` rejects (different scopes, or a list-only key). `check`
  does verify, and `configure` does verify, so this only matters if a user
  changes models without re-running `check`. Not a security issue, but the
  SKILL.md says "Configuration is complete only after authenticated inference
  succeeds," which `select` alone does not guarantee. Severity: informational.
  Location: `scripts/configure_corvee.py:163` (`select` branch).

- [ ] **F5 — CI does not run `ruff` on all matrix legs and has no drift guard
  beyond the test.** The lint job runs only on `ubuntu-latest` / Python 3.12,
  which is fine, but the matrix does not pin ruff or run `package.py` on macOS.
  More notably, there is no CI step that asserts the git-tracked file set
  matches `PACKAGE_FILES` + `EXCLUDED_FILES`; the `PackageManifestTest` covers
  this locally but only if someone runs the tests. A newly added script that is
  not added to `package.py` would silently be missing from releases. Severity:
  informational. Location: `.github/workflows/ci.yml`, `scripts/package.py:13`.

- [ ] **F6 — `load_env_file` name validation allows names `API_KEY` and
  `MODEL` to collide with unrelated tools.** The loader accepts any
  `[_A-Za-z][A-Za-z0-9_]*` name. `CORVEX_API_URL`/`API_URL` are only honored
  when a key is also present (good), but a bare `MODEL` in a dotenv is silently
  ignored (documented in `provider-setup.md`) while `API_KEY` *is* honored as a
  fallback key. If a user's dotenv sets `API_KEY` for another service, corvee
  will pick it up as the Corvex credential. The precedence (`CORVEX_API_KEY`
  first, then `CORVEX_<env>`, then `API_KEY`) makes this unlikely in practice.
  Severity: informational. Location: `scripts/corvee_config.py:resolve_api_key`
  (`API_KEY` fallback), `scripts/corvee.py:1057` (same).

## Verification performed

- `python3 -m unittest discover -s tests` → 134 pass, 9 errors (all
  `CorveeTest` `setUp` `PermissionError` on `socket()` — sandbox-only; these
  tests pass in CI with network).
- `python3 -m ruff check scripts tests` → clean.
- `python3 scripts/corvee --help` / `--version` → correct.
- `python3 scripts/package.py` → builds `dist/corvee-0.1.0.tar.gz` + sha256.
- Path-confinement: symlink escape, `../` traversal, and `.git`/`.GIT`/nested
  `vendor/.git` write attempts all blocked.
- `.codex/` is gitignored; no real secrets found in dev artifacts.
- Installed copy at `/home/kader/.codex/skills/corvee` matches source.

## Not a finding (verified safe)

- Delegate cannot execute commands (only `request_command`, exit 65).
- Credential file enforced mode 0600; config 0600.
- HTTP redirects refused (`NoRedirect`).
- Dotenv URL-only redirect refused without a co-located key.
- `git_status`/`git_diff` use fixed argv (no injection).
- `rg`/`grep` patterns passed as `-e`/`-E` (no preprocessor flag injection;
  tested `--pre=`).
- Resume refuses to widen authority (read-only → write) and refuses
  `--command-result` on non-suspended runs.
- Checkpoint files are mode 0600; run directory 0700.

## Follow-up audit fixes — 2026-09-07

- [x] **F7 — Custom run evidence was delegate-writable.** Pass the resolved
  active run directory to repository tools and reject writes beneath it,
  including symlink aliases, for both new and resumed runs. Reads remain allowed.
- [x] **F8 — Existing ignore files left checkpoints eligible for commits.**
  Preserve existing content and append an anchored exclusion for the actual
  artifact location. Escape pattern characters, handle custom paths within
  `.codex`, and fail before starting if protection cannot be written.
- [x] **F9 — Default run paths bypassed symlink containment.** Resolve the
  default path before checking repository containment, matching explicit paths.

Regression coverage includes custom evidence overwrite/replacement attempts,
symlink aliases, default `.codex` escape, existing ignore rules, idempotent
updates, and Git-verified exclusions for custom directory names.

## Second follow-up — 2026-09-07

- [x] **P2 — Resume reset cumulative accounting.** Store usage and economics
  in checkpoints and restore cumulative token, request, mission, and tool
  counters on resume. Prefer checkpoint accounting over stale status; fall
  back to status for legacy checkpoints. Refuse malformed counters. Recompute
  report/diff snapshots instead of accumulating them. Regression tests cover
  repeated resumes, interrupted resumes, legacy fallback, and invalid counters.
- [x] **P1 — Unconfined Git inspection executes configured helpers.** Replaced
  direct status/diff and automatic measurement subprocesses with fixed operations
  through Codex command/exec in a read-only sandbox; no local fallback. Disable
  external diff/textconv/fsmonitor/hooks, clear inherited Git settings, and ignore
  submodule inspection. Regression tests cover external helper suppression,
  accounting, executor rejection, and protocol without model turns. A live check
  verified status/diff/accounting and rejection of repository writes. This closes
  the unconfined inspection path, not a claim that all repository code is safe.

## Savings-claim review — 2026-09-07

- [x] Reverified P2 cumulative accounting with the full 155-test suite.
- [x] Recomputed historical same-model harness cost: Corvée $0.0405378,
  codex exec $0.07980765 per mission, two trials each. Evidence is limited
  to that small reading task and excludes planner overhead.
- [x] Replaced unsupported direct-Astra winner/break-even claims and corrected
  the Astra output/cache-write price confusion in COST_ASSESSMENT.md.
- [x] Replaced the skill's 40K-token delegation rule with specification,
  independence, and verification criteria; qualified the README comparison.
- [x] Included the cost assessment in release packaging so its README link works.
- [ ] Run the matched three-arm pilot after establishing a small spending
  allowance and an available general-purpose harness. No new paid model runs
  were made during this review; no new end-to-end savings claim is warranted.

## Fresh Astra comparison — 2026-09-07

- [x] Located Codex CLI outside PATH and ran one low-effort Astra baseline.
- [x] Ran Corvée/GLM on the identical mission and source snapshot; both passed
  the predeclared exact JSON gate and left source unchanged.
- [x] Recorded provider usage and computed costs: Astra $0.195988 standard
  API-equivalent; Corvée $0.00573735. Worker ratio 34.16x; incremental planner
  overhead must be below $0.190251 to yield overall savings on this task.
- [ ] Broader cost-per-accepted-task claims still need measured planner usage,
  representative write tasks, and repeated matched trials. No additional
  runs were launched after the two successful pilot arms.

## Full-cost delegation pilot — 2026-09-07

- [x] Measured direct Astra against Astra planning + Corvée + resumed Astra
  review/repair on the same compound-duration task and fixed gate.
- [x] Independently verified both final gates and scope; production source unchanged.
- [x] Included worker failures, one worker resume, and both reviews; verified
  per-turn Astra accounting and cumulative Corvée accounting before summing.
- [x] Recorded $0.384058 direct versus $0.5010915 delegated, with $0.187194
  planning and $0.267708 review/repair. Documented completion-cap exhaustion,
  resumed sandbox configuration error, and cache sensitivity as limitations.
- [ ] Measure successful delegation with sufficient worker output allowance
  before generalizing this constrained fallback result. No further paid runs
  launched after this pilot; dedicated total $0.8851495 API-equivalent/catalog.

## Reduce Astra involvement and repair worker behavior — 2026-09-07

- [x] Verified effective Corvex GLM thinking control using response metadata;
  native thinking/effort options had not disabled reasoning on that endpoint.
- [x] Added --thinking, --max-output-tokens, inference-setting resume persistence,
  finish-reason diagnostics, safe incomplete handling before tools, and
  same-step output-limited recovery. Preserved provider reasoning fields privately.
- [x] Added bounded untrusted --feedback for repairs within the same worker
  conversation; pending command requests still require --command-result.
- [x] Simplified default workflow to task reuse, cheap implementation/repair,
  parent gate execution, and one final review. Separate Astra planning/audit
  sessions are conditional rather than routine.
- [x] Corrected worker passed the original duration gate; real CLI feedback
  repaired a further over-maximum edge case. Combined worker cost $0.07695.
- [x] 174 native tests pass; Ruff clean. Full revised planner cost remains
  unmetered; worker-only costs are not presented as end-to-end savings.
- [x] P1 was unresolved at this earlier checkpoint; fixed by the later read-only
  executor change above. No new command execution
  capability was introduced as part of this work.


## Corrected full workflow measurement — 2026-09-07

- [x] Ran one fresh matched task with a fixed oversized-input gate in both arms.
- [x] Metered direct Astra, all Corvée attempts, Astra review and resumed
  acceptance. Task reuse required no separate planning inference.
- [x] Both final patches passed gate, review counterexamples and scope checks.
  Preserved worker exit-3 statuses separately from independent code acceptance.
- [x] Recorded $0.576740 delegated versus $0.453338 direct (27.2% more),
  125.48s versus 54.78s; dedicated total $1.030078. Setup/conversation unmetered.
- [ ] Reduce repeated worker reading/history and repair round trips before
  claiming end-to-end savings; this single corrected trial did not save money.

## Jobs with no routine Astra model calls — 2026-09-07

- [x] Added `corvee job`: bounded source packets, exact validated edits, caller
  gate, fresh Corvée repair calls, and a separate cheap review. No browsing
  tools, Git inspection, Astra invocation, or automatic publishing in this lane.
- [x] Added complete-input limits, protected fixture checks, source-conflict
  detection, strict output schemas, bounded calls, and partial usage accounting.
- [x] Added CLI/package integration and workflow/routing instructions. Existing
  installed skill symlink uses this checkout directly.
- [x] 200 native tests pass; Ruff, skill validation, package and diff checks pass.
- [x] Cheap implementation produced the new module/tests; parent verification
  corrected integration, integrity, test and accounting errors. Development
  cost is separate from task evaluation; not evidence of autonomous correctness.
- [x] Measured a fresh integrated Astra dispatch/job/report workflow, retaining
  an explicitly authorized reasoning-enabled recovery: $0.560470 incomplete
  versus $0.535228 direct accepted code. Dedicated measurement total $1.095698.
- [ ] Demonstrate successful live implementation plus cheap review and lower
  accepted-task cost. New lane remains experimental; no global saving claimed.

## Direct execution, Kimi controls, and thinking policy

- [x] Tried direct Kimi jobs without dedicated Astra calls; retained the timeout
  as unknown cost and the $0.0144551 instant-mode failure. No savings claim.
- [x] Verified and implemented Kimi's distinct `chat_template_kwargs.thinking`
  control in run/job; GLM keeps `enable_thinking`.
- [x] Corrected guidance to prefer thinking for substantive work with explicit
  output/time allowance; instant mode is mechanical/experimental.
- [x] Added optional Codex app-server gate executor with no model thread/turn,
  bounded sandbox execution, output/exit preservation and no local fallback.
- [x] Persisted cheap-review findings and diagnostics; CLI output is compact.
- [ ] Successful live cheap implementation plus independent review, and global
  savings including any Astra decisions, remain unproven.

## Larger package economic evaluation — 2026-09-07

- [x] Compared identical four-module SQLite queue stubs/specification and a fixed
  21-test gate. Included real concurrent-connection claim tests.
- [x] Metered Astra alone, one Astra architecture pass, thinking-enabled Kimi
  implementation plus repair and independent cheap review, and final Astra review.
- [x] Used model-free Codex sandbox gates; no Astra implementation in delegated arm.
- [x] Applied five reviewer-discovered counterexamples to both arms: Astra alone
  passed; delegated code failed despite its original gate and cheap review passing.
- [x] Recorded $0.634304 direct accepted versus $0.662880 delegated rejected;
  total dedicated trial $1.297184. Astra delegation overhead $0.539676; only
  1.18x direct-cost/overhead ratio, below the proposed 5x target. No saving claim.
- [ ] Improve cheap review with executable adversarial checks before another
  accepted-task cost comparison; larger packages alone did not solve the problem.
