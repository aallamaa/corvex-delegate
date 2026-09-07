# Corvée: evidence for savings

Reviewed: 2026-09-07. This supersedes the earlier claimed 40–60K-token
break-even and medium/large-task winner table. Those conclusions were not
established by matched, completed-task measurements.

Latest matched evaluation: a four-module SQLite queue cost **$0.634304 direct**
with accepted code, versus **$0.662880 delegated** with a rejected result.
Astra instruction/review overhead was still $0.539676. See the larger-package
measurement at the end for the complete method and symmetric correctness checks.

## What the evidence supports

The historical same-model harness comparison used one read-only tool-inventory
mission, GLM-5.2-FP8, and two trials per harness. Recorded usage yields:

| Harness | Mean prompt tokens | Mean completion tokens | Mean cost | Mean seconds |
| --- | ---: | ---: | ---: | ---: |
| Corvée (historical baseline) | 49,252 | 1,499.5 | $0.040538 | 12.5 |
| codex exec on Corvex | 96,559 | 3,078.5 | $0.079808 | 25 |

Prices used: $0.75/M prompt and $2.40/M completion. This is a 49.2% reduction
in model cost for that harness comparison, about $0.0393 per mission.
Historical notes say both answers were correct; no fresh quality grading or
paired live rerun is implied. The source is `.codex/bench/RESULTS.md`, with
local reports under `.codex/bench/`; these artifacts are excluded from releases.

A later eight-trial Corvée optimization averaged $0.030552. Comparing that
with the older two-trial codex sample produces the README's roughly 2.6x
ratio, but it is not a contemporaneous paired experiment.

## Why general end-to-end savings remain unproven

- The old direct-Astra and planner-overhead columns were not backed by matched
  direct-task and planner billing traces in the assessment.
- The saved SSBC CANCEL and Alcove HAMT runs both ended with exit 3,
  `incomplete`. They produced useful partial work but cannot establish cost
  per accepted completion. Retrying and repairing must be included.
- The prior resume accounting bug could overwrite cumulative status counters.
  P2 is fixed now; that does not retroactively reconstruct old totals. Event
  traces are needed before relying on a resumed historical status total.
- Repeated prompt tokens are not unique source tokens or context-window
  occupancy. Pruning can cause rereading, and a compact report may omit evidence
  that the planner must retrieve again.
- Planner specification and verification costs are task-dependent, not fixed.
  Tests do not eliminate the need to review scope, defects, or security.

## Pricing correction

The previous assessment used $12.50/M as Astra's output price. The official
model page checked on 2026-09-07 lists standard rates of $10/M input, $1/M
cached input, $12.50/M cache writes, and **$50/M output**. It also lists rate
multipliers for long prompts and Fast mode. Source:
[OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-6-astra).
Recompute both planner and direct arms from actual usage and service tier.
These are API-equivalent costs, not necessarily the user's subscription bill.

