#!/usr/bin/env python3
"""Run a bounded repository agent against an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any
from urllib import error, request

from corvee_config import (
    ConfigError,
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    default_config_path,
    load_config,
    load_env_file,
    resolve_api_key,
    validate_base_url,
    open_request,
    atomic_write as protected_write,
)


MAX_FILE_BYTES = 200_000
MAX_TOOL_OUTPUT = 30_000


class ProviderFailure(Exception):
    def __init__(self, category: str, retryable: bool = False):
        super().__init__(category)
        self.retryable = retryable


class RunJournal:
    """Private checkpoints contain repository content; events contain metadata only."""
    def __init__(self, directory: Path, api_key: str):
        self.directory = directory
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.api_key = api_key
        self.started = time.monotonic()
        self.step = 0
        self.messages = []
        self.phase = "starting"
        self.events = directory / "events.jsonl"
        self.events.touch(mode=0o600, exist_ok=False)

    def redact(self, text: str) -> str:
        return text.replace(self.api_key, "[REDACTED]") if self.api_key else text

    def event(self, event: str, **fields):
        record = {"event": event, "elapsed_seconds": round(time.monotonic() - self.started, 3),
                  "step": self.step, **fields}
        line = self.redact(json.dumps(record, ensure_ascii=False))
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        print(line, file=sys.stderr, flush=True)

    def checkpoint(self, messages, phase: str):
        self.messages = messages
        self.phase = phase
        protected_write(self.directory / "checkpoint.json", self.redact(json.dumps({
            "version": 1, "step": self.step, "phase": phase, "messages": messages,
            "automatic_replay_safe": False,
        }, ensure_ascii=False)))

    def finish(self, status: str, code: int):
        self.checkpoint(self.messages, self.phase)
        protected_write(self.directory / "status.json", json.dumps({
            "status": status, "exit_code": code, "step": self.step, "phase": self.phase,
        }))
        self.event("run_end", status=status, exit_code=code)
        report = self.directory / "report.md"
        if not report.exists():
            protected_write(report, f"# Incomplete run\n\nStatus: {status}; exit code: {code}.\n"
                            f"Last step: {self.step}; phase: {self.phase}.\n"
                            "No final model report was received. Inspect checkpoint.json and events.jsonl. "
                            "Do not replay pending tools without checking repository state.\n")


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


@contextmanager
def execution_deadline(seconds: float):
    """Enforce a wall-clock budget, including blocking HTTP and file operations."""
    def expired(signum, frame):
        fail("delegate exceeded max time", 124)

    previous = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                             previous_timer[1])


def run_process(argv, *, timeout, check=False, capture_output=True, text=True, **kwargs):
    """Terminate the command's process group on timeout or runner interruption."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=text, start_new_session=True, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 600) -> None:
        self.base_url = validate_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def call(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "codex-corvee/1",
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
            exc.close()
            raise ProviderFailure(f"http_{exc.code}", exc.code in {408, 429, 500, 502, 503, 504}) from None
        except TimeoutError:
            raise ProviderFailure("request_timeout", True) from None
        except error.URLError as exc:
            raise ProviderFailure("request_timeout" if isinstance(exc.reason, TimeoutError)
                                  else "connection_error", True) from None
        except (ConnectionError, OSError):
            raise ProviderFailure("connection_error", True) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ProviderFailure("invalid_json") from None


class RepositoryTools:
    def __init__(self, root: Path, write: bool, allowed_commands: set[str]) -> None:
        self.root = root.resolve()
        self.write = write
        self.allowed_commands = {}
        for name in allowed_commands:
            executable = shutil.which(name)
            if executable is None:
                raise ValueError(f"allow-listed executable not found: {name}")
            self.allowed_commands[name] = str(Path(executable).resolve(strict=True))

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
        if type(start_line) is not int or start_line < 1 or type(line_count) is not int or not 1 <= line_count <= 1000:
            raise ValueError("start_line must be positive and line_count must be between 1 and 1000")
        target = self.safe_path(path)
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} byte read limit: {path}")
        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1 : start_line - 1 + line_count]
        return "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start_line))

    def tool_list_files(self, glob: str = "*") -> str:
        rg = shutil.which("rg")
        if rg:
            result = run_process(
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
            return self.grep_search(pattern, glob)
        result = run_process(
            [rg, "-n", "--no-heading", "--color", "never", "-g", glob, "-e", pattern, "--", "."],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise ValueError(result.stderr.strip() or "rg failed")
        return result.stdout

    def grep_search(self, pattern: str, glob: str) -> str:
        grep = shutil.which("grep")
        if not grep:
            raise ValueError("search_text requires ripgrep (rg) or grep")
        output = []
        size = 0
        # Explicit files avoid recursive grep following symlinks outside the root.
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not name.startswith("."))
            for name in sorted(files):
                candidate = Path(directory) / name
                relative = str(candidate.relative_to(self.root))
                if name.startswith(".") or candidate.is_symlink() or not candidate.is_file():
                    continue
                if not (fnmatch.fnmatch(relative, glob) or fnmatch.fnmatch(name, glob)):
                    continue
                path = self.safe_path(relative)
                result = run_process(
                    [grep, "-nH", "-I", "-E", "-e", pattern, "--", str(path)],
                    cwd=self.root, text=True, capture_output=True, timeout=30,
                )
                if result.returncode not in (0, 1):
                    raise ValueError(result.stderr.strip() or "grep failed")
                output.append(result.stdout)
                size += len(result.stdout)
                if size > MAX_TOOL_OUTPUT:
                    return truncate("".join(output))
        return "".join(output)

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
        if not old_text:
            raise ValueError("old_text must be non-empty")
        if type(expected_occurrences) is not int or not 1 <= expected_occurrences <= 100:
            raise ValueError("expected_occurrences must be between 1 and 100")
        target = self.safe_path(path)
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} byte edit limit")
        original = target.read_text(encoding="utf-8")
        actual = original.count(old_text)
        if actual != expected_occurrences:
            raise ValueError(
                f"expected {expected_occurrences} occurrences, found {actual}; no change made"
            )
        replacement = original.replace(old_text, new_text)
        if len(replacement.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"replacement exceeds {MAX_FILE_BYTES} byte edit limit")
        self.atomic_write(target, replacement)
        return f"replaced {actual} occurrence(s) in {target.relative_to(self.root)}"

    def tool_write_file(self, path: str, content: str) -> str:
        if not self.write:
            raise ValueError("write tools are disabled")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"content exceeds {MAX_FILE_BYTES} byte write limit")
        target = self.safe_path(path, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.atomic_write(target, content)
        return f"wrote {len(content.encode('utf-8'))} bytes to {target.relative_to(self.root)}"

    def tool_run_command(
        self, argv: list[str], cwd: str = ".", timeout_seconds: int = 300
    ) -> str:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must contain non-empty strings")
        executable = self.allowed_commands.get(argv[0])
        if executable is None:
            raise ValueError(f"executable is not allow-listed: {argv[0]}")
        if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be an integer between 1 and 900")
        command_cwd = self.safe_path(cwd)
        if not command_cwd.is_dir():
            raise ValueError(f"command cwd is not a directory: {cwd}")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        }
        result = run_process(
            [executable, *argv[1:]],
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
        result = run_process(
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
    parser.add_argument("--http-timeout", type=int, default=600,
                        help="Per-request socket timeout in seconds (default: 600), capped by run budget")
    parser.add_argument("--request-retries", type=int, default=1,
                        help="Transient request retries (0-2); may incur duplicate inference charges")
    parser.add_argument("--run-dir", type=Path, help="New private artifact directory; must not exist")
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
            "scripts/corvee configure or set CORVEX_API_KEY."
        )
    client = ApiClient(base_url, api_key, timeout=args.http_timeout)

    if args.models:
        try:
            response = client.call("GET", "/models")
        except ProviderFailure as exc:
            fail(f"Provider request failed: {exc}", 1)
        models = response.get("data", []) if isinstance(response, dict) else []
        ids = sorted(
            item.get("id", "") for item in models if isinstance(item, dict) and item.get("id")
        )
        if args.model:
            ids = [model_id for model_id in ids if args.model.lower() in model_id.lower()]
        print("\n".join(ids))
        return 0

    if not model:
        fail("Select a Corvex model with $corvee select or pass --model")
    if args.mission is None:
        fail("--mission is required unless --models is used")
    if args.max_steps is not None and args.max_steps < 1:
        fail("--max-steps must be positive")
    if args.http_timeout < 1:
        fail("--http-timeout must be positive")
    if not 0 <= args.request_retries <= 2:
        fail("--request-retries must be between 0 and 2")

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
    directory = (args.run_dir.expanduser().resolve() if args.run_dir else
                 root / ".codex" / "corvee" / "reports" / uuid.uuid4().hex)
    try:
        journal = RunJournal(directory, api_key)
    except OSError as exc:
        fail(f"cannot create private run directory: {exc}")
    print(f"Run artifacts: {directory}", file=sys.stderr, flush=True)
    journal.checkpoint(messages, "ready")
    journal.event("run_start", model=model, max_steps=max_steps, max_time_seconds=max_time)
    try:
        with execution_deadline(max_time):
            code = run_steps(client, tools, messages, model, args.effort, max_steps, max_time,
                             journal=journal, request_retries=args.request_retries)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        journal.finish("budget_exhausted" if code == 124 else "failed", code)
        raise
    except KeyboardInterrupt:
        journal.finish("interrupted", 130)
        return 130
    except Exception:
        journal.finish("internal_error", 1)
        fail("Runner failed; inspect private run artifacts", 1)
    journal.finish("report_returned" if code == 0 else "incomplete", code)
    return code


def run_steps(client, tools, messages, model, effort, max_steps, max_time, *,
              journal=None, request_retries=1):
    started = time.monotonic()
    deadline = started + max_time
    request_timeout = client.timeout
    reserve = min(request_timeout, max_time * 0.2)
    repeated = {}
    error_streak = 0
    stop_reason = None
    allowed_names = {tool["function"]["name"] for tool in tools.schemas()}

    def event(name, **fields):
        if journal:
            journal.event(name, **fields)

    def checkpoint(phase):
        if journal:
            journal.checkpoint(messages, phase)

    for step in range(1, max_steps + 1):
        if journal:
            journal.step = step
        if time.monotonic() - started >= max_time:
            fail(f"delegate exceeded max time after {step - 1} steps", 124)
        if stop_reason is None and (step == max_steps or deadline - time.monotonic() <= reserve):
            stop_reason = "step_budget" if step == max_steps else "time_reserve"
        if stop_reason:
            messages.append({"role": "user", "content":
                f"Execution stopped ({stop_reason}). Tools are disabled. Return a concise partial "
                "evidence report now, including uncertainties and unverified work. Do not claim completion."})
            event("wrap_up", reason=stop_reason)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools.schemas(),
            "tool_choice": "none" if stop_reason else "auto",
        }
        if effort:
            payload["reasoning_effort"] = effort
        checkpoint("request_pending")
        response = None
        for attempt in range(request_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("delegate exceeded max time", 124)
            request_budget = remaining if stop_reason else remaining - reserve
            if request_budget <= 0:
                stop_reason = "time_reserve"
                break
            client.timeout = min(request_timeout, request_budget)
            event("request_start", attempt=attempt + 1, remaining_seconds=round(remaining, 2))
            request_started = time.monotonic()
            try:
                response = client.call("POST", "/chat/completions", payload)
                event("request_end", duration_seconds=round(time.monotonic() - request_started, 3))
                break
            except ProviderFailure as exc:
                event("request_error", category=str(exc), retryable=exc.retryable)
                if exc.retryable and not stop_reason and deadline - time.monotonic() <= reserve:
                    stop_reason = "time_reserve"
                    break
                if not exc.retryable or attempt == request_retries:
                    fail(f"Provider request failed: {exc}", 75 if exc.retryable else 1)
                delay = 2 ** attempt
                if deadline - time.monotonic() <= delay:
                    fail("delegate exhausted retry time budget", 124)
                event("request_retry", delay_seconds=delay)
                time.sleep(delay)
        if response is None:
            continue
        if time.monotonic() - started >= max_time:
            fail("delegate exceeded max time", 124)
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            fail("provider response has no assistant message", 1)
        if not isinstance(message, dict):
            fail("provider assistant message is not an object", 1)
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)
        checkpoint("response_received")

        if not tool_calls:
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                fail("provider returned neither tool calls nor a final text report", 1)
            if journal:
                protected_write(journal.directory / "report.md", journal.redact(
                    (f"# Incomplete: {stop_reason}\n\n" if stop_reason else "") + content))
            print((f"Incomplete ({stop_reason}):\n" if stop_reason else "") + content)
            return 3 if stop_reason else 0

        if stop_reason:
            event("wrap_up_rejected", reason="provider_requested_disabled_tools")
            return 3

        warn_stall = False
        for tool_call in tool_calls:
            if time.monotonic() - started >= max_time:
                fail("delegate exceeded max time", 124)
            if not isinstance(tool_call, dict):
                fail("malformed provider tool call", 1)
            call_id = tool_call.get("id", "missing-call-id")
            function = tool_call.get("function") or {}
            name = function.get("name", "")
            raw_arguments = function.get("arguments", "{}")
            tool_started = time.monotonic()
            try:
                arguments = (
                    raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
                )
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                checkpoint("tool_pending")
                event("tool_start", tool=name if name in allowed_names else "unknown")
                result = (tools.execute(name, arguments) if not stop_reason else
                          json.dumps({"ok": False, "error": "tools stopped; report required"}))
            except (json.JSONDecodeError, ValueError) as exc:
                result = json.dumps({"ok": False, "error": f"invalid tool call: {exc}"})
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            checkpoint("tool_completed")
            ok = json.loads(result).get("ok", False)
            event("tool_end", tool=name if name in allowed_names else "unknown", ok=ok,
                  duration_seconds=round(time.monotonic() - tool_started, 3))
            # Compare both arguments and results: changed evidence is progress.
            fingerprint = hashlib.sha256(json.dumps([name, raw_arguments, result], sort_keys=True).encode()).hexdigest()
            repeated[fingerprint] = repeated.get(fingerprint, 0) + 1
            error_streak = 0 if ok else error_streak + 1
            if repeated[fingerprint] == 2 or error_streak == 2:
                warn_stall = True
                event("stall_warning")
            if repeated[fingerprint] >= 3 or error_streak >= 3:
                stop_reason = "stalled"
                event("stall_detected")
        if warn_stall:
            messages.append({"role": "user", "content":
                "Repeated tool results or errors detected. Change approach or return your partial report."})

    fail(f"delegate exceeded maximum of {max_steps} model steps", 124)


if __name__ == "__main__":
    raise SystemExit(main())
