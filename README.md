# Corvée

**GPT is the brain. Corvex does the corvée.**

In French, a *corvée* is a chore—the work that needs doing. This skill lets GPT in Codex think through the problem, define the target, plan the work, and verify the results, while models served by Corvex handle the delegated legwork: repository exploration, implementation, tests, repetitive refactoring, and documentation.

GPT decides what to do and checks whether it worked. Corvex executes clearly scoped tasks. You keep Codex as your thinking and coordinating partner while handing off the corvées.

Includes model discovery, protected credential setup, persistent target tracking, and bounded execution loops. Connects directly to Corvex's OpenAI-compatible API; no OMP or third-party agent framework is required.

## Acknowledgments

Thank you to **Corvex** for generously granting me alpha access to their APIs, which made it possible to build and test this skill.

## Requirements

Python 3.11+, Git, ripgrep (`rg`) or `grep`, Codex with local skill support, and a Corvex API key. The Python runtime uses only the standard library. Linux and macOS are supported; the runner uses POSIX signals for wall-clock deadlines and does not support Windows.

Search and file listing prefer ripgrep. Both fallbacks skip hidden entries and symlinks, use filename/path globs (the search fallback adds extended regular expressions), and do not interpret `.gitignore`; use a sanitized checkout when ignored files contain private data.

### Other providers

The skill is built and tested against Corvex, and that is the endpoint it defaults to. The runner itself speaks plain OpenAI-compatible `POST /chat/completions` with function tools, so any endpoint implementing that subset works: point `--base-url` (or `CORVEX_API_URL`) at it, supply its key through `CORVEX_API_KEY`, and pass one of its exact model IDs. `models` and `select` need a `GET /models` catalog; a provider without one still runs missions through an explicit `--model`.

