#!/usr/bin/env python3
"""Run a bounded repository agent against an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib import error, request

from corvex_delegate_config import (
    ConfigError,
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    default_config_path,
    load_config,
    load_env_file,
    resolve_api_key,
    validate_base_url,
    open_request,
)


MAX_FILE_BYTES = 200_000
MAX_TOOL_OUTPUT = 30_000


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def parse_duration(value: str) -> int:
    if not value:
        raise argparse.ArgumentTypeError("duration cannot be empty")
    suffix = value[-1]
    multipliers = {"s": 1, "m": 60, "h": 3600}
    if suffix in multipliers:
        number = value[:-1]
        multiplier = multipliers[suffix]
    else:
        number = value
        multiplier = 1
    if not number.isdigit() or int(number) < 1:
        raise argparse.ArgumentTypeError("expected a positive duration such as 30m")
    return int(number) * multiplier


def truncate(value: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[truncated {len(value) - limit} characters]"


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120) -> None:
        self.base_url = validate_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def call(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "codex-corvex-delegate/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.base_url}{endpoint}", data=body, headers=headers, method=method
        )
        try:
            with open_request(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read(MAX_TOOL_OUTPUT).decode("utf-8", errors="replace")
            detail = detail.replace(self.api_key, "[REDACTED]")
            fail(f"Provider HTTP {exc.code}: {truncate(detail)}", 1)
        except error.URLError as exc:
            fail(f"Provider request failed: {exc.reason}", 1)
        except json.JSONDecodeError:
            fail("Provider returned invalid JSON", 1)


class RepositoryTools:
    def __init__(self, root: Path, write: bool, allowed_commands: set[str]) -> None:
        self.root = root.resolve()
        self.write = write
        self.allowed_commands = allowed_commands

    def safe_path(self, value: str, *, allow_missing: bool = False) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=not allow_missing)
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ValueError(f"path is outside repository root: {value}")
        return resolved

    def schemas(self) -> list[dict[str, Any]]:
        tools = [
            function_tool(
                "read_file",
                "Read a UTF-8 text file inside the repository with line numbers.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "line_count": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            function_tool(
                "list_files",
                "List repository files, optionally filtered by a glob.",
                {
                    "type": "object",
                    "properties": {"glob": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
            function_tool(
                "search_text",
                "Search repository text with a regular expression.",
                {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "glob": {"type": "string"},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            ),
            function_tool(
                "git_status",
                "Show concise git working-tree status.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            function_tool(
                "git_diff",
                "Show the current unstaged git diff, optionally for one repository path.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
        ]
        if self.write:
            tools.extend(
                [
                    function_tool(
                        "replace_text",
                        "Replace an exact text occurrence in an existing repository file.",
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                                "expected_occurrences": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "required": ["path", "old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    ),
                    function_tool(
                        "write_file",
                        "Create or fully overwrite a UTF-8 file inside the repository.",
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                            "additionalProperties": False,
                        },
                    ),
                ]
            )
        if self.allowed_commands:
            tools.append(
                function_tool(
                    "run_command",
                    "Run one explicitly allow-listed executable without a shell.",
                    {
                        "type": "object",
                        "properties": {
                            "argv": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": 100,
                            },
                            "cwd": {"type": "string"},
                            "timeout_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 900,
                            },
                        },
                        "required": ["argv"],
                        "additionalProperties": False,
                    },
                )
            )
        return tools

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            method = getattr(self, f"tool_{name}")
        except AttributeError:
            return json.dumps({"ok": False, "error": f"unknown or disabled tool: {name}"})
        try:
            result = method(**arguments)
            return json.dumps({"ok": True, "result": truncate(result)}, ensure_ascii=False)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except TypeError as exc:
            return json.dumps({"ok": False, "error": f"invalid tool arguments: {exc}"})

    def tool_read_file(self, path: str, start_line: int = 1, line_count: int = 300) -> str:
        target = self.safe_path(path)
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} byte read limit: {path}")
        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1 : start_line - 1 + line_count]
        return "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start_line))

    def tool_list_files(self, glob: str = "*") -> str:
        rg = shutil.which("rg")
        if rg:
            result = subprocess.run(
                [rg, "--files", "-g", glob],
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode not in (0, 1):
                raise ValueError(result.stderr.strip() or "rg --files failed")
            return result.stdout
        matches: list[str] = []
        for path in self.root.rglob("*"):
            if path.is_file():
                relative = str(path.relative_to(self.root))
                if fnmatch.fnmatch(relative, glob) or fnmatch.fnmatch(path.name, glob):
                    matches.append(relative)
                    if len(matches) >= 1000:
                        break
        return "\n".join(matches)

    def tool_search_text(self, pattern: str, glob: str = "*") -> str:
        rg = shutil.which("rg")
        if not rg:
            raise ValueError("search_text requires ripgrep (rg)")
        result = subprocess.run(
            [rg, "-n", "--no-heading", "--color", "never", "-g", glob, pattern, "."],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise ValueError(result.stderr.strip() or "rg failed")
        return result.stdout

    def tool_git_status(self) -> str:
        return self.fixed_command(["git", "status", "--short", "--branch"])

    def tool_git_diff(self, path: str | None = None) -> str:
        command = ["git", "diff", "--"]
        if path:
            target = self.safe_path(path, allow_missing=True)
            command.append(str(target.relative_to(self.root)))
        return self.fixed_command(command)

    def tool_replace_text(
        self, path: str, old_text: str, new_text: str, expected_occurrences: int = 1
    ) -> str:
        if not self.write:
            raise ValueError("write tools are disabled")
        target = self.safe_path(path)
        original = target.read_text(encoding="utf-8")
        actual = original.count(old_text)
        if actual != expected_occurrences:
            raise ValueError(
                f"expected {expected_occurrences} occurrences, found {actual}; no change made"
            )
        self.atomic_write(target, original.replace(old_text, new_text))
        return f"replaced {actual} occurrence(s) in {target.relative_to(self.root)}"

    def tool_write_file(self, path: str, content: str) -> str:
        if not self.write:
            raise ValueError("write tools are disabled")
        target = self.safe_path(path, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.atomic_write(target, content)
        return f"wrote {len(content.encode('utf-8'))} bytes to {target.relative_to(self.root)}"

    def tool_run_command(
        self, argv: list[str], cwd: str = ".", timeout_seconds: int = 300
    ) -> str:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must contain non-empty strings")
        executable = Path(argv[0]).name
        if executable not in self.allowed_commands:
            raise ValueError(f"executable is not allow-listed: {executable}")
        command_cwd = self.safe_path(cwd)
        if not command_cwd.is_dir():
            raise ValueError(f"command cwd is not a directory: {cwd}")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        }
        result = subprocess.run(
            argv,
            cwd=command_cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=environment,
        )
        return (
            f"exit_code={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def fixed_command(self, argv: list[str]) -> str:
        result = subprocess.run(
            argv, cwd=self.root, text=True, capture_output=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or f"command failed: {' '.join(argv)}")
        return result.stdout

    @staticmethod
    def atomic_write(target: Path, content: str) -> None:
        existing_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if existing_mode is not None:
                temporary.chmod(existing_mode)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()


def function_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--no-config", action="store_true")
    parser.add_argument("--api-key-env")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--complexity", choices=("low", "medium", "high"))
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--allow-command", action="append", default=[])
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-time", type=parse_duration)
    parser.add_argument("--http-timeout", type=int, default=120)
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = args.config.expanduser().resolve()
    try:
        config = {} if args.no_config else load_config(config_path)
    except ConfigError as exc:
        fail(str(exc))
    env_file_values: dict[str, str] = {}
    if args.env_file:
        env_path = args.env_file.resolve(strict=True)
        try:
            env_file_values = load_env_file(env_path)
        except (OSError, ConfigError) as exc:
            fail(str(exc))

    model_config_value = ""
    if args.model_config:
        model_config_path = args.model_config.resolve(strict=True)
        try:
            model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"invalid model configuration: {exc}")
        if not isinstance(model_config, dict) or set(model_config) != {"model"}:
            fail("model configuration must contain only a 'model' field")
        configured_model = model_config["model"]
        if configured_model is not None and not isinstance(configured_model, str):
            fail("model configuration 'model' must be a string or null")
        model_config_value = configured_model or ""

    base_url = (
        args.base_url
        or os.environ.get("CORVEX_API_URL")
        or env_file_values.get("CORVEX_API_URL")
        or env_file_values.get("API_URL")
        or config.get("base_url")
        or DEFAULT_BASE_URL
    )
    model = (
        args.model
        or model_config_value
        or os.environ.get("CORVEX_MODEL")
        or env_file_values.get("CORVEX_MODEL")
        or env_file_values.get("MODEL")
        or config.get("model")
        or ""
    )
    api_key_env = args.api_key_env or config.get("api_key_env") or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env, "") or env_file_values.get(api_key_env, "")
    if not api_key:
        api_key = env_file_values.get("CORVEX_API_KEY", "") or env_file_values.get("API_KEY", "")
    if not api_key:
        try:
            api_key = resolve_api_key(config, config_path, env_file_values)
        except ConfigError as exc:
            fail(str(exc))

    if not api_key:
        fail(
            "Corvex API credential is not configured. Run "
            "scripts/corvex-delegate configure or set CORVEX_API_KEY."
        )
    client = ApiClient(base_url, api_key, timeout=args.http_timeout)

    if args.models:
        response = client.call("GET", "/models")
        models = response.get("data", []) if isinstance(response, dict) else []
        ids = sorted(
            item.get("id", "") for item in models if isinstance(item, dict) and item.get("id")
        )
        if args.model:
            ids = [model_id for model_id in ids if args.model.lower() in model_id.lower()]
        print("\n".join(ids))
        return 0

    if not model:
        fail("Select a Corvex model with $corvex-delegate select or pass --model")
    if args.mission is None:
        fail("--mission is required unless --models is used")
    if args.max_steps is not None and args.max_steps < 1:
        fail("--max-steps must be positive")
    if args.http_timeout < 1:
        fail("--http-timeout must be positive")

    complexity = args.complexity or config.get("default_complexity") or "medium"
    if complexity not in {"low", "medium", "high"}:
        fail(f"invalid configured default_complexity: {complexity}")
    defaults = {
        "low": (16, 20 * 60),
        "medium": (32, 60 * 60),
        "high": (48, 120 * 60),
    }
    default_steps, default_time = defaults[complexity]
    max_steps = args.max_steps or default_steps
    max_time = args.max_time or default_time

    root = args.cwd.resolve(strict=True)
    if not root.is_dir():
        fail(f"working directory is not a directory: {root}")
    mission_path = args.mission.resolve(strict=True)
    if not mission_path.is_file():
        fail(f"mission is not a file: {mission_path}")
    if mission_path.stat().st_size > MAX_FILE_BYTES:
        fail(f"mission exceeds {MAX_FILE_BYTES} bytes")
    mission = mission_path.read_text(encoding="utf-8")
    tools = RepositoryTools(root, args.write, set(args.allow_command))

    run_description = {
        "base_url": base_url,
        "model": model,
        "config": str(config_path) if not args.no_config else "disabled",
        "mode": "write" if args.write else "read-only",
        "allowed_commands": sorted(set(args.allow_command)),
        "max_steps": max_steps,
        "max_time_seconds": max_time,
        "api_key_env": api_key_env,
        "api_key_present": True,
    }
    print(json.dumps(run_description, indent=2), file=sys.stderr)
    if args.dry_run:
        return 0

    system = (
        "You are a bounded repository delegate. Execute only the supplied mission. "
        "Use tools to inspect evidence before conclusions. Do not expand scope, access credentials, "
        "commit, push, release, or perform production operations. "
        + (
            "Repository writes are authorized only within the mission scope. "
            if args.write
            else "This is read-only: do not request or claim repository edits. "
        )
        + "Finish with a concise evidence report listing files changed, checks performed, failures, "
        "uncertainties, and whether the bounded objective is complete."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Repository root: {root}\n\nMission:\n{mission}"},
    ]
    started = time.monotonic()

    for step in range(1, max_steps + 1):
        if time.monotonic() - started >= max_time:
            fail(f"delegate exceeded max time after {step - 1} steps", 124)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools.schemas(),
            "tool_choice": "auto",
        }
        if args.effort:
            payload["reasoning_effort"] = args.effort
        response = client.call("POST", "/chat/completions", payload)
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            fail(f"provider response has no assistant message: {truncate(json.dumps(response))}", 1)
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)

        if not tool_calls:
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                fail("provider returned neither tool calls nor a final text report", 1)
            print(content)
            return 0

        for tool_call in tool_calls:
            call_id = tool_call.get("id", "missing-call-id")
            function = tool_call.get("function") or {}
            name = function.get("name", "")
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = (
                    raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
                )
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                result = tools.execute(name, arguments)
            except (json.JSONDecodeError, ValueError) as exc:
                result = json.dumps({"ok": False, "error": f"invalid tool call: {exc}"})
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

    fail(f"delegate exceeded maximum of {max_steps} model steps", 124)


if __name__ == "__main__":
    raise SystemExit(main())
