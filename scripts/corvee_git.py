"""Fixed Git inspection operations, invoked only inside the Codex executor."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def git(*args: str) -> subprocess.CompletedProcess:
    # Do not inherit GIT_DIR, config injection, external diff or trace settings.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    return subprocess.run(
        ['git', '--no-pager', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-c', 'core.hooksPath=' + os.devnull, *args],
        env=env, capture_output=True, timeout=25,
    )


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    operation, *paths = sys.argv[1:]
    if operation == 'status' and not paths:
        result = git('status', '--short', '--branch', '--ignore-submodules=all')
    elif operation == 'diff' and len(paths) <= 1:
        if paths:
            root = Path.cwd().resolve()
            target = (root / paths[0]).resolve()
            if not target.is_relative_to(root):
                return 2
            paths = [str(target.relative_to(root))]
        result = git('--literal-pathspecs', 'diff', '--no-ext-diff', '--no-textconv',
                     '--ignore-submodules=all', '--', *paths)
    elif operation == 'measure' and not paths:
        result = git('diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', 'HEAD', '--')
        if result.returncode:
            result = git('diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', '--')
        if result.returncode:
            return 1
        total = len(result.stdout)
        listed = git('ls-files', '-z', '--others', '--exclude-standard')
        if listed.returncode:
            return 1
        root = Path.cwd().resolve()
        for raw in listed.stdout.split(b'\0'):
            if not raw:
                continue
            path = root / os.fsdecode(raw)
            # Do not follow untracked symlinks into unrelated files.
            if not path.resolve().is_relative_to(root):
                return 1
            total += path.lstat().st_size
        print(total)
        return 0
    else:
        return 2
    # Fail closed instead of silently giving the caller a truncated diff/status.
    if len(result.stdout) > 30000 or len(result.stderr) > 30000:
        print('Git inspection exceeded output limit; narrow the diff path.', file=sys.stderr)
        return 1
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError):
        print('Git inspection failed', file=sys.stderr)
        raise SystemExit(1) from None
