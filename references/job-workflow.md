# Corvée jobs: Astra sets direction, scripts coordinate execution

Experimental: the first integrated live measurement failed its task; the
controller passed local tests, but global savings and successful live cheap
review remain unproven. See [the cost assessment](../COST_ASSESSMENT.md).

Use this lane for bounded work with a clear contract and an existing executable
acceptance gate. Astra resolves architecture, compatibility requirements and
ambiguous tradeoffs once. Corvée chooses implementation details. A deterministic
controller runs the gate, routes diagnostics, and commissions a fresh cheap
review. None of those transitions calls Astra.

## Run

Use an isolated, trusted checkout. Supplying `--gate-json` authorizes execution
of exactly that argv in the checkout, including code it imports. It is not a
sandbox and it must not point at a deployment or destructive operational command.
Worker-requested commands are never executed by this controller.

```bash
python3 scripts/corvee job \
  --cwd /absolute/path/to/isolated-checkout \
  --mission /absolute/path/to/task.md \
  --model moonshotai/Kimi-K2.7-Code --thinking enabled \
  --gate-json '["python3", "gate.py"]' \
  --scope-file src/parser.py --protected-file gate.py \
  --max-repairs 1 --max-input-bytes 60000 \
  --max-output-tokens 32768 --max-time 300
```

For substantive implementation/review, prefer thinking enabled and an explicit
output/time budget. The example budgets are a starting point, not a proven
successful configuration. Instant mode remains available for mechanical edits.
The CLI does not infer a thinking budget: explicitly pass these limits; its
older 8,192-token/120-second defaults can truncate reasoning workloads.

To use Codex's sandbox for the gate, add:

```bash
--executor codex --codex-bin /absolute/path/to/codex
```

The adapter sends only `initialize`, `initialized`, and `command/exec` over a
private stdio connection to `codex app-server`. No thread/turn is created, so no
Astra or other model is invoked for command execution. The command uses
`workspaceWrite` with network disabled, a caller timeout, and 32 KiB capture per
stdout/stderr stream. Sandbox rejection or adapter error escalates; there is no
local fallback. The default `--executor local` retains ordinary subprocess
execution. Neither mode permits model-selected commands: only `--gate-json`.
The outer Corvex calls run in the controller, outside the gate's network sandbox.

[Official command execution API](https://learn.chatgpt.com/docs/app-server#command-execution)
documents this model-free execution primitive. Hooks are unnecessary here;
they can automate lifecycle actions but are not the worker's reasoning loop.

List immutable test fixtures with repeated `--protected-file`. The controller
checks them before executing the gate and after workers run. Protection is a
change detector, not an OS boundary; imported code and dependencies remain
executable. This job command performs no Git inspection. The existing `run` command still
has its documented Git-helper limitation. Never use an untrusted repository
as an execution sandbox.

List the exact existing UTF-8 files to edit with repeated `--scope-file`. The
controller sends their complete contents directly to Corvée and applies only
validated, uniquely matching replacements in those files. It supplies no tools.
It rejects oversized input packets rather than omitting source. This bounded
lane does not create/delete files or explore an unknown repository. Use a cheap
read-only discovery mission first when entry points are unknown, then select a
coherent bounded edit; do not make Astra read the repository to assemble it.

The mission should state the outcome, editable scope, compatibility invariants,
and escalation questions. Include known entry points when available. Let the
worker inspect the implementation and derive its own plan; do not pay Astra to
produce a line-by-line recipe. Prefer coherent work batches with one acceptance
contract over many tiny subtasks with separate planner/reviewer sessions.

## Automatic loop

1. Corvée returns exact replacement edits from the source packet in one call.
2. The controller runs the caller's gate. Failures become bounded diagnostics.
3. A fresh Corvée call repairs current code with the original contract and those
   diagnostics. It does not replay the previous worker's entire conversation.
4. After tests pass, a fresh Corvée reviewer receives baseline/current source and the actual diff,
   checking correctness and scope, including cases tests may miss. It returns strict JSON:
   `{"accepted": true, "defects": []}` or a rejection with concrete defects.
5. Review defects go back to a fresh implementation run within the same repair
   allowance. Final integrity checks precede `ready`; the tested code must remain unchanged.

Malformed edits/review, provider failure, exhausted
repairs, oversized packets, or changed protected files produce `escalated`. A truncated provider
response never becomes `ready`. The CLI prints only status, model, executor, aggregate usage and report path.
Review findings, bounded last diagnostics, phase logs and provider token totals are retained in
`job/result.json` under the printed unique artifact directory. All attempts
count. Per-call time/input/output limits and the repair count bound execution;
they are not a dollar cap. Do not launch more jobs automatically on escalation.

## What reaches Astra

For routine tasks, inspect the compact result and let the user see the ready
patch. Do not automatically launch an Astra code-review session, re-read all
worker traces, or narrate each internal transition. `ready` means automated
tests and cheap review passed; it does not merge, publish, or guarantee correctness.

Escalate an actual question with the relevant code excerpt, evidence, options,
and proposed decision: changed public contracts, architectural choices,
security-sensitive behavior, migrations, disputed correctness, or repeated
failures. Astra can also review a batch when risk warrants it. Cheap independent
review is another perspective, not evidence of model diversity or a substitute
for required specialist assurance. The user can always require Astra review.

## Evidence boundary

This controller removes dedicated Astra inference from the routine loop by
construction. Starting and reporting a job through an Astra chat still uses
Astra; invoking the CLI directly does not. No measured global saving is claimed
until matched accepted-task evaluation includes this controller, worker review,
all retries, and any escalated Astra work. The preceding corrected workflow
lost money partly because repairs replayed history and Astra reviewed twice.
Fresh repair contexts and cheap review address those costs but must still prove
first-pass quality and acceptance rate.
