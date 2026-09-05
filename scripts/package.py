#!/usr/bin/env python3
"""Build a release from an explicit list of publishable files."""

import hashlib
from pathlib import Path
import tarfile


PACKAGE_FILES = (
    "SKILL.md", "README.md", "CHANGELOG.md", "LICENSE", "VERSION",
    "references/control-protocol.md", "references/mission-format.md",
    "references/provider-setup.md",
    "scripts/corvee", "scripts/corvee.py",
    "scripts/corvee_config.py", "scripts/configure_corvee.py",
    "scripts/install.py", "scripts/package.py",
)
TEST_FILES = ("tests/test_corvee.py",)
# Development-only resources, listed explicitly so a drift check can tell a
# deliberate exclusion from a newly added file someone forgot to package.
EXCLUDED_FILES = (
    "pyproject.toml",
    ".gitignore",
    ".github/workflows/ci.yml",
)


def checked_files(root: Path, include_tests: bool = False):
    for name in PACKAGE_FILES + (TEST_FILES if include_tests else ()):
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or unsafe package resource: {name}")
        yield name, path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text().strip()
    if not version or any(c not in "0123456789." for c in version):
        raise ValueError("Invalid release version")
    resources = list(checked_files(root, include_tests=True))
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / f"corvee-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, path in resources:
            bundle.add(path, arcname=f"corvee/{name}", recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n")
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
