# Delegate mission format

Write each mission as a short, self-contained Markdown file. The delegate receives no hidden primary-agent reasoning and should not need prior chat history.

```markdown
# Mission: <bounded unit>

## Objective
One deliverable tied to one target gate.

## Working directory
Absolute repository root.

## Current evidence
Relevant files, symbols, observed failures, and pre-existing changes.

## Decided approach
Architecture and constraints already chosen by Codex.

## Scope
Files or components the delegate may inspect or edit.

## Non-goals and forbidden actions
No scope expansion; no credentials, production actions, commit, push, release,
dependency upgrade, or destructive command unless explicitly authorized here.

## Required work
Concrete implementation or analysis tasks.

## Verification the delegate can offer
Evidence the delegate can gather by reading -- the code path it changed, the
call sites it checked. The delegate cannot run anything, so this is reasoning
to be checked, never a claim that a test passed. It may call `request_command`
to ask you to run something, which suspends the run; say here whether that is
worth a round trip for this mission, or whether it should report what it has
and let the next iteration carry the answer.

## Evidence report
- summary of result;
- files changed;
- tool calls or commands run and their outcomes;
- remaining failures, uncertainties, and assumptions;
- whether the objective is complete, with supporting evidence.
```

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
