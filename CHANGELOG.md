# Changelog

All notable changes to this skill are recorded here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Token cost per mission cut by about a quarter, measured over eight runs of a
  fixed mission against `zai-org/GLM-5.2-FP8`: mean input 46,302 -> 35,734,
  median 42,504 -> 32,044, with answer quality unchanged (every run still named
  all eight tools, quoted the gating condition and cited line numbers).
  - The system prompt asks for frugality: search before reading, read ranges
    rather than whole files, stop when the mission is answered. The opposite
    instruction -- to batch independent tool calls, which the loop has always
    supported -- was measured first and made things markedly worse (input
    tokens roughly doubled, to 51,056) because the model read speculatively.
    That is why the prompt says what it says.
  - Tool schema descriptions trimmed to what changes behavior, and the
    `additionalProperties` / `minimum` / `maximum` fields dropped, since the
    runtime enforces those bounds anyway. Read-only schema 2,036 -> 1,627
    characters, write 2,784 -> 2,313; that is re-sent on every request.
  - `prune_tool_history` caps retained tool results at `MAX_HISTORY_TOOL_BYTES`.
    Every result is re-sent on each later turn, so history costs quadratically
    and one greedy listing early in a run is billed again on every turn after
    it. Past the budget the oldest results become a stub inviting a re-read.
    Only `content` changes, so every `tool_call_id` keeps its answer and a
    pruned conversation still resumes from its checkpoint.
- README documents why the runner exists rather than `codex exec`, with the
  benchmark behind it and the reproduction recipe in `.codex/bench/`.

### Fixed

- Report cleanup failed on Python 3.11, so CI had been red on that leg for ten
  commits. `_remove_report_dir` handed `shutil.rmtree` an error handler, but
  rmtree reports a permission failure against the *child* it could not unlink,
  and relaxing that child's mode does not help -- the missing write bit is on
  the parent directory. Which path the handler receives also differs between
  3.11 and 3.12+, so the same code passed on one and failed on the other. The
  tree is now walked directly by `_force_remove_tree`, which relaxes each
  directory before descending into it, and the version branch is gone. A test
  covers a read-only directory nested inside a read-only directory.

### Changed

- The delegate can no longer run commands. `run_command`, `--allow-command`,
  the environment allow-list and the option denylist are all gone -- roughly
  160 lines of runner and 200 of tests whose whole job was to re-implement, in
  Python, a weak version of process isolation. The denylist could not hold in
  principle: no flag list makes `git` safe when `git config alias.x '!cmd'`
  followed by `git x` uses no flag at all. The protocol already required the
  planner to rerun every gate command itself, so this removes a second and
  worse execution path rather than a capability. Execution now happens only in
  the planner's session, where the user's own sandbox and approval apply.
- New `request_command` tool in its place. It executes nothing: it records a
  command and a reason, writes both to `report.md` and `status.json`, and stops
  the run at the new exit code `65` with the conversation checkpointed. The
  planner runs the command if it chooses to -- refusing is a legitimate answer
  -- and resumes with `run --resume <run-dir> --command-result <file>`. The
  output is injected as evidence, explicitly labelled not-instructions, capped
  at 100 KB. Resuming a suspended run without `--command-result` is refused
  rather than silently continuing with the question unanswered, and the flag is
  refused on a run that did not ask for anything.
- The stop happens only after every tool call in the batch is answered: an
  assistant message with an unanswered tool call is not a resumable
  conversation.

### Security

- `COMMAND_ARGUMENT_DENYLIST` matched only the bare option token, so every
  denied flag was reachable in a spelling the parser treats identically:
  `ssh -oProxyCommand=/bin/sh` (glued) and `ssh -nNo ProxyCommand=...`
  (clustered) both ran, while the separated `-o` form was refused. Matching now
  covers all three spellings.
- Added the options that were simply missing: `git -u` (short `--upload-pack`),
  `git archive --exec`, `git --git-dir` / `--work-tree` (which point git at a
  config file the delegate wrote, and so at `core.fsmonitor`), `ssh -I` (which
  `dlopen()`s a PKCS#11 library, running its constructor before any network
  traffic), and entries for `find` and `tar`, which were absent although
  `-exec` and `--to-command` are the same class of escape.
- Documented what the list is not. It closes the best-known one-liners and
  nothing more: `git config alias.x '!cmd'` followed by `git x` uses no flag at
  all. The README previously implied the risk was confined to interpreters,
  compilers and build tools, which read as a guarantee about `git` and `ssh`
  that was never true. Allow-listing a command grants everything that command
  can do.

### Fixed

- `MAX_TOOL_OUTPUT` was enforced as a character count while every neighbouring
  limit, every message quoting it, and the delegate byte ledger meant UTF-8
  bytes. A CJK or emoji file passed roughly three times the intended cap
  straight to the provider -- the payload the limit exists to bound. `truncate`,
  the `read_file` window budget and the `grep_search` accumulator now all
  measure bytes, and `truncate` will not split a codepoint.
