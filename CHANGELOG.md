# Changelog

All notable changes to this skill are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Write protection now rejects a `.git` component **anywhere** in the path and
  matches case-insensitively. The first version anchored the check at the
  repository root and compared case-sensitively, so
  `vendor/lib/.git/hooks/post-checkout` (submodules and vendored checkouts) and
  `.GIT/hooks/pre-commit` (APFS, NTFS) both slipped through to code execution.
  A submodule's `.git` pointer *file* is covered too. Directories merely named
  `git` remain writable.
- `run_command` refuses configuration flags that turn an allow-listed binary
  into an arbitrary-command runner: `git -c core.pager=...`, `--config-env`,
  `--exec-path`, `ssh -o/-F`, `rsync -e`. Allow-listing `git` no longer implies
  shell access.
- `PYTHONPATH` removed from the command environment allow-list; with `--write`
  it let a delegate drop `sitecustomize.py` and have any allow-listed
  interpreter import it.
- An `--env-file` may now set the API URL only if it also supplies the API key.
  A repo-local `.env` could otherwise aim the user's real `CORVEX_API_KEY` at
  an attacker's host, exfiltrating it in the first `Authorization` header.
- The final model report is redacted before printing to stdout, not only when
  written to `report.md`.
- The grep fallback passes relative paths, so provider-visible output no longer
  echoes the user's absolute home directory (ripgrep already did this).

- Write tools (`write_file`, `replace_text`) now refuse `.git/` and
  `.codex/corvee/reports/` after symlink resolution. A delegate in `--write`
  mode could previously install a git hook or set `core.sshCommand`, gaining
  code execution on the user's next git operation without any
  `--allow-command` grant, and could overwrite the report and checkpoint files
  that the orchestrator audits. Both trees remain readable.
- `run_command` builds the child environment from an explicit allow-list
  instead of dropping names containing `KEY`/`TOKEN`/`SECRET`/`PASSWORD`. The
  substring rule passed through `SSH_AUTH_SOCK`, `CORVEX_API_URL`, and any
  credential-bearing variable whose name did not advertise itself.

### Fixed

- `--timeout` accepts `30s`/`30m`/`2h` durations on the configuration
  subcommands, matching `run`. `corvee check --timeout 30m` previously failed
  with `invalid int value` despite the documented syntax.
- The grep fallback batches files into chunked processes instead of spawning
  one `grep` per file, and the truncation cap now short-circuits the walk.
- `run_process` honours `capture_output=False` instead of always piping.
- `_force_remove_tree` bounds its recursion depth during report cleanup.
- `parse_duration` caps durations at 30 days and rejects non-ASCII digits.
  `--max-time 999999999h` previously crashed with an uncaught `OverflowError`
  from `signal.setitimer`; `٣٠m` parsed as 1800 seconds.
- `install_native_agent` raises `ConfigError` instead of an unhandled
  `ValueError` when the managed provider markers appear in the wrong order.
- `install.py` stages its backup in a `mkdtemp` directory rather than a
  PID-predictable path that could collide with a leftover directory.
- Tool steps no longer write a full-history checkpoint twice in a row; the
  `response_received` snapshot was superseded by `tool_pending` immediately.

### Changed

- Provider HTTP lives in one place. `corvee.py` and `corvee_config.py` each
  had their own urllib call plus its own `try/except` ladder — four ladders
  that had drifted apart, so the runner and the configuration commands could
  disagree about whether the same failure was worth retrying. There is now a
  single `provider_request` / `request_json` transport with one `TransportError`
  classification; `ProviderFailure` is an alias of it, and configuration
  commands render the same error for a human audience. Response bodies are
  still closed unread, since an error page can echo the Authorization header.

- `install-agent` is reversible: `remove-agent` strips the managed provider
  block from `~/.codex/config.toml` and deletes the custom-agent file. It was
  previously a one-way edit to the user's own configuration.
- Wrap-up requests omit the tool schemas instead of sending them with
  `tool_choice: "none"`, which some OpenAI-compatible servers ignore.
- The runner writes a `.codex/.gitignore` excluding `corvee/reports/` when it
  first creates a run directory. Checkpoints embed repository content, and the
  docs warned against publishing them while defaulting to a committable path.
- `select` with no argument absorbed the identical `show` command.
- Removed `run --models`, which duplicated the `models` subcommand and
  overloaded `--model` as a substring filter.
- A dotenv must name `CORVEX_MODEL`; the generic `MODEL` key is ignored.
- `fail()` is defined once in `corvee_config`. The two SIGALRM deadline
  managers and the two `atomic_write` variants are deliberately NOT unified:
  `execution_deadline` must raise `SystemExit` so `except Exception` cannot
  swallow it, and `RepositoryTools.atomic_write` preserves each file's mode so
  delegate edits do not strip execute bits.

### Fixed (runner)

- A retryable provider failure inside the reporting reserve aborted with exit
  124 before the wrap-up prompt was ever sent, discarding the partial report
  the reserve exists to buy.

### Added

- `--version` on `scripts/corvee`, the documented entry point, as well as on
  `scripts/corvee.py`.
- `remove-agent` and its round-trip tests.
- CI across Linux/macOS on Python 3.11-3.13, plus a `ruff` lint job.
- Regression tests for write protection, the command environment allow-list,
  grep batching and its symlink boundary, and duration parsing.

## [0.1.0]

- Initial release.
