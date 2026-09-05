#!/usr/bin/env python3
"""Install the managed Corvex provider and native Codex custom-agent definition."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import tomllib

from corvee_config import ConfigError, atomic_write


PROVIDER_BEGIN = "# BEGIN corvee managed provider"
PROVIDER_END = "# END corvee managed provider"


def _provider_block(base_url: str, credential_helper: Path, config_path: Path) -> str:
    return "\n".join(
        [
            PROVIDER_BEGIN,
            "[model_providers.corvex]",
            'name = "Corvex Token Factory"',
            f"base_url = {json.dumps(base_url.rstrip('/'))}",
            'wire_api = "responses"',
            "request_max_retries = 4",
            "stream_max_retries = 5",
            "",
            "[model_providers.corvex.auth]",
            f"command = {json.dumps('/usr/bin/env')}",
            "args = ["
            + ", ".join(
                json.dumps(value)
                for value in (
                    "python3",
                    str(credential_helper.resolve()),
                    "--config",
                    str(config_path.resolve()),
                )
            )
            + "]",
            "timeout_ms = 5000",
            "refresh_interval_ms = 0",
            PROVIDER_END,
        ]
    )


def _agent_config(model: str, reasoning_effort: str) -> str:
    return "\n".join(
        [
            'name = "corvee"',
            'description = "Bounded Corvex implementation and repository-analysis delegate."',
            f"model = {json.dumps(model)}",
            'model_provider = "corvex"',
            f"model_reasoning_effort = {json.dumps(reasoning_effort)}",
            'developer_instructions = """',
            "Execute only the bounded task delegated by the parent Codex agent.",
            "Inspect repository evidence before conclusions. Preserve unrelated user changes.",
            "Do not access credentials, expand scope, commit, push, release, or perform production operations.",
            "Finish with a concise evidence report: result, files changed, checks run, failures, and uncertainties.",
            '"""',
            "",
        ]
    )


def install_native_agent(
    *,
    codex_config: Path,
    agent_file: Path,
    credential_helper: Path,
    delegate_config: Path,
    base_url: str,
    model: str,
    reasoning_effort: str,
) -> None:
    existing = codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""
    try:
        parsed = tomllib.loads(existing) if existing.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot update invalid Codex configuration {codex_config}: {exc}") from exc

    begin_count = existing.count(PROVIDER_BEGIN)
    end_count = existing.count(PROVIDER_END)
    if begin_count != end_count or begin_count > 1:
        raise ConfigError("Codex config has malformed corvee provider markers")
    if begin_count == 1 and existing.index(PROVIDER_END) < existing.index(PROVIDER_BEGIN):
        raise ConfigError("Codex config has corvee provider markers in the wrong order")
    block = _provider_block(base_url, credential_helper, delegate_config)
    if begin_count == 1:
        start = existing.index(PROVIDER_BEGIN)
        end = existing.index(PROVIDER_END, start) + len(PROVIDER_END)
        updated = existing[:start] + block + existing[end:]
    else:
        providers = parsed.get("model_providers", {})
        if isinstance(providers, dict) and "corvex" in providers:
            raise ConfigError(
                "Codex config already defines model_providers.corvex outside managed markers"
            )
        separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + separator + block + "\n"

    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"generated Codex configuration is invalid: {exc}") from exc
    mode = stat.S_IMODE(codex_config.stat().st_mode) if codex_config.exists() else 0o600
    atomic_write(codex_config, updated, mode)
    atomic_write(agent_file, _agent_config(model, reasoning_effort), 0o600)


def remove_native_agent(codex_config: Path, agent_file: Path) -> str:
    """Strip the managed provider block and custom-agent file installed earlier.

    Without this, `install-agent` is a one-way door: it edits the user's own
    ~/.codex/config.toml, and hand-editing TOML to undo it is exactly the kind
    of step that leaves a broken provider behind.
    """
    removed = []
    existing = codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""
    begin_count = existing.count(PROVIDER_BEGIN)
    end_count = existing.count(PROVIDER_END)
    if begin_count != end_count or begin_count > 1:
        raise ConfigError("Codex config has malformed corvee provider markers; remove them by hand")
    if begin_count == 1:
        start = existing.index(PROVIDER_BEGIN)
        end = existing.index(PROVIDER_END, start) + len(PROVIDER_END)
        updated = existing[:start] + existing[end:]
        # Collapse the blank run the removed block leaves behind.
        while "\n\n\n" in updated:
            updated = updated.replace("\n\n\n", "\n\n")
        try:
            tomllib.loads(updated)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"removing the provider block left invalid TOML: {exc}") from exc
        mode = stat.S_IMODE(codex_config.stat().st_mode)
        atomic_write(codex_config, updated.lstrip("\n"), mode)
        removed.append(f"provider block in {codex_config}")
    if agent_file.is_file():
        agent_file.unlink()
        removed.append(f"custom agent {agent_file}")
    if not removed:
        return "Nothing to remove: no managed provider block or custom agent found."
    return "Removed " + "; ".join(removed) + "\nRestart Codex so it forgets the corvee agent."


def native_agent_status(codex_config: Path, agent_file: Path) -> str:
    provider = False
    if codex_config.exists():
        content = codex_config.read_text(encoding="utf-8")
        provider = PROVIDER_BEGIN in content and PROVIDER_END in content
    return f"Provider: {'installed' if provider else 'not installed'}\nAgent: {'installed' if agent_file.is_file() else 'not installed'}"
