#!/usr/bin/env python3
"""Configuration and credential helpers for corvee."""

from __future__ import annotations

import argparse
from http import client
import json
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import signal
import sys
import time
import tempfile
import tomllib
from typing import Any
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://api.tokenfactory.corvex.cloud/v1"
DEFAULT_API_KEY_ENV = "CORVEX_API_KEY"
CONFIG_FIELDS = {
    "version",
    "base_url",
    "model",
    "api_key_env",
    "credentials_file",
    "default_complexity",
}


class ConfigError(RuntimeError):
    pass


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


# signal.setitimer raises OverflowError well below this, and no legitimate run
# budget approaches it; reject early with a readable message instead.
MAX_DURATION_SECONDS = 30 * 24 * 60 * 60


def parse_duration(value: str) -> int:
    """Accept a plain second count or a 30s/30m/2h suffixed duration."""
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
    if not number.isascii() or not number.isdigit() or int(number) < 1:
        raise argparse.ArgumentTypeError("expected a positive duration such as 30m")
    seconds = int(number) * multiplier
    if seconds > MAX_DURATION_SECONDS:
        raise argparse.ArgumentTypeError(
            f"duration exceeds the {MAX_DURATION_SECONDS}-second maximum"
        )
    return seconds


@contextmanager
def request_deadline(seconds: float):
    """CLI wall-clock bound including streaming reads, not just socket inactivity."""
    def expired(signum, frame):
        raise TimeoutError("provider request deadline exceeded")
    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic() - started)), timer[1])


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward provider credentials to a redirect destination.
        return None


def open_request(req: request.Request, timeout: int):
    return request.build_opener(NoRedirect()).open(req, timeout=timeout)


