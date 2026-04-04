"""Cross-module Textual Message subclasses.

Keeping shared messages here avoids circular imports between screens that
post messages and the app that handles them.
"""

from dataclasses import dataclass

from textual.message import Message


@dataclass
class HistoryUpdated(Message):
    """Posted by ChatScreen after each reply so the app can persist history."""

    history: list[dict[str, str]]
