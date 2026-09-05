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

Create or update these files with normal Codex file-editing tools. Do not store API keys, authorization headers, full environment dumps, or secret-bearing provider configuration in them.

## Target contract

`TARGET.md` is the source of truth. Keep these fields concrete:

```markdown
# Target

## Outcome
One observable end state.

## Acceptance gates
- [ ] G1: binary pass/fail condition and its evidence source
- [ ] G2: binary pass/fail condition and its evidence source

## Constraints
Authorized scope, compatibility requirements, and invariants.

## Non-goals
Explicit exclusions.

## Verification
Commands, inspections, or artifacts Codex can independently evaluate.

## Loop budget
Maximum iterations and optional time or cost boundary.
```

`STATE.md` records the current status, next unmet gate, iterations, delegate reports, primary-Codex verification, blockers, and target changes. Append iteration evidence; do not rewrite failed history into a success narrative.

`DELEGATE.json` stores only the selected non-secret model ID:

```json
{"model": "exact-provider-model-id"}
```

## Commands

### `configure`

Show current non-secret configuration and validate it, or configure Corvex from a safe credential source. Never request a key in chat. Use:

```bash
python3 <skill-dir>/scripts/corvee show
python3 <skill-dir>/scripts/corvee check
```

If `CORVEX_API_KEY` or an explicitly identified dotenv file is available, run `scripts/corvee configure` non-interactively. Otherwise direct the user to the local hidden-input wizard. Configuration is complete only after authenticated inference succeeds; the public catalog alone does not validate a key.

### `check`

Run `scripts/corvee check`. This makes a tiny billable inference request using the saved model, or the first catalog model solely for validation if no default is set. It does not change the model selection.

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

A read-only delegate may gather repository evidence. The primary Codex agent must synthesize the plan.

### `refine`

Improve the target and route to it. Resolve vague gates, remove accidental scope, add missing invariants, split oversized work, and define stronger verification. Do not weaken a gate merely because a delegate failed it. Ask the user only when a choice materially changes scope or outcome.

### `run`

Execute exactly one controlled iteration:

1. select the highest-leverage unmet gate whose prerequisites are satisfied;
2. create one bounded mission;
3. run one delegate, or a small read-only fan-out for genuinely independent analysis;
4. inspect the report and diff;
5. independently verify the affected gate;
6. update `STATE.md` and stop after reporting the next state.

Use a fresh direct-runner context for each mission. A failed or timed-out run may have left partial edits; inspect before retrying. Capture the report and exit status in `reports/`. Do not include credential values or environment dumps.

The runner creates a private per-run artifact directory automatically; use `--run-dir` to name a new directory explicitly. Read metadata in `events.jsonl` and `status.json` first. `checkpoint.json` preserves conversation/tool results and may contain private repository content. Inspect it locally as needed; do not paste it wholesale into chat or a new mission. Use `scripts/corvee run --resume <run-dir>` to continue from the latest checkpoint when safe, and keep `tool_pending` behavior conservative.

Request timeouts default to 600 seconds; retain that allowance for slow models unless the user requests less. The total run budget still wins. A transient request can retry once (up to two with `--request-retries 2`), with possible duplicate provider charges, but completed local tools are not rerun. Do not restart an entire mission merely because one inference request timed out.

The final step/time reserve requests a tools-disabled report. Repeated identical results or consecutive tool errors trigger early wrap-up. Exit `3` is an incomplete report, `75` is exhausted transient retries, and `124` is budget exhaustion. None closes an acceptance gate. Inspect diagnostics before increasing limits or changing model/scope.

### `loop [CONDITION] [--max-iterations N] [--max-time DURATION]`

Repeat `run` until the explicit condition is independently verified. If no condition is supplied, use all acceptance gates in `TARGET.md`.

Defaults are six iterations and 120 minutes for the current invocation unless `TARGET.md` specifies a lower budget. A user-supplied lower boundary wins. Never silently exceed an iteration, time, or stated cost budget.

Record the start time and completed iterations. Pass at most the remaining time to each runner through `--max-time` and stop launching work when it is exhausted. No token-price estimator is bundled: if the user supplies a monetary cap, establish a measurable usage limit before launching billable work.

After each iteration, reconsider the next unit from current evidence. Do not replay an identical failed mission. Stop and report clearly when any of these occurs:

- every required gate passes independent Codex verification;
- the budget is exhausted;
- the same external blocker occurs three consecutive times despite materially different safe attempts;
- progress requires a user decision, new authorization, credentials, production access, or expanded scope;
- continuing risks data loss, security exposure, or unrelated user work.

A timeout is not success. Partial progress remains recorded and the target stays incomplete.

### `audit`

Try to falsify the current completion claim. Prefer a fresh read-only delegate mission that receives the target and artifacts but not the previous delegate's conclusions. The primary Codex agent reviews the result and runs decisive checks. Reopen any gate contradicted by stronger evidence.

### `status`

Report the outcome, gate checklist, latest verified evidence, current blocker, budget consumed, and next recommended action. Do not invoke a delegate unless fresh evidence is required and the user also requested progress.

### `models [PATTERN]`

List provider model IDs directly using `scripts/corvee models [PATTERN]`. This is discovery only; do not infer quality from a model name.

### `select [MODEL_ID|auto]`

With no argument, use `scripts/corvee select`. With an exact ID, run `scripts/corvee select MODEL_ID`; it fetches the catalog, requires an exact match, and saves the selection. With `auto`, clear the user default; a mission still needs a model through a command argument, project file, or environment override.

Do not silently fuzzy-select or substitute a model. A project `DELEGATE.json` overrides the user default through `--model-config`; an explicit `--model` overrides both without changing stored selections.

## Completion language

Use `complete` only when every required gate has current, independently inspectable evidence. Otherwise use `in progress`, `blocked`, or `budget exhausted`, and name the unmet gates.
