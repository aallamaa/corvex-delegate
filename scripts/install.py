#!/usr/bin/env python3
"""Validate Corvex credentials, then install the corvex-delegate skill."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from corvex_delegate_config import default_config_path, get_codex_home
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
    source = args.source.expanduser().resolve(strict=True)
    if not (source / "SKILL.md").is_file():
        print(f"source is not a skill directory: {source}", file=sys.stderr)
        return 2
    resources = list(checked_files(source))
    target = codex_home / "skills" / "corvex-delegate"
    if (target.exists() or target.is_symlink()) and not args.force:
        print(f"install target already exists; use --force to replace it: {target}", file=sys.stderr)
        return 2

    configure_script = source / "scripts" / "configure_delegate.py"
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
    if args.from_env_file:
        command.extend(["--from-env-file", str(args.from_env_file.expanduser().resolve(strict=True))])
    if args.non_interactive:
        command.append("--non-interactive")

    # Configuration validates authenticated inference before saving credentials.
    configured = subprocess.run(command, check=False)
    if configured.returncode != 0:
        print("Corvex validation failed; skill installation was not finalized.", file=sys.stderr)
        return configured.returncode

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".corvex-delegate-stage-", dir=target.parent))
    backup = target.parent / f".corvex-delegate-backup-{os.getpid()}"
    try:
        for name, path in resources:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if target.exists() or target.is_symlink():
            os.replace(target, backup)
        os.replace(stage, target)
        remove_backup(backup)
    except Exception as exc:
        if not (target.exists() or target.is_symlink()) and backup.exists():
            os.replace(backup, target)
        remove_backup(stage)
        print(f"skill installation failed after configuration: {exc}", file=sys.stderr)
        return 1

    print(f"Installed corvex-delegate at {target}")
    print(f"Configuration: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
