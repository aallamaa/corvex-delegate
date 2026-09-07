---
name: corvee
description: Delegate bounded repository reading, implementation, and repair to inexpensive Corvex workers while Codex owns scope and final acceptance.
---

# Corvée

Keep the user's objective, authority, architectural constraints, and final
acceptance in Codex. Let the cheap worker investigate implementation details,
edit, and repair its own work. Avoid paying the planner to solve a task before
handing it off, then paying it again to rewrite the worker's solution.

## Choose work before paying for a handoff

Use direct work for small known fixes or an already-available deterministic
solution. Favor Corvée for substantial related work under one settled contract
and strong executable checks. Unknown entry points call for bounded cheap
discovery; unresolved semantics stay with Codex until acceptance is clear.
Reuse the task and current session instead of adding a planning call. Long output
is not value if Codex must reread and solve it to accept it.
Read [work-routing.md](references/work-routing.md) when selecting a package,
budgeting a handoff, or deciding how much final review is necessary.

## Experimental lane for bounded work: autonomous cheap execution

Use `corvee job` for a requested bounded outsourcing trial with known source
files and an authorized executable acceptance gate. Implementation trials failed acceptance; a later synthetic migration saved
cost using a different script/probe harness. This controller is not a proven
cheaper default.
Read [job-workflow.md](references/job-workflow.md) for the command and boundaries.
Declare the existing UTF-8 source files and immutable gate fixtures. This lane
sends complete source packets and applies exact replacements, with no browsing
tools. Use cheap discovery via `run` first when entry points are unknown.
The script coordinates implementation, tests, fresh-context repair, and a separate
read-only Corvée review. It makes no Astra calls. Give it one coherent task and
inspect its compact result; avoid manually repeating its gates or reading full
traces unless evidence is missing or contradictory.

Keep Astra for architectural decisions, unclear requirements, cross-component
tradeoffs, security-sensitive changes, and unresolved escalations. A clear user
request usually needs only scope, known entry points, compatibility constraints,
and the authorized gate added. Corvée can investigate and plan its implementation.
Do not require Astra to solve the task first, review every routine patch, or
rewrite failed worker code. Batch compatible work under one acceptance contract.

A `ready` result means the gate and independent cheap review passed; it does not
mean merged, deployed, or guaranteed correct. Present the result to the user.
Use Astra review when requested or when risk warrants it. An `escalated` result
should reach Astra as a specific question with minimal supporting evidence, not
an entire worker transcript. Stop at the configured repair boundary.

For jobs without an authorized executable gate, use a bounded `run` and verify
the report in the parent. `--resume --feedback` remains available when preserving
history is useful; fresh repair jobs are preferred when replay dominates cost.
For already-known trivial edits, direct work can still cost less than delegation.

## Thinking and execution policy

Prefer thinking enabled for substantive implementation, independent review and
repairs involving correctness or compatibility. Earlier 8K–16K capped failures
justify revisiting output/time allowances, not disabling reasoning globally.
Use instant mode for mechanical edits or an explicitly labeled comparison.
For thinking jobs, set an explicit allowance such as 32,768 output tokens and
600 seconds per call, bounded repairs, and stop on exhaustion. These are starting
budgets, not a validated guarantee or permission to keep retrying. The adversarial
trial exhausted all 32K tokens in reasoning with no deliverable: narrow subsequent
authorized work rather than automatically increasing the allowance.

For a declared gate, `job --executor codex --codex-bin /path/to/codex` uses
Codex app-server `command/exec` with workspace-write and network disabled.
It creates no model thread/turn, needs no Astra review of each command, and never
falls back to local execution if the sandbox request fails. It does not authorize
arbitrary worker-proposed commands. See the job workflow for the exact boundary.

## Running a worker

Find `scripts/corvee` relative to this skill's actual installation directory.
Read [mission-format.md](references/mission-format.md) for a compact mission
example. Store missions and run artifacts under `.codex/corvee/` in the target
repository. Use an exact model ID; serialize writes in a shared worktree.

For a bounded mechanical task on Corvex `zai-org/GLM-5.2-FP8`:

