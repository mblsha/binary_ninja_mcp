"""Plugin-side UI automation entry points for CLI wrappers."""

from .open_file import open_file_workflow
from .quit_app import (
    choose_decision_label,
    compute_database_save_target,
    quit_workflow,
    resolve_policy,
)
from .statusbar import read_statusbar
from .text import find_item_index, normalize_label, normalize_token

__all__ = [
    "choose_decision_label",
    "compute_database_save_target",
    "find_item_index",
    "normalize_label",
    "normalize_token",
    "open_file_workflow",
    "quit_workflow",
    "read_statusbar",
    "resolve_policy",
]
