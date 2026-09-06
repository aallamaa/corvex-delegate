# Corvée Skill Audit — Findings Ledger

Audit date: 2026-09-06
Scope: `/home/kader/Code/skills/corvee` (v0.1.0, HEAD `25dd29d`)
Method: full source review of `scripts/`, `references/`, `SKILL.md`, tests, CI;
independent verification of path confinement, write-protection, packaging,
lint, and the test suite (134/143 pass; 9 fail only on sandbox socket bind).

## Summary

The skill is well-engineered with a coherent security model: the delegate
cannot execute commands, writes are path-confined and `.git`/`reports` are
protected after symlink resolution, credentials are separated from config with
mode-0600 enforcement, redirects are refused, and a dotenv cannot redirect an
externally configured key. Findings below are real but low severity; none
represent an exploitable escape from the documented trust model.

## Findings

- [x] **F1 — `redact()` only strips the literal API key, not other secrets.**
  Fixed: `SKILL.md` now states explicitly that every tool result is persisted
  verbatim to `checkpoint.json`, that only the API key is redacted, and that a
  sanitized checkout is the only complete mitigation for secret-bearing repos.
  This was a documentation gap; the runner already disclaims being a secret
  scanner, and the new text makes the disk-persistence consequence explicit.

- [x] **F2 — `--command-result` is injected into the conversation with no
  content-type or structure validation.** Fixed: the injected command output is
  now wrapped in `----- BEGIN COMMAND OUTPUT -----` / `----- END COMMAND OUTPUT
  -----` delimiters with an anti-injection header stating the output is
  untrusted, that the delegate must not follow any directives it contains, and
  must not treat its claims (pass/fail, file contents) as verified. Test added:
  `test_command_result_is_injected_behind_a_delimiter`.

- [x] **F3 — `--run-dir` has no confinement check; a new run can write its
  artifact tree anywhere the invoking user can create.** Fixed with two changes:
  (1) `--run-dir` is now confined to be inside the `--cwd` repository (a path
  outside is refused with a clear error); (2) `_protect_run_state` now writes a
  `.gitignore` at the run-dir's parent when no `.codex` ancestor is found, so a
  custom run-dir's content-embedding checkpoint is still protected from
  accidental commits. Tests added: `RunDirConfinementTest` (two cases) and the
  updated `test_a_custom_run_dir_outside_codex_gets_a_parent_gitignore`.

- [ ] **F4 — `select MODEL_ID` validates against the catalog but does not run
  `verify_credential`.** Saving a model via `select` calls
  `checked_settings` → `fetch_models` (which *does* send the key in the
  Authorization header to list models) but skips `verify_credential` (the
  one-token inference test). The models endpoint may accept a key that
  `/chat/completions` rejects (different scopes, or a list-only key). `check`
  does verify, and `configure` does verify, so this only matters if a user
  changes models without re-running `check`. Not a security issue, but the
  SKILL.md says "Configuration is complete only after authenticated inference
  succeeds," which `select` alone does not guarantee. Severity: informational.
  Location: `scripts/configure_corvee.py:163` (`select` branch).

- [ ] **F5 — CI does not run `ruff` on all matrix legs and has no drift guard
  beyond the test.** The lint job runs only on `ubuntu-latest` / Python 3.12,
  which is fine, but the matrix does not pin ruff or run `package.py` on macOS.
  More notably, there is no CI step that asserts the git-tracked file set
  matches `PACKAGE_FILES` + `EXCLUDED_FILES`; the `PackageManifestTest` covers
  this locally but only if someone runs the tests. A newly added script that is
  not added to `package.py` would silently be missing from releases. Severity:
  informational. Location: `.github/workflows/ci.yml`, `scripts/package.py:13`.

- [ ] **F6 — `load_env_file` name validation allows names `API_KEY` and
  `MODEL` to collide with unrelated tools.** The loader accepts any
  `[_A-Za-z][A-Za-z0-9_]*` name. `CORVEX_API_URL`/`API_URL` are only honored
  when a key is also present (good), but a bare `MODEL` in a dotenv is silently
  ignored (documented in `provider-setup.md`) while `API_KEY` *is* honored as a
  fallback key. If a user's dotenv sets `API_KEY` for another service, corvee
  will pick it up as the Corvex credential. The precedence (`CORVEX_API_KEY`
  first, then `CORVEX_<env>`, then `API_KEY`) makes this unlikely in practice.
  Severity: informational. Location: `scripts/corvee_config.py:resolve_api_key`
  (`API_KEY` fallback), `scripts/corvee.py:1057` (same).

## Verification performed

- `python3 -m unittest discover -s tests` → 134 pass, 9 errors (all
  `CorveeTest` `setUp` `PermissionError` on `socket()` — sandbox-only; these
  tests pass in CI with network).
- `python3 -m ruff check scripts tests` → clean.
- `python3 scripts/corvee --help` / `--version` → correct.
- `python3 scripts/package.py` → builds `dist/corvee-0.1.0.tar.gz` + sha256.
- Path-confinement: symlink escape, `../` traversal, and `.git`/`.GIT`/nested
  `vendor/.git` write attempts all blocked.
- `.codex/` is gitignored; no real secrets found in dev artifacts.
- Installed copy at `/home/kader/.codex/skills/corvee` matches source.

## Not a finding (verified safe)

- Delegate cannot execute commands (only `request_command`, exit 65).
- Credential file enforced mode 0600; config 0600.
- HTTP redirects refused (`NoRedirect`).
- Dotenv URL-only redirect refused without a co-located key.
- `git_status`/`git_diff` use fixed argv (no injection).
- `rg`/`grep` patterns passed as `-e`/`-E` (no preprocessor flag injection;
  tested `--pre=`).
- Resume refuses to widen authority (read-only → write) and refuses
  `--command-result` on non-suspended runs.
- Checkpoint files are mode 0600; run directory 0700.