The URL must be HTTPS, except on loopback, so a local server (vLLM, Ollama, LM Studio) is reachable at `http://localhost:PORT/v1`. Only point it at an endpoint you trust with your key and your repository contents.

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
$corvee loop until every gate passes, at most 6 iterations or 120 minutes
$corvee status
```

Other instructions: `check`, `refine`, `run` (one iteration), `audit`, and `cleanup`. These are skill arguments, not slash commands, and any budget written after `loop` is prose the planner interprets rather than parsed flags. `select auto` clears the default rather than choosing a model automatically.

Codex maintains acceptance criteria, missions, and progress in `.codex/corvee/` in the target repository. A loop ends at independently verified completion or its budget/blocking boundary. Loops run in the active Codex session, not as a background service.

## CLI

One public entry point handles setup and individual missions:

```bash
python3 scripts/corvee configure
python3 scripts/corvee models
python3 scripts/corvee select exact-provider-model-id
python3 scripts/corvee select
python3 scripts/corvee check
python3 scripts/corvee cleanup --older-than-days 30
python3 scripts/corvee run --mission /path/mission.md --cwd /path/repo --max-time 20m
python3 scripts/corvee run --resume /path/repo/.codex/corvee/reports/<run-id> --max-steps 8 --max-time 15m
```

Use `COMMAND --help` for options. `--config PATH` selects another settings file. Target planning, audit, and loops are interpreted by Codex; the CLI runs individual missions, not the entire orchestration workflow.

### Long requests and recovery

Inference requests default to a **600-second** socket timeout (`run --http-timeout`, or `--timeout` for configuration commands). Every duration option accepts plain seconds or a `30s`/`30m`/`2h` suffix. The runner's total `--max-time` remains a hard wall-clock boundary. Between requests it reserves up to 20% of the run budget for reporting, so a shorter run may cap an individual request below 600 seconds.

`status.json` also records what the run cost on the delegate side: `delegate_tool_bytes` and `delegate_tool_calls` (what the delegate read), `mission_bytes`, `report_bytes`, and `diff_bytes` (working tree against `HEAD`, plus untracked files). The runner has no visibility into the planner's own token spend, which happens in another process, so it reports its own side and leaves the comparison to you. `diff_bytes` is null when it could not be measured, which is not the same as zero.

`status.json` records the token usage the provider reported: `requests`, `prompt_tokens`, `completion_tokens`, `total_tokens`, and `reported_by_provider`. Per-request counts appear on `request_end` in `events.jsonl`. No prices are bundled, and a provider that returns no usage leaves `reported_by_provider` false, which means unmeasured rather than zero. Enforce spending through `--max-steps`, `--max-time`, and iteration budgets.

The runner keeps its stderr short: one line per run boundary and per error. The complete event stream is always written to `events.jsonl`, and `--verbose` echoes it to stderr as well. Read `status.json` rather than scrolling the stream, so a long run does not consume the planner context this tool exists to save.

Each non-dry run creates a new private directory under `.codex/corvee/reports/`, or at `--run-dir PATH` (which must not already exist). It contains metadata-only `events.jsonl`, `status.json`, `report.md`, and an atomic `checkpoint.json` with conversation and tool results. Directories are mode 0700 and files mode 0600. Checkpoints contain repository content: do not publish them or treat key redaction as a comprehensive secret scanner.

To avoid redoing completed work after an interrupted or budget-exhausted run, use `--resume /path/to/run-dir` when rerunning. The runner restores the checkpointed conversation and continues from the next step while preserving prior tool outputs and avoiding duplicate re-execution. It also restores the original run's `--write` mode, so the flag need not be repeated; asking for `--write` on a run that was read-only is refused rather than silently widening the delegate's authority mid-run.

One transient API retry is allowed by default; `--request-retries 0` disables retries and `2` is the maximum. Retries may incur duplicate inference charges, stay within the original time budget, and never replay completed local tools. Authentication errors and invalid JSON are not retried.

Repeated identical tool results or consecutive errors trigger a warning, then a tools-disabled wrap-up. The last model step is also reserved for reporting. A forced wrap-up is incomplete even when a report arrives. Exit codes: `0` report returned (not verified success), `2` unusable arguments or configuration, `3` incomplete wrap-up, `65` suspended awaiting a requested command, `75` transient provider failure after retries, `124` budget exhausted, `130` interrupted, `1` other failure.

After failure, inspect `status.json`, `events.jsonl`, and the checkpoint before preparing a smaller follow-up mission. A `tool_pending` checkpoint means execution may have partially happened: inspect repository state before continuing. Use `--resume` to continue from the same run directory when safe. Hard interruptions such as SIGKILL cannot finalize status; use the last durable checkpoint as incomplete evidence.

## Configuration and data handling

Settings live in `${CODEX_HOME:-~/.codex}/corvee/config.toml`; the key lives beside them in mode-0600 `credentials.toml`. `CORVEX_API_KEY` overrides the stored key. Never paste keys into chat or commit credentials.

Reads are windowed: `read_file` streams to the requested `start_line`, so a large log can be paged rather than refused, and one window returns at most 30 KB. Editing tools still require the whole file to fit in 200 KB. Directory listings cap at 1000 entries and say so, whether ripgrep or the fallback answered. A search stops after 120 seconds across all batches and reports partial results rather than consuming the run budget.

Mission text and tool results are sent to the configured provider. File tools operate inside the selected repository; read-only mode omits editing tools. The delegate has no way to execute a command. This is not an OS sandbox or a comprehensive secret scanner: delegate only content you may share and use a sanitized checkout where appropriate. Codex independently verifies returned work.

Even in `--write` mode, `write_file` and `replace_text` refuse `.git/` and `.codex/corvee/reports/` after resolving symlinks. Writing `.git/hooks/*` or `core.sshCommand` would otherwise execute on your next git operation, and the report tree is the evidence trail. Both remain readable. A `.git` component anywhere in the path is refused, case-insensitively, so vendored checkouts and submodules are covered as well as the root repository.

The delegate cannot run commands. Earlier versions exposed a `run_command` tool behind an `--allow-command` allow-list, guarded by a denylist of options that turn a benign binary into a runner for something else (`ssh -oProxyCommand=...`, `git --git-dir`, `find -exec`). That guard could not hold: no flag list makes `git` safe when `git config alias.x '!cmd'` followed by `git x` uses no flag at all. Execution now happens only where the user already approves it.

Instead the delegate calls `request_command`, which executes nothing: it records the command and the reason, writes both to `report.md` and `status.json`, and exits **65** with the conversation checkpointed. Codex runs the command itself, under its own sandbox and approval, and resumes the delegate with the output:

```bash
python3 scripts/corvee run --resume <run-dir> --command-result output.txt
```

Refusing is a legitimate answer: resume with a file that says so. The output is injected as evidence and labelled as such, never as instructions, and is capped at 100 KB. A resume of a suspended run without `--command-result` is refused rather than silently continuing with the question unanswered.

An `--env-file` may override the API URL only when it also supplies the API key, so a repository-local `.env` cannot redirect an externally configured credential to another host.

## Release checks

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
ruff check scripts tests
python3 scripts/corvee --help
python3 scripts/corvee.py --version
python3 scripts/package.py
```

CI runs the same checks on Linux and macOS across Python 3.11-3.13. Tests use a mock provider and no real key. Live checks are opt-in. Packaging creates a filtered archive and SHA-256 checksum in `dist/`.

This is an independent community skill, not an official OpenAI or Corvex product.

Licensed under the [MIT License](LICENSE).