The public Corvex catalog checked on the same date lists Kimi-K2.7-Code at
$0.55/M prompt and $2.20/M completion and GLM-5.2-FP8 at $0.75/M and $2.40/M.
Their cache-read rates equal prompt rates. Source:
[Corvex model catalog](https://api.tokenfactory.corvex.cloud/v1/models).
The Kimi description labels its pricing provisional.
For cached Astra input alone, $1/M is about 1.3–1.8x these delegate input
rates, not the previously claimed 5x.

## Decision rule

Keep Corvée optional. Delegate when specification, execution, and independent
verification are expected to cost less than direct completion. Favor bounded
inventories, focused reviews, and mechanical changes with reliable gates.
Do not use a fixed repository-size or 40K-token threshold.

## Small validation protocol

Use the same clean snapshot and acceptance gates for each arm:

1. Direct Astra.
2. Astra planning/review plus Corvée on a pinned Corvex model.
3. Astra planning/review plus a general-purpose harness on the same Corvex model.

Start with one bounded inventory task and one mechanical edit with executable
acceptance checks. Treat a single trial as a pilot, not a general benchmark.
Keep failures, retries, elapsed time, planner involvement, and all token charges
in the results. Grade outputs before ranking costs. Report unavailable planner
usage as unknown. Never price an incomplete result as a completed win.

No new Astra baseline should run without a small explicit spending allowance.
A run's step/time limits are not a monetary cap: bound input and output per
request or use provider-enforced limits before promising a dollar ceiling.

## Matched Astra pilot — 2026-09-07

One fresh trial per arm used byte-identical copies of the current runner and
an identical 729-byte mission: inspect the resume-accounting contract and
return five JSON fields. Before inference, expected.json fixed the accepted
answer. Both answers matched it exactly and both source copies stayed unchanged.

| Arm | Input tokens | Cached input (included) | Output tokens | Cost | Accepted |
| --- | ---: | ---: | ---: | ---: | --- |
| Codex CLI, GPT-6 Astra, low effort, default tier | 38,683 | 21,888 | 123 | $0.195988 API-equivalent | yes |
| Corvée, GLM-5.2-FP8 | 6,373 | no cache discount | 399 | $0.00573735 catalog cost | yes |

Astra performed one read command; Corvée used two tools across three model
requests. Corvée's output was capped at 2,048 tokens/request, with six steps,
60 seconds, no request retries, and a conservative request-byte allowance.
Astra ran through the existing signed-in CLI: its dollar figure translates
observed tokens to standard API rates, not a claim about subscription billing.
The CLI's rollout budget is guidance, not a strict monetary cap.

Worker cost was **34.16x lower**, a difference of **$0.190251**. Any additional
Astra specification/review/repair cost must fit below that difference for this
particular delegation to save overall. Planner usage and benchmark setup cost
were not measured, so this is not proof of end-to-end workflow savings.
It is one small source-inventory task with a cheap deterministic gate; it does
not validate implementation quality, large-task savings, or a 40K threshold.
Different models and harnesses were used intentionally to compare direct Astra
with the Corvée worker. No separate Astra planning/review arm was billed.

Private local evidence: `.codex/bench/astra-pilot-20260907/` contains mission,
expected answer, source copies, raw model usage, answers, and `result.json`.
Source SHA-256: `5af13c64f0e6ddb3e2f20d3b707f44aa9af4fa8b395b7b4c0f763711ff460968`.
Two CLI configuration attempts failed locally before the successful inference
run. The executable was found outside PATH under the local npm installation;
the earlier PATH lookup did not mean it was absent from the machine.

## Current validation status

P2 is present; 155 regression tests and Ruff passed on 2026-09-07.
The historical benchmark's outside-repository artifact path was repaired locally.
The fresh inventory pilot is complete. The full-cost write pilot below adds
measured planning and review. Repeated trials and a fresh third harness arm
remain unperformed to conserve the user's GPT credit.

## Full planning, execution, and review pilot — 2026-09-07

This extends the inventory pilot with measured Astra planning and review on a
small write task. Two isolated, initially identical copies of
`corvee_config.py` were asked to support compound durations (e.g. `1h30m`),
preserve existing behavior, and reject malformed and out-of-range inputs.
A fixed `gate.py` covered valid cases, invalid cases, and combinatorial checks.
Both final implementations passed it independently and passed scope review.
The production source file was not changed by this experiment.

### Actual measured phases

| Phase | API-equivalent/catalog USD | Seconds |
| --- | ---: | ---: |
| Direct Astra: implement, inspect diff, run gate | **0.384058** | **49.39** |
| Delegation: Astra mission planning | 0.187194 | 26.79 |
| Corvée worker, initial attempt | 0.019442 | 17.79 |
| Astra first review | 0.079622 | 20.35 |
| Corvée worker resume, incremental cost | 0.026748 | 42.14 |
| Astra final review and implementation repair | 0.188086 | 47.60 |
| **Delegation total to accepted completion** | **0.501092** | **154.67** |

Delegation cost **30.5% more** on this capped trial. Planning was $0.187194;
review/repair totaled $0.267708; both worker attempts together were $0.0461895.
Thus Astra coordination and fallback implementation comprised 90.8% of the
full delegated workflow's measured model cost. Worker resume totals were
cumulative; only the increment was added, avoiding double-counting P2 counters.
Dedicated benchmark runs totaled **$0.8851495 API-equivalent/catalog cost**.

### What happened, and why the limitations matter

The worker first received a 3,072-token completion cap, then a single resume
with 8,192. Both responses consumed the full cap without yielding a usable
edit or report. This is a budget-constrained failed-worker/fallback path, not
a measurement of normal successful GLM implementation. The first Astra review
found the missing implementation, but the CLI resume defaulted to read-only
and could not make the permitted repair. The final resume explicitly set
workspace-write; Astra implemented the change and passed the gate. All failed
attempt and review costs remain included. The final work was written by Astra.
The original plan was to stop on rejection; the pilot was extended by one
worker resume and final bounded repair to obtain an accepted outcome.

These two harness settings materially affect interpretation. This result does
not establish that a correctly configured, successful delegation would lose.
It does establish the cost of this observed full attempt, including recovery,
and shows why the earlier worker-only discount was not an end-to-end saving.
The direct and delegated arms had the same user task and gate. The planner
chose a narrower implementation scope (function/docstring only); direct Astra
also added a necessary standard-library `re` import, which is within the user
scope. Independent review confirmed all unrelated module definitions unchanged.

### Accounting and cache sensitivity

Astra used the same model (`gpt-6-astra`), low effort, and default service tier
for direct, planning, and review phases. Planning and review shared a persisted
session. Token-event inspection confirmed that CLI usage counters reset per
resumed turn; their phase totals can be added. Actual cached input is charged
at $1/M, ordinary input at $10/M, output at $50/M, and cache writes at $12.50/M.
No cache-write tokens were reported. Corvex GLM rates were $0.75/M input and
$2.40/M output. These are API-equivalent/catalog figures, not subscription
quota percentages or an invoice. Benchmark setup and this parent conversation
are not metered; dedicated task planning and review are metered.

As an arithmetic sensitivity check, pricing all observed Astra input as
uncached yields $0.736570 direct and $1.980260 delegated. This is not another
experiment or a prediction of cold-run behavior. It shows that the observed
cache reuse favored the multi-phase delegation workflow rather than penalizing
it. Recorded phase durations exclude orchestration gaps and benchmark setup.

Evidence and rerun helpers are local at `.codex/bench/full-cost-20260907/`:
`eval.py`, `worker.py`, `score.py`, fixed task/gate/baseline, per-phase usage,
source diffs, independent gate logs, and `result.json`. Temporary workspaces
are recorded in `workdir.txt`; final source copies are retained with the results.
One trial provides no variance estimate and does not validate large-task claims.

**Decision:** prefer direct work for small changes like this. Keep Corvée an
optional worker for tasks where expected planning, verification, and recovery
fit inside the anticipated execution savings. The corrected matched trial below now includes accepted worker code and
metered review; it still does not establish general savings.

## Worker recovery and reduced planner involvement — 2026-09-07

The failed-worker result above exposed a provider-control problem, not just
an economic tradeoff. Live metadata showed that native `thinking.type=disabled`
still produced an 8,192-token reasoning-only response with `finish_reason=length`
on Corvex GLM-5.2-FP8. A separate 1,024-token probe with `reasoning_effort=low`
also exhausted its allowance. The same small probe with
`chat_template_kwargs.enable_thinking=false` returned usable code, 127 output
tokens, zero reasoning bytes, and `finish_reason=stop`.

The runner now exposes the verified mapping as `--thinking disabled`, adds an
explicit per-request output limit, preserves returned reasoning for continuity,
and records finish reasons. Truncated responses cannot execute tools or count
as successful reports; explicit resume retries their request at the same step.
`--feedback FILE` lets the same worker investigate and repair parent-observed
failures, preserving original write authority and inference controls.

The workflow now reuses a clear task instead of requiring a separate Astra
planning session. The parent runs authorized checks, returns concise failures
to the worker, and performs one final diff review in its existing session.
Routine worker repair precedes an expensive Astra rewrite; retries remain bounded.

### Live corrected-worker evidence

The same compound-duration task and original baseline were rerun with the
original task text (not the separately generated Astra mission), the verified
thinking control, and an 8,192-token completion allowance. The cheap worker
produced a patch that passed the fixed gate in **16.18 seconds for $0.02877645**.
An additional check of the existing over-maximum requirement exposed Python's
integer-conversion limit on 5,000-digit input. The parent supplied diagnostics
through the actual new CLI `--feedback` path. The worker repaired its own code;
both the original gate and the additional check then passed.

Combined worker cost for implementation plus repair was **$0.076950**, with
10 requests, 88,920 prompt tokens and 4,275 completion tokens. The repair took
14.30 seconds including parent gate checks. No dedicated Astra planning or
reviewing session was launched for this corrected run. Independent parent
checks and diff review still occurred; their token cost is not separately
metered in this conversation. Therefore this does not claim a measured full
workflow saving. Compared with the earlier $0.384058 direct trial, the execution
cost leaves about $0.307108 for additional planner work before breaking even;
that is a budget margin, not an observed total saving.

Local evidence: `.codex/bench/worker-recovery-20260907/control-results.json`,
`response-metadata.json`, and `.codex/bench/worker-recovery-fast-20260907/`.
The raw traces distinguish native controls that did not work from the effective
Corvex template control. Provider behavior may differ elsewhere; the CLI leaves
these controls unset by default rather than silently imposing a provider policy.

The inexpensive implementation worker also initially wasted reads on a broad
runner-edit mission. Supplying exact relevant source fragments and requesting
bounded replacement edits produced the runner patch for $0.0192; its follow-up
repair cost $0.0313. Parent review corrected remaining integration/test-fixture
issues. These are development costs, not part of the duration-task result.
The lesson is to bound exploration when entry points are known, not to require
Astra to pre-solve every task.

## Corrected full task workflow measurement — 2026-09-07

One fresh matched trial compared direct Astra implementation/self-review/tests
against task reuse → Corvée implementation → parent gate → worker repair →
Astra review → worker repair → resumed Astra acceptance. Both arms began with
identical source and a fixed gate, including the 5,000-digit invalid input.
The task was already fully specified, so there was **no separate planning model
call**. Mission preparation copied that task with brief scope/gate instructions;
this does not establish zero planning cost for ambiguous tasks.

| Phase | API-equivalent/catalog cost | Seconds |
| --- | ---: | ---: |
| Direct Astra, including self-review and gate | $0.453338 | 54.78 |
| Corvée initial implementation | $0.059020 | 28.24 |
| Corvée repair after gate failure | $0.117231 | 25.53 |
| Astra review, rejected compatibility bug | $0.175598 | 24.65 |
| Corvée repair after review | $0.155289 | 30.00 |
| Astra resumed acceptance | $0.069602 | 17.05 |
| **Delegated total** | **$0.576740** | **125.48** |

**Observed outcome: delegation cost 27.2% more and took 2.29× the measured
phase time.** Both final patches passed the fixed gate, the reviewer's three
leading-zero counterexamples, and independent scope/fixture-integrity checks.
Astra performed no implementation in the delegated arm. The reviewer rejected
a real defect despite green tests: the first repair incorrectly rejected valid
leading-zero counts. The second repair corrected it.

Worker initial exit was 0 despite failing the gate. Both repair runs returned
exit 3 (`incomplete`) after their bounded step allowances. Their patches were
subsequently verified and accepted independently; this is accepted code, **not
a successful worker completion-status result**. All attempts are charged.

Worker total: 28 requests, 407,202 prompt tokens, 10,891 completion tokens,
**$0.331540**. Recorded tools included 14 reads and 9 replacements. Resuming
accumulated conversation history made the later repairs expensive. These traces
support reducing repeated reading/history and improving first-pass correctness;
removing the review would have accepted a compatibility regression.

Astra direct usage: 68,759 input (32,128 cached), 1,098 output tokens.
Delegated Astra review usage across two turns: 91,231 input (77,440 cached),
597 output tokens, **$0.245200**. Astra API-equivalent cost fell 45.9%, but
Corvée cost exceeded the $0.208138 left before total-cost break-even. Output
includes reasoning; reasoning tokens are not charged a second time. Worker
resume counters are cumulative, so the table charges only repair increments.

The dedicated benchmark total was **$1.030078**. Astra uses the standard rates
verified earlier in this assessment ($10/M uncached input, $1/M cached input,
$12.50/M cache writes, $50/M output); GLM uses $0.75/M input and $2.40/M output.
These are API-equivalent/catalog dollars, not measured ChatGPT subscription
credits or an invoice. No further paid trials were launched for this request.

Measurement boundaries: this includes every dedicated task model call, repairs,
and review. General benchmark construction, dispatch decisions in the parent
conversation, and reporting tokens are not separately metered. Phase seconds
sum subprocess durations and exclude manual dispatch gaps/setup. The initial
review used a fresh session to expose review cost, then resumed for acceptance;
normal in-session review may have different cache behavior. There was one small
task and no variance estimate. A hypothetical no-cache calculation is $0.742490
direct versus $1.273700 delegated; it is arithmetic sensitivity, not a cold run.

Local evidence: `.codex/bench/minimal-workflow-20260907/result.json`, task and
baseline hashes, fixed gate, phase usage/events, repair diagnostics, retained
final source/diffs, and independent gate/regression logs. Artifacts remain local
and excluded from releases. Production duration parsing was not changed.

**Recommendation:** retain the cheap-worker approach for substantial bounded
work, but do not claim savings from this corrected workflow yet. For small
changes of this kind, direct Astra still won. Before another paid comparison,
reduce worker repair round trips and repeated context; review was useful and
was already materially cheaper than direct implementation in this trial.

## Source-packet jobs and integrated Astra measurement — 2026-09-07

Added `corvee job`, a deterministic controller: exact declared source → Corvée
replacement edits → authorized gate → fresh repair calls → independent cheap
review. It has no Astra calls, browsing tools, or Git inspection. Invalid output,
source/fixture drift, missing accounting, and exhausted repairs stop the job.
This implements a lane with zero internal Astra inference, not a proven saving
per accepted task. Starting it directly from a terminal avoids Astra dispatch;
starting and monitoring it through Astra chat still consumes Astra tokens.

### Fresh matched task, including Astra dispatch and reporting

Both arms received identical original duration-parser code and the same fixed
gate. Previously found leading-zero cases were added before either arm started.
Direct Astra implemented, inspected its diff and ran the gate. Delegated Astra
was instructed to dispatch the job and inspect its compact result, without
pre-reading code, implementation, or a separate Astra review. All its tool
coordination and final-report tokens were included.

| Arm/phase | API-equivalent/catalog dollars | Outcome |
| --- | ---: | --- |
| Direct Astra | $0.535228 | Code passed; CLI budget error after final answer |
| Initial Astra dispatch/report | $0.263524 | Reported job escalation |
| Initial Corvée job, three implementation calls | $0.030162 | All three gates failed |
| Astra dispatch/report for one authorized recovery | $0.220380 | Reported incomplete output |
| Corvée recovery with thinking enabled | $0.046404 | 16,384-token output limit exhausted |
| **Delegated total** | **$0.560470** | **Incomplete; not accepted** |

Direct measured duration was **46.26s**, versus **140.23s** across the two
Astra-controlled delegated phases. Delegation spent 4.7% more and failed to
complete. This is a failed-task comparison, **not** comparable accepted outputs
or evidence of savings. Neither job reached cheap review; that implemented path
has local mocked-provider validation, not successful live validation here.

Initial job settings: GLM-5.2-FP8, thinking disabled, 8,192 output tokens, two
repairs, fresh messages for each call. It used 31,941 input and 2,586 output
tokens. It failed ASCII/oversized-input constraints; a repair then regressed
other valid inputs. A separately authorized single recovery used the same
current code with thinking enabled, 16,384 output tokens, and zero repairs. It
used 9,443 input and 16,384 output tokens, returned incomplete output, and applied
no further edits. All failed calls remain in the total; no additional trials
were launched after this recovery.

Astra direct usage was 68,629 input (23,168 cached), 1,149 output tokens.
Delegated Astra usage was 280,498 input (264,064 cached), 1,110 output tokens,
**$0.483904**. Corvée total was **$0.076566**. Astra's dispatch, waiting/polling,
and reporting dominated cost even though it wrote no task code. Launching jobs
through an expensive conversational agent is therefore not automatically cheap.
The total dedicated measurement cost was **$1.095698**, using the rates above.
These are API-equivalent/catalog dollars, not subscription credit measurements.

The direct CLI exhausted its 50,000 weighted rollout-token allowance after
recording a final answer and passing gate. Its exit 1 is preserved. Cost was
recovered from the final persisted session `total_token_usage`, not guessed or
silently rerun. The delegated dispatch allowance was raised to 150,000 to avoid
losing final accounting; model, low effort and standard tier remained the same.
Both arms used explicitly unrestricted execution in separate temporary fixtures
because the delegated controller needs provider network access.

Independent final checks confirmed direct gate success, delegated gate failure,
unchanged gate/original fixtures, and preservation of existing unrelated AST
nodes in both arms. Controller development, benchmark setup, the root conversation
and final assessment remain outside dedicated task totals. Corvée development
calls for this controller totaled $0.240085, including an exploration attempt
that wrote no code and an unusable generation response; parent implementation
review and fixes were not separately metered. Do not amortize them away in claims
about this one task.

No-cache arithmetic sensitivity is $0.743740 direct versus $2.937046 delegated;
these are not measured cold runs. There is one task, no variance estimate, and
elapsed totals exclude the gap between initial escalation and recovery.
Evidence: `.codex/bench/job-workflow-20260907/` contains `result.json`, exact
fixtures/hashes, phase events, persisted direct token evidence, controller
snapshot, both job results/gate logs, final diffs, and source copies.

### Decision after this trial

Keep this as an experimental bounded lane, not a savings-backed default for
unsupervised production changes. It enforces the right separation of duties:
Astra sets a contract or answers an escalated question; ordinary job transitions
are code, and workers do the edits. But the tested worker configuration did not
meet that contract reliably. More Astra supervision would defeat the objective.
The next validation should focus on cheap-worker correctness and a successful
cheap review, then batch/direct-terminal dispatch to avoid repeated Astra turns.
Do not claim that removing tool exploration alone fixed delegation economics.

## Direct Kimi jobs and model-free Codex execution — 2026-09-07

Follow-up used the exact prior task, baseline and fixed gate, but invoked the
controller directly instead of starting an Astra session. No dedicated Astra
calls were launched. The first Kimi-K2.7-Code job timed out at 90s before returning
usage or edits. Its cost is **unknown**, not zero. The controller preserved
`accounting_complete=false` and stopped; the task was not accepted.

An upstream Kimi template example motivated a small control probe. On Corvex,
`chat_template_kwargs.thinking=false` returned valid JSON in 1.10s with 16 input,
six output tokens and no reasoning bytes. This differs from GLM's
`enable_thinking` key. Both runner paths now apply the model-specific mapping.
A new instant-mode Kimi job then used 20,054 input and 1,557 output tokens across
two implementation attempts, costing **$0.0144551** in 25.76s. Both gates failed,
and the job never reached independent cheap review. The probe cost $0.000022.
Known reported cost for these follow-up calls is $0.0144771 **plus the unknown
cost of the timed-out request**; no total saving or accepted-task ratio follows.
Prices came from the fresh Corvex catalog: $0.55/M input, $2.20/M output (catalog
marks Kimi pricing provisional). This chat/setup/reporting remains unmetered.

User feedback corrected the policy: thinking should be preferred for substantive
implementation, review and tricky repairs. A low completion cap on a thinking
model is not evidence that reasoning is uneconomical. The guide now recommends
explicit larger thinking budgets rather than instant mode as a general default.
No additional large paid thinking trial was launched in this follow-up.

A separate local verification established a useful execution boundary:
Codex app-server `command/exec` executes a command under its sandbox **without
creating a thread or model turn**. The new optional `job --executor codex` adapter
uses that API for the caller's fixed gate. Live verification preserved stdout,
stderr and exit code 7, with no inference request. This can remove Astra from
command execution independently of which model performs the reasoning. It does
not yet give the `run` worker an autonomous arbitrary-command tool.

Local evidence: `.codex/bench/direct-job-kimi-20260907/` (timeout, catalog, probe),
`.codex/bench/direct-job-kimi-instant-20260907/` (failed fixed task), and
`.codex/bench/codex-executor-20260907/` (actual protocol trace and adapter check).
The prior $0.535228 direct-Astra result is a reference only; it was not rerun.

## Larger matched work package: durable SQLite queue — 2026-09-07

This trial increased scope from a small parser fix to a four-module durable task
queue: shared validation, strict JSON payloads, capped backoff, SQLite persistence,
atomic priority/FIFO claiming, expiring leases, heartbeat, acknowledgements,
retries, dead-letter transitions, cancellation, and crash/lease recovery.
Both arms started from byte-identical stubs and the same 21-test acceptance gate.
The gate included concurrent claims through independent SQLite connections and
validation before mutation. Task/gate hashes were frozen before model execution.
This is a synthetic engineering work package, not a production qualification.

Astra alone implemented and self-reviewed the package. The delegated arm used
one 178-word Astra architecture addendum, then Kimi-K2.7-Code with **thinking
enabled**, fresh contexts, 32,768 output tokens and 600 seconds per call, at most
one repair, and a separate cheap reviewer. The gate ran through Codex app-server
`command/exec` without model turns. After cheap acceptance, Astra resumed its
original planning session for one final architecture/correctness review. It made
no implementation edits. Known controller failures would have skipped that final
Astra call; this job reached `ready`, so the review actually ran and was charged.

| Phase | API-equivalent/catalog dollars | Seconds |
| --- | ---: | ---: |
| Astra alone: implementation, checks, self-review | $0.634304 | 180.74 |
| Delegated Astra architecture | $0.188320 | 12.97 |
| Corvée implementation, repair, cheap review and model-free gates | $0.123204 | 674.57 |
| Delegated Astra final review | $0.351356 | 60.43 |
| **Delegated total** | **$0.662880** | **747.98** |

**Outcome: Astra alone accepted; delegated result rejected.** Delegation spent
4.5% more and took about 4.14× the measured phase time. This is not an accepted
completion saving. The complete dedicated trial cost was **$1.297184**.

Corvée's first implementation passed 20 of 21 tests. Its one repair fixed
normalization of an overflowing integer-to-float conversion; the original gate
then passed. Its independent cheap reviewer returned accepted=true with no
findings. Astra's final review identified four defect categories, concretized as
five additional checks:

- JSON exponent overflow (`{"n":1e400}`) produces infinity instead of ValueError.
- Subnormal backoff raises OverflowError for a finite, below-cap result.
- A large valid integer time reaches SQLite unnormalized in `ack`.
- Large valid priority and max-attempt integer values overflow SQLite bindings;
  the specified contract did not impose a 64-bit range restriction.

These checks were applied **symmetrically to both final implementations**, without
changing the original gate or giving either arm another repair. Astra-alone code
passed all five; delegated code failed all five. Both arms preserved the fixed
gate/task and edited only the four permitted modules. The additional checks are
retained separately so their post-review origin remains visible. Passing these
checks is benchmark acceptance, not proof of all possible correctness properties.

### Did the work sufficiently outweigh Astra's overhead?

No. Astra instruction plus final review cost **$0.539676**, reducing Astra spend
by only **14.9%** relative to direct execution. That left $0.094628 for workers
before break-even; actual Corvée cost was $0.123204. Direct baseline cost divided
by delegated Astra overhead was only **1.18×**, far below the proposed 5× target.
This ratio is an economic proxy, not a claim that generated line counts measure
value. The direct result had 319 source lines; producing a longer worker result
did not compensate for its missed requirements.

Corvée used 30,428 input and 48,395 output tokens across three calls. Breakdown:
implementation $0.050080, repair $0.043918, cheap review $0.029207. All output,
including thinking, is charged once. The repair was counted even though it
corrected only one defect. The actual thinking allowance enabled substantial
implementation; it did not establish sufficient independent-review quality.

Astra input/output usage: direct 175,882/4,766 (151,424 cached input); architecture
17,532/260 (no cached input); final review 86,006/1,392 (64,256 cached input).
Rates are the standard Astra API-equivalent rates used above and the fresh
Corvex catalog's Kimi $0.55/M input and $2.20/M output. These are not subscription
credit measurements or invoices. No-cache arithmetic sensitivity is $1.997120
direct versus $1.241184 delegated; it is not a cold-run measurement and does not
change the delegated correctness failure.

Limits: one work package, no variance estimate; shared task/test construction,
root orchestration conversation, setup and final reporting were not separately
metered. All dedicated task architecture, execution, repair and review calls are
included. Phase durations exclude gaps between phase dispatches. No further paid
repair or alternate-model trial was launched after rejection.

Evidence: `.codex/bench/queue-workflow-20260907/` includes the frozen specification,
21-test gate, stubs, controller snapshot, provider catalog, exact invocation,
all phase usage/events, cheap review, Astra review, final code/diffs, symmetric
counterexample results, and `result.json`.

**Decision:** increasing package size alone did not make this configuration
cost-effective. Cheap implementation now produces substantial tested code, but
cheap review missed defects that required expensive Astra investigation. The
next improvement should make cheap review produce and execute adversarial checks
before Astra acceptance, then retest accepted-task cost. Removing the final
review from this measured run would have accepted incorrect code; it is not a
valid retroactive saving.


## Workflow-fit pilot: mechanical migration and adversarial probes (2026-09-07)

One fresh matched trial per category, with Astra low/default and Kimi-K2.7-Code
thinking enabled. This used an experimental source-packet script/probe workflow,
not the existing `corvee job` implementation/review loop unchanged. Astra planned,
Corvee returned an executable deliverable, the command executor ran it without a
model turn, and Astra received compact acceptance evidence. No paid retries.

| Mission | Astra alone | Astra + Corvee | Outcome |
|---|---:|---:|---|
| 160-callsite migration across 40 generated modules | $0.452406 | $0.190431 | Both exact transformations passed; 57.9% lower dedicated task-call cost |
| Adversarial queue contract probes | $0.477136 | $0.183392 spent, incomplete | Astra produced six reproducing probes; Corvee produced none |

Migration delegation costs: planning $0.109820, worker $0.057335, final acceptance
$0.023276. Astra overhead was $0.133096, 70.6% below direct Astra cost; the
baseline/overhead ratio was 3.40x, still below the proposed 5x target. Exact byte
comparison verified every authorized callsite transformation; source integrity
checks passed. Both arms could generate automation instead of rewriting files.
The fixture has identical-shape calls and is synthetic: this does not establish
savings for heterogeneous production migrations. Its acceptance oracle is cheap
because the complete transformation is already specified.

Migration model-phase time was 95.0 seconds direct versus 364.1 seconds delegated
(14.1 planning + 342.4 worker + 7.6 acceptance). The worker used 7,426 input and
24,205 output tokens, including 22,714 reasoning tokens. Thinking was enabled in
both worker trials; these results do not compare instant mode.

Adversarial delegation costs: planning $0.108830, worker $0.074562, final review
$0 (deterministically skipped because there was no deliverable). The provider
reported `finish_reason=length`: all 32,768 completion tokens were reasoning and
content was empty. A JSON parsing error initially obscured the usage in the
summary; accounting was recovered from the already-saved provider response.
This was a paid failed attempt, not a saving or a zero-finding completed audit.
Direct model-phase time was 120.1 seconds; delegated phases consumed 471.3 seconds.

Both adversarial arms received the same previously generated buggy queue modules
and authoritative contract, without the earlier known reproducers or reference
implementation. Six direct probes reproduced failures without changing source.
Four passed on the separate reference: intermediate backoff overflow, rounded
backoff cap threshold, decoded JSON exponent overflow, and SQLite integer-binding
range (grouped across argument sites to avoid inflation). Two failed on both
implementations: surrogate strings and Python's integer-string digit limit.
The unrestricted str/int contract supports treating these as shared contract edge
cases, not false positives merely because the reference also fails. Conservative
cost per reference-distinguishing finding is $0.119284; including the two shared
contract edge cases gives $0.079523 per category. Delegated cost per finding is
undefined because it delivered zero probes. This is a known-buggy fixture, not a
blind estimate of audit recall on typical repositories.

Total dedicated calls across both comparisons: **$1.303366**. Astra uses the
[current published API-equivalent rates](https://developers.openai.com/api/docs/models/gpt-6-astra),
including observed cached input; Corvex's freshly archived catalog lists Kimi at
$0.55/M input and $2.20/M output, marked provisional. These are not measured
subscription credits or invoices. Shared root-agent fixture construction,
orchestration, independent adjudication and reporting are not separately metered.
Thus this is a task-call operating-cost comparison, not the total global cost of
creating and conducting this experiment. Model-phase times exclude dispatch gaps
and external deterministic validation. One trial per category provides no variance
estimate. A thread/signal preflight failure occurred before any provider request;
it was corrected with separate processes and incurred no model call.

Evidence: `.codex/bench/fit-workflows-20260907/` retains fixture hashes, harness,
source packets, phase events/usage, catalog, deliverables, execution evidence,
root-cause adjudication and aggregated `result.json`.

**Decision:** the evidence-first migration workflow achieved a measured saving
when acceptance was completely specified and mechanically checkable. The tested
thinking-enabled adversarial workflow did not deliver within its cap. Do not
promote it on an assumed saving, automatically raise its budget, or remove failed
attempts from accounting. Any next adversarial trial should first constrain the
search breadth and require a small executable deliverable within the reasoning
budget; that remains an unmeasured proposed change.
