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

from corvee_config import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    ConfigError,
    TransportError,
    build_provider_request,
    default_config_path,
    fail,
    load_config,
    load_env_file,
    parse_duration,
    request_json,
    resolve_api_key,
    validate_base_url,
    atomic_write as protected_write,
)


MAX_FILE_BYTES = 200_000
MAX_TOOL_OUTPUT = 30_000

# A deny-list cannot keep pace with credential-bearing variable names, and it
# leaks channels such as SSH_AUTH_SOCK that carry no secret in the name itself.
# grep fallback batching: one process per chunk of files, not per file.
GREP_BATCH_SIZE = 256
GREP_BATCH_TIMEOUT = 60

COMMAND_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ",
    "PYTHONDONTWRITEBYTECODE",
})
# PYTHONPATH is deliberately absent: combined with --write it lets a delegate
# drop a sitecustomize.py and have any allow-listed interpreter import it.

# Flags that turn a benign-looking allow-listed binary into arbitrary
# execution by pointing it at a model-supplied configuration or hook.
COMMAND_ARGUMENT_DENYLIST = {
    "git": ("-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack"),
    "ssh": ("-o", "-F"),
    "rsync": ("-e", "--rsh"),
}

# Repository-internal paths a delegate must never write, even in --write mode.
# A ".git" component anywhere grants code execution through hooks or
# core.sshCommand on the next git operation in THAT repository -- vendored
# checkouts, submodules and worktrees put real git directories well below the
# root, so this cannot be anchored at position 0. Matching is case-insensitive
# because APFS and NTFS resolve ".GIT" to the same directory.
PROTECTED_WRITE_COMPONENTS = frozenset({".git"})
# The run's own evidence tree, anchored at the repository root.
PROTECTED_WRITE_PREFIXES = (
    (".codex", "corvee", "reports"),
)


