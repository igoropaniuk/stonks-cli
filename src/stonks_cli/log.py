"""Logging configuration for stonks-cli."""

import logging
import logging.handlers
from pathlib import Path

from platformdirs import user_log_dir

# ~/.local/state/stonks/log/stonks.log  (Linux / macOS XDG)
# ~/Library/Logs/stonks/stonks.log      (macOS native)
# %LOCALAPPDATA%\stonks\Logs\stonks.log (Windows)
LOG_DIR = Path(user_log_dir("stonks"))
LOG_FILE = LOG_DIR / "stonks.log"


def setup_logging(level: int = logging.WARNING) -> None:
    """Configure the ``stonks_cli`` logger hierarchy.

    Two handlers are attached to the ``stonks_cli`` root logger:

    * **RotatingFileHandler** -- *level* and above, written to :data:`LOG_FILE`
      (max 1 MB, 3 backups).

    The function is idempotent: calling it more than once is safe.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("stonks_cli")
    if logger.handlers:
        return  # already configured

    logger.setLevel(level)
    logger.propagate = False

    # --- file handler ---
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(fh)
