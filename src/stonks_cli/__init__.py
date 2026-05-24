"""stonks-cli package metadata.

``__version__`` is the static version recorded in ``pyproject.toml`` for
released wheels.  When the package is imported from a source checkout
(the ``.git`` directory sits next to the ``src/`` tree), a PEP 440
local-version suffix is appended so that ``stonks --version`` makes it
obvious the running build isn't a tagged release:

* exact tag, clean tree            -> ``0.6.3``
* exact tag, dirty tree            -> ``0.6.3+dirty``
* past tag, clean tree             -> ``0.6.3+dev.<N>.g<sha>``
* past tag, dirty tree             -> ``0.6.3+dev.<N>.g<sha>.dirty``
* no reachable tag, clean / dirty  -> ``0.6.3+dev.<sha>[.dirty]``

PyPI / wheel installs never carry the suffix because no ``.git``
directory is co-located with the installed package.
"""

import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _git_dev_suffix(base: str) -> str:
    """Return a PEP 440 local-version suffix for source-checkout installs.

    Returns an empty string for wheel/PyPI installs (no ``.git`` next to the
    package), when ``git`` isn't on PATH, when the probe times out, or when
    HEAD is exactly on the release tag with a clean tree.
    """
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "describe",
                "--tags",
                "--always",
                "--dirty=.dirty",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    desc = result.stdout.strip()
    if not desc:
        return ""

    tag_prefix = f"v{base}"
    # Exactly on the release tag with a clean tree -- no suffix needed.
    if desc == tag_prefix:
        return ""
    # On the release tag with uncommitted changes.
    if desc == f"{tag_prefix}.dirty":
        return "+dirty"
    # Past the release tag: "v0.6.3-2-gabc1234[.dirty]" -> "+dev.2.gabc1234[.dirty]"
    if desc.startswith(f"{tag_prefix}-"):
        rest = desc[len(tag_prefix) + 1 :]
        return f"+dev.{rest.replace('-', '.')}"
    # No reachable matching tag (shallow clone, different tag, etc.) --
    # fall back to whatever git describe returned, normalized for PEP 440.
    return f"+dev.{desc.replace('-', '.')}"


def _resolve_version() -> str:
    try:
        base = version("stonks-cli")
    except PackageNotFoundError:
        return "0.0.0.dev"
    return base + _git_dev_suffix(base)


__version__ = _resolve_version()
