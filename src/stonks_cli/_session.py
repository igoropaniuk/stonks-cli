"""Market session constants shared across the codebase.

Using a StrEnum means values are interchangeable with plain strings in
comparisons and dict keys, while giving a single authoritative definition
and IDE-visible names.
"""

from enum import Enum


class Session(str, Enum):
    PRE = "pre"
    REGULAR = "regular"
    POST = "post"
    CLOSED = "closed"
