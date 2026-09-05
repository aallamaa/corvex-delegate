# Goal control protocol

Use a durable control directory in the target repository:

```text
.codex/corvee/
|-- TARGET.md
|-- STATE.md
|-- DELEGATE.json
|-- missions/
`-- reports/
```

The runner writes a `.codex/.gitignore` that excludes `corvee/reports/` the first time it creates a run directory, because checkpoints embed repository content. `TARGET.md` and `STATE.md` are meant to be reviewed and may be committed.

Create or update these files with normal Codex file-editing tools. Do not store API keys, authorization headers, full environment dumps, or secret-bearing provider configuration in them.

## Target contract

`TARGET.md` is the source of truth. Keep these fields concrete:

```markdown
# Target

## Outcome
One observable end state.

## Acceptance gates
- [ ] G1: `command that exits 0 when this gate passes`
- [ ] G2: `command that exits 0 when this gate passes`
- [ ] G3: condition that genuinely cannot be a command, and how to judge it

## Constraints
Authorized scope, compatibility requirements, and invariants.

## Non-goals
Explicit exclusions.

## Verification
How to run the gate commands, including any setup they need.

## Loop budget
Maximum iterations and optional time or cost boundary.
```

Write a gate as a command whenever one exists. A command costs the planner one exit code to check no matter how much work the delegate did, while a gate phrased as a judgement costs a full reading of the change. That difference is the whole economics of delegating: keep the planner's verification cost flat as the delegated volume grows. Reserve prose gates for what genuinely cannot be executed, such as an interface being coherent, and expect them to be the expensive ones.

`STATE.md` records the current status, next unmet gate, iterations, delegate reports, primary-Codex verification, blockers, and target changes. Append iteration evidence; do not rewrite failed history into a success narrative.

`DELEGATE.json` is the project-level model pin, distinct from the user-level default in `config.toml`, and stores only the selected non-secret model ID:

```json
{"model": "exact-provider-model-id"}
```

## Commands

### `configure`

Show current non-secret configuration and validate it, or configure Corvex from a safe credential source. Never request a key in chat. Use:

```bash
python3 <skill-dir>/scripts/corvee select
python3 <skill-dir>/scripts/corvee check
```

If `CORVEX_API_KEY` or an explicitly identified dotenv file is available, run `scripts/corvee configure` non-interactively. Otherwise direct the user to the local hidden-input wizard. Configuration is complete only after authenticated inference succeeds; the public catalog alone does not validate a key.

### `check`

Run `scripts/corvee check`. This makes a tiny billable inference request using the saved model, or the first catalog model solely for validation if no default is set. It does not change the model selection.

### `cleanup`

Remove stale run report directories with `scripts/corvee cleanup [--older-than-days N] [--dry-run]`. Reports accumulate one directory per run under `.codex/corvee/reports/` and hold repository content in `checkpoint.json`. Preview with `--dry-run` before deleting, and keep any run still referenced by an open gate in `STATE.md`.

### `target GOAL`

Turn the user's goal into `TARGET.md`. Inspect enough repository context to make the acceptance gates observable. Identify assumptions explicitly. If an active target exists, preserve its prior text in `STATE.md` before replacing or materially changing it.

Do not delegate `target`; Codex owns the target contract.

### `analyze`

Perform a read-only gap analysis against the current target:

- map present state to each acceptance gate;
- identify dependencies, risks, unknowns, and the critical path;
- propose the smallest independently verifiable work units;
- distinguish facts from hypotheses;
- update `STATE.md`, but do not edit product code.

Delegate the reading. A read-only delegate should gather the evidence and draft the work units; exploring a repository is the most token-heavy step in the loop and the one worth moving off the planner. The planner accepts, reorders or rejects that draft and owns the result, but should not re-read the repository to produce it.

### `refine`

Improve the target and route to it. Resolve vague gates, remove accidental scope, add missing invariants, split oversized work, and define stronger verification. Do not weaken a gate merely because a delegate failed it. Ask the user only when a choice materially changes scope or outcome.

### `run`

Execute exactly one controlled iteration:

1. select the highest-leverage unmet gate whose prerequisites are satisfied;
2. create one bounded mission;
3. run one delegate, or a small read-only fan-out for genuinely independent analysis;
4. rerun the gate command yourself and read its exit code;
5. read the change only to the extent the gate does not cover it. When the diff
   is larger than roughly a screen, delegate the reading (see `review` below)
   and read the reviewer's verdict instead;
6. update `STATE.md` and stop after reporting the next state.

Step 4 is not optional and is not delegable: the delegate's own claim that a
check passed is evidence, not proof. Step 5 is where planner cost is won or
lost. Reading a whole diff makes the planner's spend grow with the delegate's
output, which is the shape delegation exists to avoid.

Use a fresh direct-runner context for each mission. A failed or timed-out run may have left partial edits; inspect before retrying. Capture the report and exit status in `reports/`. Do not include credential values or environment dumps.

The runner creates a private per-run artifact directory automatically; use `--run-dir` to name a new directory explicitly. Read metadata in `events.jsonl` and `status.json` first. `checkpoint.json` preserves conversation/tool results and may contain private repository content. Inspect it locally as needed; do not paste it wholesale into chat or a new mission. Use `scripts/corvee run --resume <run-dir>` to continue from the latest checkpoint when safe, and keep `tool_pending` behavior conservative.

Request timeouts default to 600 seconds; retain that allowance for slow models unless the user requests less. The total run budget still wins. A transient request can retry once (up to two with `--request-retries 2`), with possible duplicate provider charges, but completed local tools are not rerun. Do not restart an entire mission merely because one inference request timed out.

The final step/time reserve requests a tools-disabled report. Repeated identical results or consecutive tool errors trigger early wrap-up. Exit `3` is an incomplete report, `75` is exhausted transient retries, and `124` is budget exhaustion. None closes an acceptance gate. Inspect diagnostics before increasing limits or changing model/scope.

### `loop [CONDITION] [budget]`

Repeat `run` until the explicit condition is independently verified. A budget written after the instruction (for example "at most 6 iterations or 120 minutes") is prose for the planner to honour, not a parsed flag; only the runner's own `--max-time` and `--max-steps` reach the CLI. If no condition is supplied, use all acceptance gates in `TARGET.md`.

Defaults are six iterations and 120 minutes for the current invocation unless `TARGET.md` specifies a lower budget. A user-supplied lower boundary wins. Never silently exceed an iteration, time, or stated cost budget.

Record the start time and completed iterations. Pass at most the remaining time to each runner through `--max-time` and stop launching work when it is exhausted.

The runner records what the provider reports: `status.json` carries a `usage` object with `requests`, `prompt_tokens`, `completion_tokens`, `total_tokens`, and `reported_by_provider`. Per-request counts appear on `request_end` in `events.jsonl`. No prices are bundled, so tokens do not convert to money here. If the user sets a monetary cap, translate it into iteration, step and time budgets before launching billable work, and report token totals from `status.json` rather than estimating them. When `reported_by_provider` is false the provider returned no counts: say usage is unmeasured, never that it was zero.

After each iteration, reconsider the next unit from current evidence. Do not replay an identical failed mission. Stop and report clearly when any of these occurs:

- every required gate passes independent Codex verification;
- the budget is exhausted;
- the same external blocker occurs three consecutive times despite materially different safe attempts;
- progress requires a user decision, new authorization, credentials, production access, or expanded scope;
- continuing risks data loss, security exposure, or unrelated user work.

A timeout is not success. Partial progress remains recorded and the target stays incomplete.

### `review`

Delegate the reading of a change. Run a fresh read-only mission whose scope is
the current diff and whose objective is to report, per hunk: what changed,
whether it is inside the mission's authorised scope, anything unrelated that
was touched, and any defect it can support with evidence. It receives the
target and the diff but not the implementing delegate's report or conclusions.

The planner then reads a verdict of a few hundred bytes rather than the diff
itself, and still reruns the gate commands. Use it whenever the diff is large
enough that reading it would dominate the iteration's planner cost. Do not use
it to replace step 4, and do not treat a clean review as a passing gate.

### `audit`

Try to falsify the current completion claim. Prefer a fresh read-only delegate mission that receives the target and artifacts but not the previous delegate's conclusions. The primary Codex agent reruns the gate commands and reads the falsification report, not the whole change. Reopen any gate contradicted by stronger evidence.

### `status`

Report the outcome, gate checklist, latest verified evidence, current blocker, budget consumed, and next recommended action. Budget consumed means iterations and wall-clock time, plus the token totals in `status.json` when the provider reported them. Do not estimate token counts or costs that were not measured. Do not invoke a delegate unless fresh evidence is required and the user also requested progress.

### `models [PATTERN]`

List provider model IDs directly using `scripts/corvee models [PATTERN]`. This is discovery only; do not infer quality from a model name.

### `select [MODEL_ID|auto]`

With no argument, use `scripts/corvee select`. With an exact ID, run `scripts/corvee select MODEL_ID`; it fetches the catalog, requires an exact match, and saves the selection. With `auto`, clear the user default; a mission still needs a model through a command argument, project file, or environment override.

Do not silently fuzzy-select or substitute a model. A project `DELEGATE.json` overrides the user default through `--model-config`; an explicit `--model` overrides both without changing stored selections.

## Completion language

Use `complete` only when every required gate has current, independently inspectable evidence. Otherwise use `in progress`, `blocked`, or `budget exhausted`, and name the unmet gates.
