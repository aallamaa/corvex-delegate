---
name: corvee
description: Delegate bounded repository work directly to Corvex models while Codex owns planning and verification. Use for provider setup, model selection, target refinement, and execution loops with measurable acceptance criteria.
---

# Corvée

Keep architecture, targets, sequencing, review, and acceptance in Codex. Send bounded missions to Corvex through the bundled runner. OMP is not required and the primary Codex model is not changed.

## Commands

The word after `$corvee` is a skill instruction, not a registered slash command:

| Instruction | Purpose |
| --- | --- |
| `configure` | Configure URL and credential |
| `models [PATTERN]` | List live model IDs |
| `select [MODEL_ID\|auto]` | Show, save, or clear the default model |
| `check` | Verify credentials with a tiny inference request |
| `cleanup` | Remove stale run report directories |
| `target GOAL` | Define outcome and acceptance gates |
| `analyze` | Assess gaps and propose work units |
| `refine` | Clarify the target and plan |
| `run` | Execute and verify one iteration |
| `loop [CONDITION]` | Repeat within a bounded budget |
| `audit` | Independently challenge completion |
| `status` | Report progress and remaining gates |

Read [control-protocol.md](references/control-protocol.md) for target and execution operations. Read [provider-setup.md](references/provider-setup.md) for setup and model selection.

## Setup

Find `scripts/corvee` relative to this skill's actual installation directory. Run `python3 <skill-dir>/scripts/corvee configure` in a local terminal for the hidden-input wizard. Never request keys in chat or pass them in command arguments. Codex may configure non-interactively from an existing environment variable or user-identified dotenv file.

Settings default to `${CODEX_HOME:-~/.codex}/corvee/config.toml`, with the key in a separate mode-0600 `credentials.toml`. `configure` and `check` make a tiny inference request that may incur a charge. The public model catalog does not authenticate the key.

## Delegation

Read [mission-format.md](references/mission-format.md) before preparing a mission. Store target state, missions, and reports under `.codex/corvee/` in the target repository. Keep secrets out of all context sent to Corvex.

Use an exact selected model. Split ambiguous or oversized work before delegation. Parallel read-only missions or independent worktrees are possible; serialize writes within a shared worktree.

```bash
python3 <skill-dir>/scripts/corvee run \
  --mission /absolute/path/to/mission.md \
  --cwd /absolute/path/to/repository \
  --model exact-provider-model-id \
  --complexity low --max-steps 16 --max-time 20m
```

Omit `--write` for analysis and review. Enable it only for authorized edits, and pass `--allow-command NAME` only for needed executables. Command execution is not a security sandbox; interpreters, compilers, and repository scripts can execute arbitrary code. File tools confine paths to the repository but do not guarantee that repository contents are secret-free. Use a sanitized checkout when needed.

Write tools additionally refuse `.git/` and `.codex/corvee/reports/` after symlink resolution: the first would grant execution through hooks or `core.sshCommand` on your next git operation, the second holds the evidence you audit. Both stay readable. Allow-listed commands receive only a fixed environment allow-list (`PATH`, `HOME`, locale, and similar), so agent sockets and provider variables are not inherited.

Allowed executables are resolved at startup; delegate commands must use the exact authorized name or path. Search prefers `rg` and falls back to `grep` with extended regular expressions. The fallback skips hidden entries and symlinks but does not honor `.gitignore`, so ignored private files must be removed from shared checkouts. Linux/macOS wall-clock deadlines interrupt blocked requests and terminate active command process groups; deliberately detached processes are not sandboxed.

After each run, capture the report and exit status, inspect changes, independently rerun decisive checks, and update the ledger with evidence. Exit zero means a report was returned, not that the target passed. Timeouts and missing reports are incomplete work. Never weaken acceptance gates to obtain success.

Requests default to a 600-second timeout within the total run budget. Private run artifacts preserve progress, errors, and checkpoints. On failure or incomplete wrap-up, follow the recovery guidance in [control-protocol.md](references/control-protocol.md) before retrying; prefer `scripts/corvee run --resume <run-dir>` to continue from checkpoint instead of reissuing the same mission from scratch.

## Execution boundary

The supported workflow uses the direct API runner. Cross-provider native subagents failed in the recorded Codex CLI 0.153.2 test: the child retained the OpenAI provider. A standalone Codex process using Corvex worked, but is a separate session. Optional native helpers are experimental; see [native-compatibility.md](references/native-compatibility.md). Do not enable them during normal installation or infer native support from an API probe.

`loop` is a workflow within the active Codex session, not a background scheduler. Stop at independently verified completion, the budget, or a blocker requiring user input or expanded authority.