def _protect_run_state(run_dir: Path) -> None:
    """Keep checkpoints, which contain repository content, out of commits.

    Run state is deliberately co-located with the repository so it travels with
    the workspace and stays inspectable, but checkpoint.json embeds file
    contents and tool output. Drop an ignore file at the .codex root the first
    time a run directory is created so the default is "not committed".
    """
    for parent in run_dir.parents:
        if parent.name == ".codex":
            marker = parent / ".gitignore"
            if not marker.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(
                        "# Written by corvee. Run artifacts embed repository content\n"
                        "# and tool output; they are not meant to be committed.\n"
                        "corvee/reports/\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass  # An unwritable .codex is the user's call, not a run failure.
            return


class RunJournal:
    """Private checkpoints contain repository content; events contain metadata only."""
    def __init__(self, directory: Path, api_key: str, *, resume: bool = False):
        self.directory = directory
        if resume:
            if not directory.exists():
                raise OSError(f"run directory does not exist: {directory}")
            if not directory.is_dir():
                raise OSError(f"run path is not a directory: {directory}")
            self.events = directory / "events.jsonl"
            if not self.events.exists():
                self.events.touch(mode=0o600, exist_ok=True)
        else:
            directory.mkdir(parents=True, mode=0o700, exist_ok=False)
            self.events = directory / "events.jsonl"
            self.events.touch(mode=0o600, exist_ok=False)
        self.api_key = api_key
        self.started = time.monotonic()
        self.step = 0
        self.messages = []
        self.phase = "starting"
        self.run_context: dict[str, Any] | None = None
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
        checkpoint_data = {
            "version": 1,
            "step": self.step,
            "phase": phase,
            "messages": messages,
            "automatic_replay_safe": False,
        }
        if self.run_context is not None:
            checkpoint_data["run_context"] = self.run_context
        protected_write(self.directory / "checkpoint.json", self.redact(json.dumps({
            **checkpoint_data
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


def _load_checkpoint_messages(
    run_dir: Path,
) -> tuple[list[dict[str, Any]], int, str, bool, dict[str, Any]]:
    """Load messages from an existing checkpoint and return (messages, next_step, phase, pending_trimmed, run_context)."""
    checkpoint_path = run_dir / "checkpoint.json"
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except OSError as exc:
        fail(f"resume failed: cannot read checkpoint {checkpoint_path}: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"resume failed: invalid checkpoint format in {checkpoint_path}: {exc}")
    if not isinstance(payload, dict):
        fail(f"resume failed: invalid checkpoint format in {checkpoint_path}")
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        fail(f"resume failed: checkpoint messages must be a list in {checkpoint_path}")
    if not all(isinstance(message, dict) for message in messages):
        fail(f"resume failed: checkpoint contains malformed messages in {checkpoint_path}")
    if not messages:
        fail(f"resume failed: checkpoint has no messages in {checkpoint_path}")
    if messages and messages[0].get("role") != "system":
        fail(f"resume failed: checkpoint missing expected system prompt in {checkpoint_path}")

    raw_context = payload.get("run_context", {})
    if not isinstance(raw_context, dict):
        fail(f"resume failed: invalid checkpoint run_context in {checkpoint_path}")
    run_context: dict[str, Any] = {}
    if "cwd" in raw_context:
        cwd = raw_context["cwd"]
        if not isinstance(cwd, str) or not cwd.strip():
            fail(f"resume failed: invalid checkpoint run_context.cwd in {checkpoint_path}")
        run_context["cwd"] = cwd
    if "write" in raw_context:
        write = raw_context["write"]
        if not isinstance(write, bool):
            fail(f"resume failed: invalid checkpoint run_context.write in {checkpoint_path}")
        run_context["write"] = write
    if "allowed_commands" in raw_context:
        commands = raw_context["allowed_commands"]
        if not isinstance(commands, list) or not all(isinstance(command, str) for command in commands):
            fail(f"resume failed: invalid checkpoint run_context.allowed_commands in {checkpoint_path}")
        run_context["allowed_commands"] = sorted(set(commands))

    phase = str(payload.get("phase", ""))
    step = payload.get("step", 0)
    try:
        step = int(step)
    except (TypeError, ValueError):
        step = 0
    if step < 0:
        step = 0
    if phase in {"ready", "request_pending"}:
        next_step = step
    else:
        next_step = step + 1
    if next_step < 1:
        next_step = 1

    messages_copy = messages.copy()
    pending_trimmed = False
    index = len(messages_copy) - 1
    while index >= 0 and messages_copy[index].get("role") == "tool":
        index -= 1
    if index >= 0:
        assistant = messages_copy[index]
        if assistant.get("role") == "assistant" and isinstance(assistant.get("tool_calls"), list):
            tool_calls = assistant.get("tool_calls") or []
            if tool_calls:
                expected: set[str] = set()
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        fail(f"resume failed: checkpoint has malformed tool call in {checkpoint_path}")
                    call_id = tool_call.get("id")
                    if not isinstance(call_id, str):
                        fail(f"resume failed: checkpoint has tool calls without IDs in {checkpoint_path}")
                    expected.add(call_id)
                responses = {
                    str(message.get("tool_call_id"))
                    for message in messages_copy[index + 1:]
                    if isinstance(message, dict) and message.get("role") == "tool"
                }
                if expected - responses:
                    messages_copy = messages_copy[:index]
                    pending_trimmed = True

    if pending_trimmed and not messages_copy:
        fail(f"resume failed: checkpoint ends with an unterminated tool call in {checkpoint_path}")
    return messages_copy, next_step, phase, pending_trimmed, run_context


def _get_report_dir_mtime(directory: Path) -> float:
    latest = directory.stat().st_mtime
    try:
        for item in directory.rglob("*"):
            try:
                if item.is_symlink():
                    continue
                mtime = item.stat().st_mtime
                if mtime > latest:
                    latest = mtime
            except OSError:
                continue
    except OSError:
        pass
    return latest


MAX_REMOVE_DEPTH = 64
KNOWN_REPORT_ARTIFACTS = {"checkpoint.json", "events.jsonl", "status.json", "report.md"}


def _is_uuid_hex(name: str) -> bool:
    if len(name) != 32:
        return False
    try:
        val = uuid.UUID(hex=name)
        return val.hex == name.lower()
    except ValueError:
        return False


def _is_report_candidate(entry: Path) -> bool:
    if _is_uuid_hex(entry.name):
        return True
    for artifact in KNOWN_REPORT_ARTIFACTS:
        if (entry / artifact).exists():
            return True
    return False


def _force_remove_tree(path: Path, depth: int = 0) -> None:
    if depth > MAX_REMOVE_DEPTH:
        raise OSError(f"report tree exceeds {MAX_REMOVE_DEPTH} levels; refusing to recurse: {path}")
    path = Path(path)
    try:
        st = path.lstat()
    except OSError:
        return
    if path.is_symlink() or not stat.S_ISDIR(st.st_mode):
        path.unlink()
        return

    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass
    entries: list[Path] = []
    try:
        entries = list(path.iterdir())
    except OSError:
        pass
    for child in entries:
        _force_remove_tree(child, depth + 1)
    os.rmdir(path)


def _remove_readonly_onexc(func: Any, path: Any, exc: Any) -> None:
    del func, exc
    _force_remove_tree(Path(path))


def _remove_report_dir(directory: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(directory, onexc=_remove_readonly_onexc)
    else:
        shutil.rmtree(directory, onerror=_remove_readonly_onexc)


def cleanup_reports(
    reports_root: Path,
    *,
    older_than_days: int = 30,
    dry_run: bool = False,
) -> dict[str, int | str | bool]:
    """Remove stale report artifacts and return a result summary."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be positive")
    result = {
        "removed": 0,
        "kept": 0,
        "skipped": 0,
        "failed": 0,
        "mode": "dry-run" if dry_run else "delete",
        "path": str(reports_root),
    }
    if not reports_root.exists():
        print(f"[corvée] No report directory found: {reports_root}")
        return result
    if not reports_root.is_dir():
        print(f"[corvée] Report path is not a directory: {reports_root}")
        return {**result, "failed": result["failed"] + 1}
    now = time.time()
    cutoff = now - older_than_days * 24 * 60 * 60
    print(f"[corvée] Report root: {reports_root}")
    print(f"[corvée] Keeping items newer than {older_than_days} day(s)")
    try:
        entries = sorted(reports_root.iterdir())
    except OSError as exc:
        print(f"[corvée] Failed to read report directory {reports_root}: {exc}")
        return {**result, "failed": result["failed"] + 1}
    for entry in entries:
        if entry.is_symlink():
            print(f"SKIP: {entry.name} (symlink)")
            result["skipped"] += 1
            continue
        if not entry.is_dir():
            print(f"SKIP: {entry.name} (not a directory)")
            result["skipped"] += 1
            continue
        if not _is_report_candidate(entry):
            print(f"SKIP: {entry.name} (not a report candidate)")
            result["skipped"] += 1
            continue
        try:
            mtime = _get_report_dir_mtime(entry)
        except OSError:
            print(f"SKIP: {entry.name} (stat failed)")
            result["skipped"] += 1
            continue
        age_days = (now - mtime) / (24 * 60 * 60)
        if mtime <= cutoff:
            if dry_run:
                print(f"DELETE [dry-run]: {entry.name} ({age_days:.1f}d old)")
                result["removed"] += 1
                continue
            try:
                _remove_report_dir(entry)
                print(f"DELETED: {entry.name} ({age_days:.1f}d old)")
                result["removed"] += 1
            except OSError as exc:
                print(f"FAILED: {entry.name} ({exc})")
                result["failed"] += 1
        else:
            print(f"KEEP: {entry.name} ({age_days:.1f}d old)")
            result["kept"] += 1
    print("[corvée] Summary:", json.dumps(result, ensure_ascii=False))
    return result



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
    pipe = subprocess.PIPE if capture_output else None
    with subprocess.Popen(argv, stdout=pipe, stderr=pipe,
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
        req = build_provider_request(
            self.base_url, endpoint, api_key=self.api_key, method=method, payload=payload
        )
        # No SIGALRM here: run_steps already holds the run-budget itimer, and a
        # nested one would only fight it. The socket timeout still applies.
        return request_json(req, timeout=self.timeout, deadline=False)


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
            raise ValueError(f"path is outside repository root: {value}") from None
        return resolved

    def safe_write_path(self, value: str, *, allow_missing: bool = False) -> Path:
        """Confine writes to the repository and refuse protected internal paths."""
        resolved = self.safe_path(value, allow_missing=allow_missing)
        parts = resolved.relative_to(self.root).parts
        folded = tuple(part.lower() for part in parts)
        for index, part in enumerate(folded):
            if part in PROTECTED_WRITE_COMPONENTS:
                raise ValueError(
                    f"path is write-protected: {'/'.join(parts[: index + 1])} "
                    "is a git directory and may not be modified by a delegate"
                )
        for prefix in PROTECTED_WRITE_PREFIXES:
            if folded[: len(prefix)] == prefix:
                raise ValueError(
                    f"path is write-protected: {'/'.join(prefix)} may not be modified by a delegate"
                )
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
        output: list[str] = []
        size = 0
        batch: list[str] = []

        def flush(paths: list[str]) -> bool:
            """Run one grep over a batch; return False once the output cap is reached."""
            nonlocal size
            if not paths:
                return True
            result = run_process(
                [grep, "-nH", "-I", "-E", "-e", pattern, "--", *paths],
                cwd=self.root, text=True, capture_output=True, timeout=GREP_BATCH_TIMEOUT,
            )
            if result.returncode not in (0, 1):
                raise ValueError(result.stderr.strip() or "grep failed")
            output.append(result.stdout)
            size += len(result.stdout)
            return size <= MAX_TOOL_OUTPUT

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
                # Validate, but hand grep the relative path: absolute paths would
                # echo the user's home directory back to the provider, and ripgrep
                # already reports relative.
                self.safe_path(relative)
                batch.append(os.path.join(".", relative))
                if len(batch) >= GREP_BATCH_SIZE:
                    if not flush(batch):
                        return truncate("".join(output))
                    batch = []
        if not flush(batch):
            return truncate("".join(output))
        return truncate("".join(output))

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
        target = self.safe_write_path(path)
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
        target = self.safe_write_path(path, allow_missing=True)
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
        forbidden = COMMAND_ARGUMENT_DENYLIST.get(Path(executable).name, ())
        for argument in argv[1:]:
            if argument in forbidden or any(
                argument.startswith(f"{flag}=") for flag in forbidden if flag.startswith("--")
            ):
                raise ValueError(
                    f"argument is not permitted for {argv[0]}: {argument}; it can execute "
                    "an arbitrary command through configuration"
                )
        command_cwd = self.safe_path(cwd)
        if not command_cwd.is_dir():
            raise ValueError(f"command cwd is not a directory: {cwd}")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in COMMAND_ENV_ALLOWLIST
        }
        environment["PWD"] = str(command_cwd)
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


def read_version() -> str:
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


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
    parser.add_argument("--resume", type=Path, help="Resume from an existing run directory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--version", action="version", version=f"corvee {read_version()}")
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

    model = (
        args.model
        or model_config_value
        or os.environ.get("CORVEX_MODEL")
        or env_file_values.get("CORVEX_MODEL")
        or config.get("model")
        or ""
    )
    api_key_env = args.api_key_env or config.get("api_key_env") or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env, "")
    key_from_env_file = False
    if not api_key:
        api_key = (
            env_file_values.get(api_key_env, "")
            or env_file_values.get("CORVEX_API_KEY", "")
            or env_file_values.get("API_KEY", "")
        )
        key_from_env_file = bool(api_key)
    if not api_key:
        try:
            api_key = resolve_api_key(config, config_path, env_file_values)
        except ConfigError as exc:
            fail(str(exc))

    # An --env-file may redirect the endpoint only when it also supplies the
    # credential. Otherwise a repo-local .env could aim the user's real key at
    # an attacker's host, which exfiltrates it in the first Authorization header.
    env_file_url = env_file_values.get("CORVEX_API_URL") or env_file_values.get("API_URL")
    if env_file_url and not key_from_env_file:
        fail(
            "--env-file sets an API URL but not the API key; refusing to send an "
            "externally configured credential to a file-supplied endpoint"
        )
    base_url = (
        args.base_url
        or os.environ.get("CORVEX_API_URL")
        or (env_file_url if key_from_env_file else "")
        or config.get("base_url")
        or DEFAULT_BASE_URL
    )

    if not api_key:
        fail(
            "Corvex API credential is not configured. Run "
            "scripts/corvee configure or set CORVEX_API_KEY."
        )
    client = ApiClient(base_url, api_key, timeout=args.http_timeout)

    if not model:
        fail("Select a Corvex model with $corvee select or pass --model")
    if args.mission is None and not args.resume:
        fail("--mission is required unless --resume is used")
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
    resume_context: dict[str, Any] = {}

    resume_dir = args.resume.expanduser().resolve() if args.resume else None
    if args.resume and args.run_dir and args.run_dir.expanduser().resolve() != resume_dir:
        fail("--run-dir must match --resume when resuming")

    run_dir = (resume_dir if resume_dir else (
        args.run_dir.expanduser().resolve() if args.run_dir
        else root / ".codex" / "corvee" / "reports" / uuid.uuid4().hex
    ))
    start_step = 1
    resume_phase = ""
    tool_pending_trimmed = False

    if args.resume:
        messages, start_step, resume_phase, tool_pending_trimmed, resume_context = _load_checkpoint_messages(run_dir)
        if tool_pending_trimmed and start_step > 1:
            start_step -= 1
        if "cwd" in resume_context and Path(resume_context["cwd"]).resolve() != root:
            fail("resume failed: checkpoint was started from a different --cwd; rerun with matching --cwd")
        if "write" in resume_context and resume_context["write"] != args.write:
            fail("resume failed: resume used mismatched --write mode")
        if "allowed_commands" in resume_context:
            if sorted(set(args.allow_command)) != resume_context["allowed_commands"]:
                fail("resume failed: resume used mismatched --allow-command list")
    else:
        mission_path = args.mission.resolve(strict=True)
        if not mission_path.is_file():
            fail(f"mission is not a file: {mission_path}")
        if mission_path.stat().st_size > MAX_FILE_BYTES:
            fail(f"mission exceeds {MAX_FILE_BYTES} bytes")
        mission = mission_path.read_text(encoding="utf-8")
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
        "cwd": str(root),
        "run_context": {
            "cwd": str(root),
            "write": bool(args.write),
            "allowed_commands": sorted(set(args.allow_command)),
            "http_timeout": args.http_timeout,
        },
    }
    if args.resume:
        run_description["resume_from"] = str(run_dir)
    print(json.dumps(run_description, indent=2), file=sys.stderr)
    if args.dry_run:
        return 0

    if args.resume and tool_pending_trimmed:
        messages.append(
            {"role": "user", "content":
             "The prior run ended while a tool call was pending. Do not replay pending tool calls."
             " Verify repository state and continue from here with the existing evidence."}
        )
    directory = run_dir
    _protect_run_state(directory)
    try:
        journal = RunJournal(directory, api_key, resume=bool(args.resume))
    except OSError as exc:
        if args.resume:
            fail(f"cannot open private run directory: {exc}")
        fail(f"cannot create private run directory: {exc}")
    print(f"Run artifacts: {directory}", file=sys.stderr, flush=True)
    journal.run_context = run_description["run_context"]
    if args.resume and resume_context:
        journal.run_context["cwd"] = resume_context.get("cwd", journal.run_context["cwd"])
        if "write" in resume_context:
            journal.run_context["write"] = resume_context["write"]
        if "allowed_commands" in resume_context:
            journal.run_context["allowed_commands"] = resume_context["allowed_commands"]
    if not args.resume:
        journal.checkpoint(messages, "ready")
    else:
        journal.step = max(1, start_step - 1)
        journal.messages = messages
        journal.phase = resume_phase or "resumed"
        journal.event("run_resume", resume_from=str(directory), from_phase=journal.phase, start_step=start_step)
    journal.event("run_start", model=model, max_steps=max_steps, max_time_seconds=max_time,
                 start_step=start_step, resumed=bool(args.resume))
    try:
        with execution_deadline(max_time):
            code = run_steps(client, tools, messages, model, args.effort, max_steps, max_time,
                             journal=journal, request_retries=args.request_retries, start_step=start_step)
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
              journal=None, request_retries=1, start_step=1):
    started = time.monotonic()
    deadline = started + max_time
    request_timeout = client.timeout
    reserve = min(request_timeout, max_time * 0.2)
    repeated = {}
    error_streak = 0
    stop_reason = None
    wrap_up_announced = False
    allowed_names = {tool["function"]["name"] for tool in tools.schemas()}
    start_step = max(1, int(start_step))
    end_step = start_step + max_steps - 1

    def event(name, **fields):
        if journal:
            journal.event(name, **fields)

    def checkpoint(phase):
        if journal:
            journal.checkpoint(messages, phase)

    for step in range(start_step, end_step + 1):
        if journal:
            journal.step = step
        if time.monotonic() - started >= max_time:
            fail(f"delegate exceeded max time after {step - 1} steps", 124)
        if stop_reason is None and (step == end_step or deadline - time.monotonic() <= reserve):
            stop_reason = "step_budget" if step == end_step else "time_reserve"
        if stop_reason and not wrap_up_announced:
            messages.append({"role": "user", "content":
                f"Execution stopped ({stop_reason}). Tools are disabled. Return a concise partial "
                "evidence report now, including uncertainties and unverified work. Do not claim completion."})
            event("wrap_up", reason=stop_reason)
            wrap_up_announced = True
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if not stop_reason:
            payload["tools"] = tools.schemas()
            payload["tool_choice"] = "auto"
        # During wrap-up the schemas are omitted rather than sent with
        # tool_choice "none": some OpenAI-compatible servers still emit tool
        # calls when the definitions are present, which costs the final report.
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
            except TransportError as exc:
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
            # The reserve exists to buy a partial report. Only give up once the
            # wrap-up prompt has actually been sent and still produced nothing;
            # otherwise fall through so the next step announces it.
            if stop_reason and wrap_up_announced:
                fail("delegate exceeded max time while waiting for final wrap-up", 124)
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
        if not tool_calls:
            # Tool steps checkpoint again as "tool_pending" before executing anything,
            # so a snapshot here would rewrite the whole history for nothing.
            checkpoint("response_received")

        if not tool_calls:
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                fail("provider returned neither tool calls nor a final text report", 1)
            if journal:
                protected_write(journal.directory / "report.md", journal.redact(
                    (f"# Incomplete: {stop_reason}\n\n" if stop_reason else "") + content))
            printable = (f"Incomplete ({stop_reason}):\n" if stop_reason else "") + content
            print(journal.redact(printable) if journal else printable)
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