# Statuses worth a second attempt; everything else is a client-side problem
# that a retry would only repeat, at the cost of a duplicate inference charge.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class TransportError(Exception):
    """One classification of a failed provider request, shared by all callers.

    Carries a stable category string and a retry verdict. It never carries a
    response body: an error page from a misconfigured endpoint can echo the
    Authorization header back, so bodies are closed unread.
    """

    def __init__(self, category: str, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.status = status


def build_provider_request(
    base_url: str,
    endpoint: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> request.Request:
    """Build an authenticated provider request against a validated base URL."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "codex-corvee/1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    return request.Request(
        f"{validate_base_url(base_url)}{endpoint}", data=body, headers=headers, method=method
    )


def request_json(req: request.Request, *, timeout: int, deadline: bool = True) -> Any:
    """Perform a request and decode a JSON body, translating every failure.

    The body is read inside the guarded block: a response that dies mid-read is
    a transport failure, not a protocol one. `deadline` adds a SIGALRM
    wall-clock bound for callers not already inside one; the runner is, and
    nesting two itimers around one request buys nothing.
    """
    try:
        if deadline:
            with request_deadline(timeout), open_request(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        with open_request(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        exc.close()
        raise TransportError(f"http_{exc.code}", exc.code in RETRYABLE_STATUS, exc.code) from None
    except TimeoutError:
        raise TransportError("request_timeout", True) from None
    except error.URLError as exc:
        retryable_category = (
            "request_timeout" if isinstance(exc.reason, TimeoutError) else "connection_error"
        )
        raise TransportError(retryable_category, True) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TransportError("invalid_json") from None
    except client.HTTPException:
        # A body that dies mid-read (IncompleteRead, BadStatusLine) is a
        # transport failure, but http.client.HTTPException descends from
        # Exception, not OSError, so it slips past the clause below and would
        # otherwise crash the run instead of being retried.
        raise TransportError("connection_error", True) from None
    except (ConnectionError, OSError):
        raise TransportError("connection_error", True) from None


def describe_transport_error(subject: str, exc: TransportError) -> str:
    """Render a TransportError for a configuration-time audience."""
    if exc.status is not None:
        return f"{subject} returned HTTP {exc.status}"
    if exc.category == "invalid_json":
        return f"{subject} returned invalid JSON"
    if exc.category == "request_timeout":
        return f"{subject} request timed out"
    return f"{subject} request failed; check connectivity and provider settings"


def get_codex_home(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".codex"


def default_config_path(codex_home: Path | None = None) -> Path:
    return get_codex_home(codex_home) / "corvee" / "config.toml"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"invalid environment entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise ConfigError(f"invalid environment name at {path}:{line_number}")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ConfigError(f"invalid quoted environment value at {path}:{line_number}")
            value = value[1:end]
        else:
            for index, char in enumerate(value):
                if char == "#" and (index == 0 or value[index - 1].isspace()):
                    value = value[:index].rstrip()
                    break
        values[name] = value
    return values


def load_config(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(f"configuration does not exist: {path}")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc
    unknown = set(data) - CONFIG_FIELDS
    if unknown:
        raise ConfigError(f"unknown configuration field(s): {', '.join(sorted(unknown))}")
    if data.get("version", 1) != 1:
        raise ConfigError(f"unsupported configuration version: {data.get('version')}")
    for name in ("base_url", "api_key_env", "credentials_file", "default_complexity"):
        if name in data and not isinstance(data[name], str):
            raise ConfigError(f"configuration field {name!r} must be a string")
    if "model" in data and data["model"] is not None and not isinstance(data["model"], str):
        raise ConfigError("configuration field 'model' must be a string or null")
    return data


def validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = parse.urlparse(normalized)
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ConfigError("API URL must be an absolute URL without credentials, query, or fragment")
    is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise ConfigError("API URL must use HTTPS; HTTP is allowed only for loopback testing")
    return normalized


def resolve_api_key(
    config: dict[str, Any], config_path: Path, env_values: dict[str, str] | None = None
) -> str:
    env_values = env_values or {}
    env_name = config.get("api_key_env") or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(env_name, "") or env_values.get(env_name, "")
    if not api_key:
        api_key = env_values.get("API_KEY", "")
    if api_key:
        return api_key
    credential_name = config.get("credentials_file") or "credentials.toml"
    credential_path = Path(credential_name)
    if not credential_path.is_absolute():
        credential_path = config_path.parent / credential_path
    if not credential_path.exists():
        return ""
    if stat.S_IMODE(credential_path.stat().st_mode) & 0o077:
        raise ConfigError(f"credential file must not be group/world accessible: {credential_path}")
    try:
        credentials = tomllib.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid credential file {credential_path}: {exc}") from exc
    if set(credentials) != {"api_key"} or not isinstance(credentials.get("api_key"), str):
        raise ConfigError("credential file must contain only a string 'api_key' field")
    return credentials["api_key"]


def fetch_models(base_url: str, api_key: str, timeout: int = 600) -> list[str]:
    req = build_provider_request(base_url, "/models", api_key=api_key)
    try:
        payload = request_json(req, timeout=timeout)
    except TransportError as exc:
        raise ConfigError(describe_transport_error("Corvex API", exc)) from None
    models = payload.get("data", []) if isinstance(payload, dict) else []
    ids = sorted(
        item.get("id", "") for item in models if isinstance(item, dict) and item.get("id")
    )
    if not ids:
        raise ConfigError("Corvex API returned no model IDs")
    return ids


def verify_credential(base_url: str, api_key: str, model: str, timeout: int = 600) -> None:
    """Model discovery is public; authenticate with a tiny inference request."""
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
               "max_tokens": 1, "stream": False}
    req = build_provider_request(
        base_url, "/chat/completions", api_key=api_key, method="POST", payload=payload
    )
    try:
        result = request_json(req, timeout=timeout)
    except TransportError as exc:
        raise ConfigError(
            f"Credential/inference check failed: {describe_transport_error('the provider', exc)}"
        ) from None
    if not isinstance(result, dict) or not result.get("choices") or result.get("error"):
        raise ConfigError("Credential/inference check returned no completion choices")



def atomic_write(path: Path, content: str, mode: int | None = 0o600) -> None:
    """Replace a file's contents atomically.

    `mode` of None preserves the existing file's permissions, which is what
    repository edits need: a delegate rewriting a script must not silently
    strip its execute bit. A new file under None gets the process umask.
    """
    if mode is None:
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            # NamedTemporaryFile always creates at 0600, so a brand-new file
            # would land far more restrictive than the umask this promises.
            current = os.umask(0)
            os.umask(current)
            mode = 0o666 & ~current
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_configuration(
    config_path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str | None,
    default_complexity: str = "medium",
    api_key_env: str = DEFAULT_API_KEY_ENV,
    credentials_file: str = "credentials.toml",
) -> None:
    credential_path = Path(credentials_file).expanduser()
    if not credential_path.is_absolute():
        credential_path = config_path.parent / credential_path
    if config_path.resolve() == credential_path.resolve():
        raise ConfigError("configuration path must differ from credentials.toml")
    config_content = "\n".join(
        [
            "version = 1",
            f"base_url = {toml_string(validate_base_url(base_url))}",
            f"model = {toml_string(model or '')}",
            f"api_key_env = {toml_string(api_key_env)}",
            f"credentials_file = {toml_string(str(credential_path) if Path(credentials_file).expanduser().is_absolute() else credentials_file)}",
            f"default_complexity = {toml_string(default_complexity)}",
            "",
        ]
    )
    credentials_content = f"api_key = {toml_string(api_key)}\n"
    atomic_write(credential_path, credentials_content, 0o600)
    atomic_write(config_path, config_content, 0o600)


def update_selected_model(config_path: Path, model: str | None) -> None:
    config = load_config(config_path, required=True)
    config["model"] = model or ""
    content = "\n".join(
        f"{key} = {toml_string(value) if isinstance(value, str) else json.dumps(value)}"
        for key, value in config.items()
    ) + "\n"
    atomic_write(config_path, content, 0o600)
