#!/usr/bin/env python3
"""Print the configured Corvex bearer token for Codex provider authentication."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from corvee_config import ConfigError, default_config_path, load_config, resolve_api_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    try:
        config = load_config(args.config, required=True)
        api_key = resolve_api_key(config, args.config)
        if not api_key:
            raise ConfigError("no Corvex API credential is configured")
        print(api_key)
        return 0
    except (ConfigError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