- `read_file` appended its truncation notice after spending the whole budget,
  pushing the result past the cap so `truncate` cut it again -- through the
  notice, destroying the "continue from a later start_line" guidance that tells
  the delegate how to page on. The notice is reserved inside the budget now.
- `atomic_write` promised a new file created with `mode=None` would get the
  process umask, but `NamedTemporaryFile` always creates at 0600 and nothing
  reset it, so delegate-created files landed unreadable to anyone else. It
  applies the umask now, and still preserves an existing file's permissions.

- A provider response whose body died mid-read raised
  `http.client.IncompleteRead`, which descends from `Exception`, not `OSError`,
  and so slipped past every clause in `request_json`. It reached `main()` as an
  unhandled internal error and exited 1 instead of being classified as a
  retryable transport failure, so the retry path never engaged for the one
  failure it most obviously covers. `http.client.HTTPException` is now caught
  and classified `connection_error`.

- `RunJournal.summarize` matched event names the runner never emits (`error`,
  `tools_disabled`, `budget_warning`, `tool_error`) and misspelled `wrap_up` as
  `wrapup`. Every outcome-changing event -- wrap-up, wrap-up rejection, stalls,
  request errors and retries -- was therefore dropped from stderr instead of
  summarized, which is a failure that shows up as silence. The names now live in
  `SUMMARIZED_EVENTS`, and a test asserts every one of them is a name the runner
  actually emits so they cannot drift apart again.
- The cleanup tilde-expansion test built its fixture inside the developer's real
  home directory and leaked it if interrupted. It now gives the subprocess its
  own `HOME`.

### Removed

- The experimental native-Codex-subagent path is gone: `scripts/native_agent.py`,
  `scripts/credential_helper.py`, `agents/openai.yaml`,
  `references/native-compatibility.md`, `probe_responses_api`, and the
  `check-responses` / `install-agent` / `remove-agent` / `agent-status`
  subcommands. Its own recorded test said cross-provider spawn failed on Codex
  CLI 0.153.2, so it was ~350 lines of code and three documentation sections
  supporting a feature that did not work. The supported path is the direct
  runner. Anyone who ran `install-agent` before this release should remove the
  managed provider block from `~/.codex/config.toml` and delete
  `~/.codex/agents/corvee.toml` by hand.

### Fixed

- The runner no longer echoes every journal event to stderr. The planner reads
  this process's stderr as a tool result, so a 32-step run spent the planner
  context the runner exists to save. Stderr now carries one line per run
  boundary and per error; `events.jsonl` still holds the complete record, and
  `--verbose` restores the old behavior.
- `list_files` without ripgrep walked the tree with `rglob("*")`, descending
  into `.git/` and following symlinks out of the repository. In any real
  repository the 1000-entry cap was exhausted by loose objects before a source
  file was reached. It now skips hidden entries and symlinks, matching the
  search fallback and the behavior both `SKILL.md` and `README.md` already
  claimed.
- `--http-timeout` accepts `30s`/`30m`/`2h` like every other duration option;
  it was `type=int` and crashed on a suffix that the README documented.
- `--resume` restores the original run's `--write` mode and `--allow-command`
  list from the checkpoint instead of failing and making the user reconstruct
  them. A flag that conflicts with the checkpoint is still refused, so a resume
  cannot silently widen or narrow a delegate's authority.

### Removed

- `tests/fixtures/live_readonly_mission.md`: nothing has read it since the live
  integration tests were dropped, and it survived only as an entry in the
  package manifest that kept the manifest test quiet. Its text still described
  the skill in terms of an OMP dependency that no longer appears anywhere.
- The `--http-timeout` and `--timeout` positivity checks. `parse_duration` is
  the only producer of both values and already refuses anything below one
  second, with a test covering it; the downstream guards could not fire.

### Changed

- `build_provider_request` no longer takes `accept` and `user_agent`: only the
  deleted SSE probe ever passed anything but the default. `provider_request`
  went with it -- it existed to hand a live stream to that probe, had one
  caller, and is now folded into `request_json`.
- `tool_list_files` and `grep_search` had the same filtered directory walk
  copied into both; it is now `RepositoryTools.walk_visible_files`. The
  redundant `safe_path` re-validation of a path just produced by walking the
  root is gone -- skipping symlinks is what actually bounds that walk.
- `main()` drops from ~245 to ~178 lines: provider settings resolution
  (model/credential/endpoint precedence, including the dotenv redirection rule)
  and budget resolution are now `resolve_provider_settings` and
  `resolve_budget`, with the complexity table as a module constant.
- Test suite: one shared `without_ripgrep()` context manager replaces five
  hand-rolled copies, one `FakeResponse` replaces two inline `Body` doubles, a
  `CleanupTest.aged_report` helper replaces the repeated fixture boilerplate,
  and the mock provider's dead `/responses` SSE branch is gone.
