#!/usr/bin/env python3
"""Configure, validate, and select models for corvee."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from corvee_config import (
    ConfigError,
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    default_config_path,
    fail,
    fetch_models,
    get_codex_home,
    load_config,
    load_env_file,
    parse_duration,
    resolve_api_key,
    update_selected_model,
    validate_base_url,
    write_configuration,
    verify_credential,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    codex_home = get_codex_home()
    result.add_argument("--config", type=Path, default=default_config_path(codex_home))
    result.add_argument("--timeout", type=parse_duration, default=600,
                        help="Request timeout; seconds or a 30s/30m/2h suffix (default: 600)")
    commands = result.add_subparsers(dest="command", required=True)

    configure = commands.add_parser("configure", help="validate and save provider settings")
    configure.add_argument("--url")
    configure.add_argument("--model")
    configure.add_argument("--api-key-env")
    configure.add_argument("--from-env-file", type=Path)
    configure.add_argument("--non-interactive", action="store_true")

    commands.add_parser("check", help="validate credentials with a tiny billable inference request")
    listing = commands.add_parser("models", help="list live model IDs")
    listing.add_argument("pattern", nargs="?")
    selection = commands.add_parser("select", help="save an exact live model ID")
    selection.add_argument("model", nargs="?")
    return result


def configure(args: argparse.Namespace) -> int:
    env_values = load_env_file(args.from_env_file.resolve(strict=True)) if args.from_env_file else {}
    current = load_config(args.config)
    env_file_url = env_values.get("CORVEX_API_URL") or env_values.get("API_URL")
    env_file_key = env_values.get("CORVEX_API_KEY", "") or env_values.get("API_KEY", "")
    if env_file_url and not env_file_key and not args.url:
        raise ConfigError(
            "--from-env-file sets an API URL but not the API key; supply --url explicitly "
            "rather than pointing an externally configured credential at a file-supplied endpoint"
        )
    default_url = (
        args.url
        or env_file_url
        or current.get("base_url")
        or DEFAULT_BASE_URL
    )

    if args.non_interactive or args.url:
        base_url = validate_base_url(default_url)
    else:
        answer = input(f"Use the default Corvex API URL {DEFAULT_BASE_URL}? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            base_url = DEFAULT_BASE_URL
        elif answer in ("n", "no"):
            base_url = validate_base_url(input("Corvex API URL: ").strip())
        else:
            raise ConfigError("answer must be yes or no")

    api_key_env = args.api_key_env or current.get("api_key_env") or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env, "")
    if args.api_key_env:
        if not api_key:
            raise ConfigError(f"API key environment variable is not set: {args.api_key_env}")
    if not api_key:
        api_key = env_values.get("CORVEX_API_KEY", "") or env_values.get("API_KEY", "")
    if not api_key and current:
        api_key = resolve_api_key(current, args.config)
    if not api_key and not args.non_interactive:
        api_key = getpass.getpass("Corvex API key (input hidden): ").strip()
    if not api_key:
        raise ConfigError(
            f"no API key supplied; set {DEFAULT_API_KEY_ENV}, use --api-key-env, "
            "or provide --from-env-file"
        )

    models = fetch_models(base_url, api_key, args.timeout)
    selected = (
        args.model
        or env_values.get("CORVEX_MODEL")
        or current.get("model")
        or None
    )
    if not args.non_interactive and not selected:
        print("Available models (catalog discovery does not validate the key):")
        print("\n".join(f"  {model}" for model in models))
        selected = input("Default model ID (blank to select later): ").strip() or None
    if selected and selected not in models:
        raise ConfigError(f"model is not in the live catalog: {selected}")

    print("Validating the key with a one-token inference request (provider charges may apply).")
    verify_credential(base_url, api_key, selected or models[0], args.timeout)
    write_configuration(args.config, base_url=base_url, api_key=api_key, model=selected,
                        api_key_env=api_key_env,
                        credentials_file=current.get("credentials_file") or "credentials.toml",
                        default_complexity=current.get("default_complexity") or "medium")
    print(f"Corvex connection verified; configuration saved to {args.config}")
    print(f"Default model: {selected or 'not selected'}")
    return 0


def checked_settings(args: argparse.Namespace) -> tuple[dict[str, object], str, list[str]]:
    config = load_config(args.config, required=True)
    api_key = resolve_api_key(config, args.config)
    if not api_key:
        raise ConfigError("no Corvex API credential is configured")
    base_url = config.get("base_url") or DEFAULT_BASE_URL
    models = fetch_models(str(base_url), api_key, args.timeout)
    return config, str(base_url), models


def main() -> int:
    args = parser().parse_args()
    try:
        args.config = args.config.expanduser().resolve()
        if args.command == "configure":
            return configure(args)
        if args.command == "select" and args.model is None:
            config = load_config(args.config, required=True)
            print(f"Configuration: {args.config}")
            print(f"API URL: {config.get('base_url') or DEFAULT_BASE_URL}")
            print(f"Model: {config.get('model') or 'not selected'}")
            print(f"Credential source: {config.get('api_key_env') or DEFAULT_API_KEY_ENV}, then protected file")
            return 0
        if args.command == "select" and args.model == "auto":
            update_selected_model(args.config, None)
            print("Cleared the default Corvex model")
            return 0
        config, base_url, models = checked_settings(args)
        if args.command == "check":
            selected = config.get("model")
            if selected and selected not in models:
                raise ConfigError(f"saved model is not in the live catalog: {selected}")
            verify_credential(base_url, resolve_api_key(config, args.config), str(selected or models[0]), args.timeout)
            print(f"Corvex connection verified at {base_url}; {len(models)} model(s) available")
            print(f"Default model: {selected or 'not selected'}")
            return 0
        if args.command == "models":
            pattern = (args.pattern or "").lower()
            print("\n".join(model for model in models if pattern in model.lower()))
            return 0
        if args.command == "select":
            if args.model not in models:
                raise ConfigError(f"model is not in the live catalog: {args.model}")
            update_selected_model(args.config, args.model)
            print(f"Selected Corvex model: {args.model}")
            return 0
        raise ConfigError(f"unknown command: {args.command}")
    except (ConfigError, OSError) as exc:
        fail(str(exc), 1)


if __name__ == "__main__":
    raise SystemExit(main())