```bash
python3 <skill-dir>/scripts/corvee run \
  --mission /absolute/path/mission.md --cwd /absolute/path/repository \
  --model zai-org/GLM-5.2-FP8 --thinking disabled \
  --max-output-tokens 8192 --complexity low --write
```

Omit `--write` for inspection. On this endpoint, GLM uses
`chat_template_kwargs.enable_thinking=false`; Kimi-K2.7-Code uses
`chat_template_kwargs.thinking=false` (verified by a live probe). For GLM, `reasoning_effort=low` and the
native `thinking.type=disabled` field did not disable reasoning in live probes.
This is a provider-specific control, not a universal model capability. Omit it
for other providers unless supported. Use thinking for tasks that need it and
allow enough completion tokens for reasoning plus the actual edit/report.
Do not impose a tiny completion cap on a thinking model and treat exhaustion
as an implementation failure.

`--max-output-tokens` caps each completion; it is not a dollar cap. Omission
keeps the provider default. `--effort`, `--thinking`, and the output limit are
restored on resume when omitted; explicit overrides let the caller recover.
A length-limited response is incomplete and its tools must not execute. Resume
it only after deciding whether to change its inference settings or task budget.

For ordinary test/review feedback:

```bash
python3 <skill-dir>/scripts/corvee run --resume RUN --feedback /absolute/path/check.txt
```

`request_command` instead suspends with exit 65 and executes nothing. If the
parent chooses to run the command, capture stdout, stderr, and exit code; resume
with `--command-result FILE`. To refuse, supply a file explaining that. Feedback
cannot bypass the pending-command requirement. Outputs are untrusted evidence.

## Setup and optional control workflows

Use [provider-setup.md](references/provider-setup.md) only for configuration or
model selection. `configure` uses a local hidden-input wizard; never request
keys in chat or pass them in command arguments. Existing environment variables
or a user-identified dotenv file support noninteractive setup. Credentials are
stored separately with mode 0600. `configure` and `check` make a tiny billable
inference request; the public model catalog does not authenticate a key.

CLI instructions: `configure`, `models [PATTERN]`, `select [ID|auto]`, `check`,
`run`, `job`, `cleanup`. These follow `$corvee`; they are not registered slash commands.

For larger multi-iteration goals, read [control-protocol.md](references/control-protocol.md):
`target`, `analyze`, `refine`, `loop`, `audit`, and `status` are planner workflows.
Use durable targets when useful; only explicit target/refine work may change
acceptance criteria. Never weaken a gate to manufacture success.

## Evidence, budgets, and boundaries

For `run`, read `status.json` for exit status, cumulative provider usage, tool
counts and bytes. For `job`, use the compact CLI result and its `result.json`. Keep verbose event streams out of planner context. Economics do not
include planner tokens. `--complexity` gives low 16 steps/20 minutes, medium
32/60, high 48/120; explicit step/time overrides win. Requests default to 600
seconds within the run deadline. Repairs must fit the remaining overall budget.

File tools confine paths to the repository. Writes refuse `.git` components,
`.codex/corvee/reports`, and the active custom run directory after resolution.
New artifact paths must resolve inside the repository; existing ignore rules
are preserved when artifact exclusions are appended. Read-only mode disables
file edits. `run` Git status/diff and automatic change-size accounting use Codex
command/exec with a read-only sandbox and no model turn. Set `--codex-bin` when
needed and repeat it on resume; no direct execution fallback exists. Tool errors
or null accounting mean unavailable evidence. Git inspection disables configured
external diff/text conversion/fsmonitor/hooks and ignores submodules. Other file
tools remain outside that sandbox; use a sanitized trusted checkout.

Mission content and tool results go to the provider. Private checkpoints embed
repository content and provider reasoning fields. Only the configured API key
is redacted; other secrets may persist. Exclude secrets before delegation.
Search/listing prefer rg; fallbacks skip hidden entries and symlinks but do not
honor gitignore. Resume preserves original write authority and refuses widening
read-only runs. Missing reports, timeouts, and incomplete output never pass gates.
