# Route work by acceptance cost

Use this when choosing a work package or preparing a handoff. This policy is
informed by the [measured trials](../COST_ASSESSMENT.md), not a proven optimum.
The successful synthetic migration cost $0.190 versus $0.452 direct; a larger
implementation was more expensive and incorrect. Size alone is not a selector.

## Default admission

Before ordinary delegation, identify the actual outcome, bounded editable scope,
existing acceptance checks, and why execution is likely to outweigh coordination.
These are dispatch facts, not a new planner report. If a fact is missing, keep
work direct or resolve it through an explicitly requested bounded discovery or
experiment. Do not manufacture extra work to make a small task look delegatable.

Use one fixed attempt budget. Reaching it does not authorize another model sweep,
a larger cap, or an Astra rewrite. Preserve the failed cost and present the
specific remaining decision. If no representative real task is available, ask
for one and defer paid comparison rather than repeating synthetic evidence.

## Choose the lane before preparing a detailed mission

| Situation | Route | Acceptance |
|---|---|---|
| Small known fix, or an existing deterministic command already solves it | Direct Codex or that command | Existing focused check; avoid a worker planning/review round trip |
| Many related edits under one settled contract, with meaningful existing checks | One bounded `job` if its source-packet limits fit | Controller runs protected checks, routes repairs, and returns evidence |
| Unknown entry points or a package too large to specify cheaply | Bounded read-only `run` discovery | File/symbol map, proposed editable scope, existing checks and gaps; Codex resolves only the remaining decisions |
| New semantics, weak tests, architectural ambiguity | Codex settles the contract and verification approach first | Delegate execution only once acceptance is reviewable; retain direct work if designing checks would cost as much as solving it |
| Adversarial exploration | Explicit bounded experiment, not a default savings route | One named invariant family and a small executable reproducer deliverable; confirm and deduplicate failures against the contract |

Explicit user model, review and delegation requirements take precedence. Do not
use a universal file count, line count or 40K-token threshold. Favor a batch with
one contract and one gate over unrelated tasks that need separate reviews.

## Keep Astra out of routine execution

Reuse the user's task. Add the editable scope, compatibility constraints, fixed
checks and stopping budget; do not first read every file or write the solution.
If those are already clear, dispatch from the existing session without an extra
Astra planning call. Cheap discovery should return a map, not another full source
dump for Astra to process.

Use `job` for autonomous gate/repair transitions. Its controller, not the model,
executes the declared argv. The `run` tool loop cannot execute tests itself:
`request_command` suspends for the parent. Do not promise unattended verification
for a `run` mission. See [job-workflow.md](job-workflow.md) for executable scope,
packet limits and the separate cheap review that every successful job includes.

Return the compact result first. Read relevant gate/review evidence as needed,
not all provider traces. A controller-run gate need not be run again solely to
move execution into an Astra turn, provided it covers the accepted contract and
still describes the unchanged candidate. Review uncovered requirements, stale
results, conflicting evidence and user-required checks explicitly. A passing
weak gate plus a model's approval is insufficient: this failed in the queue trial.
Do not silently repair failed work with Astra and still count it as cheap execution.

## Bound the total attempt, not just each call

For current `job`, at most `2 * (max_repairs + 1)` provider calls can occur,
including cheap reviews. `--max-time` is integer seconds **per provider call**;
`--gate-timeout` is per gate. There is no total-dollar or whole-job wall-time flag.
Select all allowances together, and use a caller deadline if a hard whole-job
limit is required. Do not reset the budget by launching another job on escalation.

Use thinking for correctness work with enough room for the deliverable. Instant
mode is an option for mechanical transforms, not a measured winner in the latest
paired trial. In the adversarial trial, 32,768 output tokens were entirely consumed
by reasoning. Narrow the next authorized mission rather than automatically
increasing its cap or interpreting no output as no defects. Requesting an early
reproducer is prompting guidance, not a guaranteed reservation of output tokens.

## Compare accepted outcomes

Use comparable prior measurements when available:

`delegation = Astra instruction + all worker calls + Astra acceptance + escalation/fallback`

Compare this with direct Astra execution at the same acceptance standard. Include
fixture/gate creation when it is new task work, source-packet preparation, and
actual cache usage; a reusable benchmark setup exclusion is not free production
work. Unknown usage is unknown cost. Failed attempts belong in the total.

The worker budget available before break-even is direct cost minus estimated
Astra overhead and fallback allowance. If that leaves little room, use direct
work or a better batch. Where failure rates are measured, include their expected
fallback cost; where they are not, label the route an experiment rather than
inventing a success probability. Keep latency and correctness beside dollar cost.
A lower bill for an incomplete result is not a saving.

## Compact handoff

Reuse these fields in a mission; they are guidance, not new CLI arguments:

- Outcome and compatibility contract: refer to the existing task.
- Scope and entry points: editable files; protected fixtures; known prior edits.
- Acceptance: fixed argv, what it proves, and requirements still needing review.
- Budget: model/thinking, input/output limits, per-call time, gate time, repairs.
- Escalation: the decision the worker must stop for; no scope or gate weakening.

`job` already supplies source packets and imposes its edit/review JSON schemas;
do not ask it for a conflicting free-form report. `run` can return a short map or
patch report but does not provide controller-verified acceptance on its own.

## Git and the command executor

The `job` lane needs no Git tools: it snapshots declared files and builds diffs
in Python. `run` retains the `git_status`/`git_diff` names for checkpoint
compatibility but sends fixed operations and `diff_bytes` accounting through the
read-only Codex executor. This is not a generic worker shell. Set `--codex-bin`
when needed and repeat it on resume; no unsandboxed fallback exists.

Keep Git only where repository status/history is actually needed; use file
snapshots for bounded patch acceptance. Replacing an operation's implementation
with command/exec removes no Corvée reasoning or tool-result tokens. The executor
itself creates no model turn, while dispatching/reporting through Codex chat
still consumes tokens. Its sandbox constrains execution; it does not make every
repository command harmless or authorized. Non-Git file tools and provider calls
remain outside that sandbox.

## Deterministic cleanup before another reasoning call

The real refactor pilot initially failed lint on one unused import. A post-hoc
Ruff fix and fresh verification produced an accepted result without another
worker call. For a future task, consider declaring a standard formatter or narrow
lint autofix in the authorized workflow up front. Confine it to editable files,
check its actual diff, and rerun affected acceptance checks. Do not alter tests,
contracts, or lint configuration to make a candidate pass. This is an exploratory
workflow improvement; the pilot excluded task-preparation and root-review cost.
