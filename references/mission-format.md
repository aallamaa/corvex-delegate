# Delegate mission format

Prefer the user's existing task over rewriting it. Add only what the worker
cannot infer safely. A routine mission is usually 100–200 words:

```markdown
## Outcome
The requested observable change.

## Scope and evidence
Files/components allowed to change; useful entry points; known pre-existing edits.

## Constraints
Compatibility and architectural decisions already made. Let the worker choose
ordinary implementation details; do not require a complete planner-written patch.

## Acceptance
The command the parent will run and any additional checks it cannot automate.
Do not weaken or edit the gate. Do not run or request commands for routine
verification; the parent will return failures through --resume --feedback.

## Report
At most 150 words: changed files, why the change meets the task, and uncertainties.
Do not claim tests ran. Stop when the patch is ready for parent verification.
```

The worker receives no hidden planner reasoning. Include necessary context but
avoid full source dumps where a path/symbol is sufficient. Do not make a separate
Astra planning call for a task whose outcome, scope, and gate are already clear.
For ambiguous design, Codex resolves the ambiguity before authorizing writes.

The parent runs the authorized gate under its own execution boundary. Return
concise failures to the same run with `--feedback`; keep repair ownership with
the cheap worker. Preserve the original scope and remaining budget. Do not
silently promote a read-only worker or run commands based on its report.

## Audit missions

An `audit` mission is read-only and inverts the usual scope: the subject is the
change, not the task. Give it the target, the gate the change was meant to
satisfy, and the diff itself; withhold the implementing delegate's report so it
cannot inherit that delegate's conclusions. Ask for a verdict of at most a few
hundred words:

```markdown
## Verdict
pass | concerns | reject, and the single reason.

## Per hunk
File and lines, what changed, and whether it is inside the stated scope.

## Outside scope
Anything touched that the mission did not authorise.

## Defects
Only what the reviewer can support with evidence from the diff.
```

The planner reads this verdict and still reruns the gate command itself. A
clean audit is not a passing gate.

Keep the mission within the user's authorized scope. State known pre-existing changes so the delegate does not overwrite or misattribute them. For read-only missions, say explicitly that no repository edits are allowed.

Delegate verification is advisory. The primary Codex agent reruns the gate command before closing a gate, and reads the change to the extent the gate does not cover it.
