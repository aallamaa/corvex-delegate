# Corvée

**GPT is the brain. Corvex does the corvée.**

In French, a *corvée* is a chore—the work that needs doing. This skill lets GPT in Codex think through the problem, define the target, plan the work, and verify the results, while models served by Corvex handle the delegated legwork: repository exploration, implementation, tests, repetitive refactoring, and documentation.

GPT decides what to do and checks whether it worked. Corvex executes clearly scoped tasks. You keep Codex as your thinking and coordinating partner while handing off the corvées.

Includes model discovery, protected credential setup, persistent target tracking, and bounded execution loops. Connects directly to Corvex's OpenAI-compatible API; no OMP or third-party agent framework is required.

## Acknowledgments

Thank you to **Corvex** for generously granting me alpha access to their APIs, which made it possible to build and test this skill.

## Requirements

Python 3.11+, Git, ripgrep (`rg`) or `grep`, Codex with local skill support, and a Corvex API key. The Python runtime uses only the standard library. Linux and macOS are supported; the runner uses POSIX signals for wall-clock deadlines and does not support Windows.

Search prefers ripgrep. The grep fallback uses extended regular expressions and filename/path globs, skips hidden entries and symlinks, and does not interpret `.gitignore`; use a sanitized checkout when ignored files contain private data.

## Install

Clone the repository and run the installer:

```bash
git clone https://github.com/aallamaa/corvex-delegate.git corvee
cd corvee
python3 scripts/install.py
```

The installer confirms `https://api.tokenfactory.corvex.cloud/v1`, accepts the key through hidden input, lists models, optionally saves a model, and verifies authentication with a one-token inference request before installation. This can incur a small provider charge. Model listing is public and cannot validate a key.

Default installation: `~/.codex/skills/corvee/`. Use `--codex-home PATH` for a different Codex home, or `--force` to replace an existing installation. Restart Codex if the skill does not appear. The installer copies only package resources, excluding development artifacts and credentials.

For an existing protected environment:

```bash
python3 scripts/install.py --non-interactive --api-key-env CORVEX_API_KEY
```

A standard Codex skill installer can also install this folder; run `configure` afterward. Discovering a skill does not automatically run its wizard.

## Use in Codex

```text
$corvee configure
$corvee models
$corvee select exact-provider-model-id
$corvee target Make the service restart without losing queued jobs
$corvee analyze
$corvee loop --max-iterations 6 --max-time 120m
$corvee status
```

Other instructions: `check`, `refine`, `run` (one iteration), and `audit`. These are skill arguments, not slash commands. `select auto` clears the default rather than choosing a model automatically.

Codex maintains acceptance criteria, missions, and progress in `.codex/corvee/` in the target repository. A loop ends at independently verified completion or its budget/blocking boundary. Loops run in the active Codex session, not as a background service.

## CLI

One public entry point handles setup and individual missions:

```bash
python3 scripts/corvee configure
python3 scripts/corvee models
python3 scripts/corvee select exact-provider-model-id
python3 scripts/corvee show
python3 scripts/corvee check
python3 scripts/corvee run --mission /path/mission.md --cwd /path/repo --max-time 20m
```

Use `COMMAND --help` for options. `--config PATH` selects another settings file. Target planning, audit, and loops are interpreted by Codex; the CLI runs individual missions, not the entire orchestration workflow.

### Long requests and recovery

Inference requests default to a **600-second** socket timeout (`run --http-timeout`, or `--timeout` for configuration commands). The runner's total `--max-time` remains a hard wall-clock boundary. Between requests it reserves up to 20% of the run budget for reporting, so a shorter run may cap an individual request below 600 seconds.

Each non-dry run creates a new private directory under `.codex/corvee/reports/`, or at `--run-dir PATH` (which must not already exist). It contains metadata-only `events.jsonl`, `status.json`, `report.md`, and an atomic `checkpoint.json` with conversation and tool results. Directories are mode 0700 and files mode 0600. Checkpoints contain repository content: do not publish them or treat key redaction as a comprehensive secret scanner.

One transient API retry is allowed by default; `--request-retries 0` disables retries and `2` is the maximum. Retries may incur duplicate inference charges, stay within the original time budget, and never replay completed local tools. Authentication errors and invalid JSON are not retried.

Repeated identical tool results or consecutive errors trigger a warning, then a tools-disabled wrap-up. The last model step is also reserved for reporting. A forced wrap-up is incomplete even when a report arrives. Exit codes: `0` report returned (not verified success), `3` incomplete wrap-up, `75` transient provider failure after retries, `124` budget exhausted, `130` interrupted, `1` other failure.

After failure, inspect `status.json`, `events.jsonl`, and the checkpoint before preparing a smaller follow-up mission. A `tool_pending` checkpoint means execution may have partially happened: inspect repository state before retrying. Automatic checkpoint resume/replay is deliberately not provided. Hard interruptions such as SIGKILL cannot finalize status; use the last durable checkpoint as incomplete evidence.

## Configuration and data handling

Settings live in `${CODEX_HOME:-~/.codex}/corvee/config.toml`; the key lives beside them in mode-0600 `credentials.toml`. `CORVEX_API_KEY` overrides the stored key. Never paste keys into chat or commit credentials.

Mission text and tool results are sent to the configured provider. File tools operate inside the selected repository; read-only mode omits editing tools. Commands are disabled unless enabled with `--allow-command`. This is not an OS sandbox or a comprehensive secret scanner: delegate only content you may share and use a sanitized checkout where appropriate. Codex independently verifies returned work.

## Native agents

The supported workflow uses the direct runner. Cross-provider native delegation failed in Codex CLI 0.153.2: an OpenAI parent spawned the custom role but the child retained OpenAI's provider. A separate Codex process configured for Corvex worked. Experimental helpers remain for investigation; normal installation does not install a native agent or modify the primary Codex provider. See [compatibility evidence](references/native-compatibility.md).

## Release checks

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/corvee --help
python3 scripts/package.py
```

Tests use a mock provider and no real key. Live checks are opt-in. Packaging creates a filtered archive and SHA-256 checksum in `dist/`.

This is an independent community skill, not an official OpenAI or Corvex product.

Licensed under the [MIT License](LICENSE).
