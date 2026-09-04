# Corvex Delegate

A Codex skill for planning in Codex and executing bounded repository tasks through Corvex's OpenAI-compatible API. Includes model discovery, protected credential setup, persistent target tracking, and bounded execution loops. No OMP or third-party agent framework is required.

## Requirements

Python 3.11+, Git, ripgrep (`rg`), Codex with local skill support, and a Corvex API key. The Python runtime uses only the standard library. Linux and macOS are supported; Windows credential permissions have not been validated.

## Install

Clone the repository and run the installer:

```bash
git clone https://github.com/aallamaa/corvex-delegate.git
cd corvex-delegate
python3 scripts/install.py
```

The installer confirms `https://api.tokenfactory.corvex.cloud/v1`, accepts the key through hidden input, lists models, optionally saves a model, and verifies authentication with a one-token inference request before installation. This can incur a small provider charge. Model listing is public and cannot validate a key.

Default installation: `~/.codex/skills/corvex-delegate/`. Use `--codex-home PATH` for a different Codex home, or `--force` to replace an existing installation. Restart Codex if the skill does not appear. The installer copies only package resources, excluding development artifacts and credentials.

For an existing protected environment:

```bash
python3 scripts/install.py --non-interactive --api-key-env CORVEX_API_KEY
```

A standard Codex skill installer can also install this folder; run `configure` afterward. Discovering a skill does not automatically run its wizard.

## Use in Codex

```text
$corvex-delegate configure
$corvex-delegate models
$corvex-delegate select exact-provider-model-id
$corvex-delegate target Make the service restart without losing queued jobs
$corvex-delegate analyze
$corvex-delegate loop --max-iterations 6 --max-time 120m
$corvex-delegate status
```

Other instructions: `check`, `refine`, `run` (one iteration), and `audit`. These are skill arguments, not slash commands. `select auto` clears the default rather than choosing a model automatically.

Codex maintains acceptance criteria, missions, and progress in `.codex/corvex-delegate/` in the target repository. A loop ends at independently verified completion or its budget/blocking boundary. Loops run in the active Codex session, not as a background service.

## CLI

One public entry point handles setup and individual missions:

```bash
python3 scripts/corvex-delegate configure
python3 scripts/corvex-delegate models
python3 scripts/corvex-delegate select exact-provider-model-id
python3 scripts/corvex-delegate show
python3 scripts/corvex-delegate check
python3 scripts/corvex-delegate run --mission /path/mission.md --cwd /path/repo --max-time 20m
```

Use `COMMAND --help` for options. `--config PATH` selects another settings file. Target planning, audit, and loops are interpreted by Codex; the CLI runs individual missions, not the entire orchestration workflow.

## Configuration and data handling

Settings live in `${CODEX_HOME:-~/.codex}/corvex-delegate/config.toml`; the key lives beside them in mode-0600 `credentials.toml`. `CORVEX_API_KEY` overrides the stored key. Never paste keys into chat or commit credentials.

Mission text and tool results are sent to the configured provider. File tools operate inside the selected repository; read-only mode omits editing tools. Commands are disabled unless enabled with `--allow-command`. This is not an OS sandbox or a comprehensive secret scanner: delegate only content you may share and use a sanitized checkout where appropriate. Codex independently verifies returned work.

## Native agents

The supported workflow uses the direct runner. Cross-provider native delegation failed in Codex CLI 0.153.2: an OpenAI parent spawned the custom role but the child retained OpenAI's provider. A separate Codex process configured for Corvex worked. Experimental helpers remain for investigation; normal installation does not install a native agent or modify the primary Codex provider. See [compatibility evidence](references/native-compatibility.md).

## Release checks

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/corvex-delegate --help
python3 scripts/package.py
```

Tests use a mock provider and no real key. Live checks are opt-in. Packaging creates a filtered archive and SHA-256 checksum in `dist/`.

This is an independent community skill, not an official OpenAI or Corvex product.

Licensed under the [MIT License](LICENSE).
