#!/usr/bin/env python3
"""Validate Corvex credentials, then install the corvee skill."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from corvee_config import default_config_path, get_codex_home
from package import checked_files


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--codex-home", type=Path)
    result.add_argument("--url")
    result.add_argument("--model")
    result.add_argument("--api-key-env")
    result.add_argument("--from-env-file", type=Path)
    result.add_argument("--non-interactive", action="store_true")
    result.add_argument("--force", action="store_true")
    result.add_argument("--source", type=Path, default=Path(__file__).parents[1])
    return result


def remove_backup(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def main() -> int:
    args = parser().parse_args()
    codex_home = get_codex_home(args.codex_home)
    try:
        source = args.source.expanduser().resolve(strict=True)
        resources = list(checked_files(source))
        env_file = args.from_env_file.expanduser().resolve(strict=True) if args.from_env_file else None
    except (OSError, ValueError) as exc:
        print(f"invalid installation input: {exc}", file=sys.stderr)
        return 2
    if not (source / "SKILL.md").is_file():
        print(f"source is not a skill directory: {source}", file=sys.stderr)
        return 2
    target = codex_home / "skills" / "corvee"
    if (target.exists() or target.is_symlink()) and not args.force:
        print(f"install target already exists; use --force to replace it: {target}", file=sys.stderr)
        return 2

    configure_script = source / "scripts" / "configure_corvee.py"
    config_path = default_config_path(codex_home)
    command = [
        sys.executable,
        str(configure_script),
        "--config",
        str(config_path),
        "configure",
    ]
    if args.url:
        command.extend(["--url", args.url])
    if args.model:
        command.extend(["--model", args.model])
    if args.api_key_env:
        command.extend(["--api-key-env", args.api_key_env])
    if env_file:
        command.extend(["--from-env-file", str(env_file)])
    if args.non_interactive:
        command.append("--non-interactive")

    # Configuration validates authenticated inference before saving credentials.
    configured = subprocess.run(command, check=False)
    if configured.returncode != 0:
        print("Corvex validation failed; skill installation was not finalized.", file=sys.stderr)
        return configured.returncode

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".corvee-stage-", dir=target.parent))
    backup = target.parent / f".corvee-backup-{os.getpid()}"
    try:
        for name, path in resources:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if target.exists() or target.is_symlink():
            os.replace(target, backup)
        os.replace(stage, target)
    except Exception as exc:
        if not (target.exists() or target.is_symlink()) and (backup.exists() or backup.is_symlink()):
            os.replace(backup, target)
        remove_backup(stage)
        print(f"skill installation failed after configuration: {exc}", file=sys.stderr)
        return 1

    try:
        remove_backup(backup)
    except OSError as exc:
        print(f"Installed successfully, but backup cleanup failed: {backup}: {exc}", file=sys.stderr)

    print(f"Installed corvee at {target}")
    print(f"Configuration: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
