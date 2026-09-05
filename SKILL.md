---
name: corvee
description: Delegate bounded repository work directly to Corvex models while Codex owns planning and verification. Use for provider setup, model selection, target refinement, and execution loops with measurable acceptance criteria.
---

# Corvée

Keep architecture, targets, sequencing, review, and acceptance in Codex. Send bounded missions to Corvex through the bundled runner. The primary Codex model is not changed.

## Instructions

The word after `$corvee` is a skill instruction, not a registered slash command. Six of them run the bundled CLI; the rest are planning steps you carry out yourself, with no script behind them.

CLI-backed:

| Instruction | Purpose |
| --- | --- |
| `configure` | Configure URL and credential |
| `models [PATTERN]` | List live model IDs |
| `select [MODEL_ID\|auto]` | Show, save, or clear the default model |
| `check` | Verify credentials with a tiny inference request |
| `run` | Execute and verify one iteration |
| `cleanup` | Remove stale run report directories |

Planning steps you perform (they read and write `.codex/corvee/`, and call `run` when they need a delegate):

| Instruction | Purpose |
| --- | --- |
| `target GOAL` | Define outcome and acceptance gates |
| `analyze` | Assess gaps and propose work units; never edits `TARGET.md` |
| `refine` | Revise `TARGET.md` itself: the only instruction that may |
| `loop [CONDITION]` | Repeat `run` within a bounded budget |
| `audit` | Independently challenge completion |
| `status` | Report progress and remaining gates |

`analyze` and `refine` are separate so that a gate is never softened because a delegate failed it: only `refine` may change `TARGET.md`.

Write acceptance gates as commands wherever one exists. Checking an exit code costs the planner the same whether the delegate changed three files or three hundred; judging a change by reading it does not.

Read [control-protocol.md](references/control-protocol.md) for target and execution operations. Read [provider-setup.md](references/provider-setup.md) for setup and model selection.

## Setup

Find `scripts/corvee` relative to this skill's actual installation directory. Run `python3 <skill-dir>/scripts/corvee configure` in a local terminal for the hidden-input wizard. Never request keys in chat or pass them in command arguments. Codex may configure non-interactively from an existing environment variable or user-identified dotenv file.

Settings default to `${CODEX_HOME:-~/.codex}/corvee/config.toml`, with the key in a separate mode-0600 `credentials.toml`. `configure` and `check` make a tiny inference request that may incur a charge. The public model catalog does not authenticate the key.

## Delegation

Read [mission-format.md](references/mission-format.md) before preparing a mission. Store target state, missions, and reports under `.codex/corvee/` in the target repository. Keep secrets out of all context sent to Corvex.

Use an exact selected model. Split work that is ambiguous, not work that is merely large: every mission costs the planner a specification, a verdict and a ledger entry regardless of its size. A large mechanical change with a clear recipe is the ideal mission. Parallel read-only missions or independent worktrees are possible; serialize writes within a shared worktree.

```bash
python3 <skill-dir>/scripts/corvee run \
  --mission /absolute/path/to/mission.md \
  --cwd /absolute/path/to/repository \
  --model exact-provider-model-id \
  --complexity low
```

Omit `--write` for analysis and review. Enable it only for authorized edits, and pass `--allow-command NAME` only for needed executables. Command execution is not a security sandbox; interpreters, compilers, and repository scripts can execute arbitrary code. File tools confine paths to the repository but do not guarantee that repository contents are secret-free. Use a sanitized checkout when needed.

The runner prints one line per run boundary and error to stderr; the full event stream stays in `events.jsonl` unless you pass `--verbose`. Read `status.json` rather than scrolling the stream.

`status.json` records what the run itself cost on the delegate side: `economics.delegate_tool_bytes`, `delegate_tool_calls`, `mission_bytes`, `report_bytes`, `diff_bytes`, and the provider's token counts under `usage`. The runner cannot see what the planner reads, so it does not guess.

## Boundaries the runner enforces

Write tools refuse `.git/` and `.codex/corvee/reports/` after symlink resolution: the first would grant execution through hooks or `core.sshCommand` on your next git operation, the second holds the evidence you audit. Both stay readable. Allow-listed commands receive only a fixed environment allow-list (`PATH`, `HOME`, locale, and similar), so agent sockets and provider variables are not inherited.

Allowed executables are resolved at startup; delegate commands must use the exact authorized name or path. Search and listing prefer `rg` and fall back to `grep` with extended regular expressions and a plain directory walk. Both fallbacks skip hidden entries and symlinks but do not honor `.gitignore`, so ignored private files must be removed from shared checkouts. Linux/macOS wall-clock deadlines interrupt blocked requests and terminate active command process groups; deliberately detached processes are not sandboxed.

## After a run

Capture the report and exit status, rerun the gate command yourself, read the change, and update the ledger with evidence. Rerunning the gate is not delegable. For a change too large to read, `audit` sends a fresh read-only mission that reports on it, but delegating a reading costs planner output tokens to specify and a round trip to wait for. Exit zero means a report was returned, not that the target passed. Timeouts and missing reports are incomplete work. Never weaken acceptance gates to obtain success.

`--complexity` picks the step and time budget: `low` is 16 steps and 20 minutes, `medium` 32 and 60, `high` 48 and 120. Pass `--max-steps` or `--max-time` only to override one of them.

Requests default to a 600-second timeout within the total run budget. On failure or incomplete wrap-up, follow the recovery guidance in [control-protocol.md](references/control-protocol.md) before retrying; prefer `scripts/corvee run --resume <run-dir>` to continue from checkpoint instead of reissuing the same mission from scratch. Resuming reuses the original run's `--write` mode and `--allow-command` list; passing a conflicting one is refused.

`loop` is a workflow within the active Codex session, not a background scheduler. Stop at independently verified completion, the budget, or a blocker requiring user input or expanded authority.