- `SKILL.md` separates the six CLI-backed instructions from the six planning
  steps that have no script behind them; the earlier single table presented all
  twelve as if they were commands.
- The delegation-economics argument is stated once, in `control-protocol.md`,
  rather than restated in five places.

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

- The protocol now serves the cost arbitrage it was written for. The skill
  exists to move token-heavy work from Codex to a cheaper provider, but the
  planner's own cost grew with the volume delegated, which caps the saving:
  - Acceptance gates are commands with exit codes wherever one exists.
    Checking an exit code costs the planner the same whether the delegate
    changed three files or three hundred; judging a change by reading it does
    not.
  - Rerunning the gate command stays with the planner and is not delegable.
    `audit` covers a change too large to read; it was briefly split into a
    separate `review` instruction, which the same round then removed.
  - `analyze` now delegates repository exploration and drafting rather than
    requiring the planner to synthesise from its own reading. Exploration is
    the most token-heavy step in the loop.
  - The sizing advice was inverted. It said to split oversized work; every
    mission costs the planner a specification, a verdict and a ledger entry
    regardless of size, so splitting a well-specified change multiplies the
    expensive side of the trade. Split for ambiguity, not for size.
- `status.json` carries an `economics` object recording what the runner can
  actually observe: `mission_bytes`, `delegate_tool_bytes`,
  `delegate_tool_calls`, `report_bytes`, `diff_bytes`. `diff_bytes` measures
  the tree against `HEAD` plus untracked files, is measured on interrupted and
  failed runs too, and is null when unmeasurable, which is not zero.

- Token usage is measured instead of demanded. The runner discarded the
  provider's `usage` payload while `control-protocol.md` told the planner to
  honour a monetary cap and report "budget consumed" — an instruction with no
  mechanism behind it, which invites invented numbers. `status.json` now
  carries `requests`, `prompt_tokens`, `completion_tokens`, `total_tokens` and
  `reported_by_provider`; per-request counts land on `request_end`. A provider
  that reports nothing leaves the flag false, which means unmeasured, not zero.
  The docs now say cost caps must be enforced through step, time and iteration
  budgets, because that is all the runner can actually enforce.
- `read_file` streams to the requested window instead of sizing up the whole
  file. A 225 KB log was refused outright even for lines 1-10, which made
  paging through a large file impossible; a window now returns at most 30 KB,
  matching what a tool result can carry anyway.
- `MAX_FILE_BYTES` governed reads, writes, replaces and mission size from one
  number. Split into `MAX_TOOL_OUTPUT` for read windows, `MAX_EDIT_BYTES` for
  whole-file edits, `MAX_MISSION_BYTES`, and `MAX_LIST_ENTRIES`.
- `list_files` capped at 1000 entries only when ripgrep was absent, so the same
  tool behaved differently per host. Both backends now cap at 1000 and say when
  they truncated.
- `search_text` bounds the whole search at 120 seconds, not just each batch. An
  unbounded batch count could otherwise spend a 20-minute run inside one call.
- `SKILL.md`'s example passed `--complexity low --max-steps 16 --max-time 20m`,
  restating the exact preset it had just selected. It now shows the preset and
  documents what each one means.

- Dead parameters removed from `run_process`: `check` was never `True` and
  `capture_output` never `False` at any of its five call sites, so both are
  gone along with the redundant kwargs each caller repeated.
- One `atomic_write`. The repository writer was a near-copy that existed only
  to preserve file modes, which the shared one now does under `mode=None`.
- The launcher imports `read_version` instead of carrying a second copy.
- `configure` no longer honours a bare `MODEL` key in a dotenv. The runner and
  `provider-setup.md` already rejected it; only this path still read it.
- The grep fallback stopped truncating its own output, which `execute` does
  anyway, and lost a pair of identical return branches.

- Provider HTTP lives in one place. `corvee.py` and `corvee_config.py` each
  had their own urllib call plus its own `try/except` ladder — four ladders
  that had drifted apart, so the runner and the configuration commands could
  disagree about whether the same failure was worth retrying. There is now a
  single `provider_request` / `request_json` transport, and one `TransportError`
  raised by one classifier. The runner's `ProviderFailure` is gone rather than
  aliased: a second name for the same type only hid where the classification
  happened. Configuration commands render that same error for a human audience. Response bodies are
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

- A missing `--mission`, `--cwd`, `--env-file` or `--model-config` path printed
  a raw `FileNotFoundError` traceback instead of a message. `resolve(strict=True)`
  raised before the guard beneath it could report anything. All four now fail
  with a readable line and exit 2.
- Exit 2 is reachable from `run` for unusable arguments but was missing from
  the documented exit-code table.
- `measure_diff`'s docstring still described "the working-tree diff the planner
  would have to read", from the removed leverage metric. It measures the tree
  against HEAD plus untracked files, and the runner cannot see what the planner
  reads.

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
