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

## Verification requested from the delegate
Checks the delegate should perform if their executables were explicitly enabled.

## Evidence report
- summary of result;
- files changed;
- tool calls or commands run and their outcomes;
- remaining failures, uncertainties, and assumptions;
- whether the objective is complete, with supporting evidence.
```

Keep the mission within the user's authorized scope. State known pre-existing changes so the delegate does not overwrite or misattribute them. For read-only missions, say explicitly that no repository edits are allowed.

Delegate verification is advisory. The primary Codex agent must inspect the actual changes and rerun decisive checks before closing a gate.
