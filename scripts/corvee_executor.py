"""Codex sandbox command execution without creating a model thread or turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


class ExecutorError(RuntimeError):
    """Execution failed; never fall back to an unsandboxed command."""


def execute(codex_bin: str, command: list[str], cwd: Path, timeout: float, *,
            read_only: bool = False) -> subprocess.CompletedProcess:
    if not command or timeout <= 0:
        raise ExecutorError("invalid command or timeout")
    deadline = time.monotonic() + timeout + 10
    process = subprocess.Popen(
        [codex_bin, "app-server", "--stdio"], cwd=cwd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pending = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def receive(request_id):
        while True:
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    raise ExecutorError("invalid app-server response") from None
                if not isinstance(message, dict):
                    raise ExecutorError("invalid app-server message")
                if message.get("id") == request_id:
                    if "error" in message:
                        raise ExecutorError("Codex rejected sandbox command request")
                    return message.get("result")
                # This integration never authorizes server-requested actions.
                if "id" in message and "method" in message:
                    raise ExecutorError("unexpected app-server request")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ExecutorError("Codex executor timed out")
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                raise ExecutorError("Codex executor disconnected")
            pending.extend(data)
            if len(pending) > 1024 * 1024:
                raise ExecutorError("Codex response exceeded capture limit")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "corvee_executor", "version": "0.1.0"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "command/exec", "params": {
            "command": command, "cwd": str(cwd), "timeoutMs": int(timeout * 1000),
            "outputBytesCap": 32768,
            "sandboxPolicy": ({"type": "readOnly"} if read_only else
                              {"type": "workspaceWrite", "networkAccess": False}),
        }})
        result = receive(2)
        if (not isinstance(result, dict) or type(result.get("exitCode")) is not int
                or not all(isinstance(result.get(key), str) for key in ("stdout", "stderr"))):
            raise ExecutorError("invalid command result")
        return subprocess.CompletedProcess(command, result["exitCode"], result["stdout"], result["stderr"])
    finally:
        selector.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdin.close()
        process.stdout.close()
