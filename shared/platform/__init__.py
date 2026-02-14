"""Shared platform adapter helpers for Binary Ninja runtime management."""

from .adapter import (
    BinaryNinjaPlatformAdapter,
    find_binary_ninja_pids,
    get_platform_adapter,
    prepare_log_file,
    signal_pid,
    terminate_pid_tree,
)

__all__ = [
    "BinaryNinjaPlatformAdapter",
    "find_binary_ninja_pids",
    "get_platform_adapter",
    "prepare_log_file",
    "signal_pid",
    "terminate_pid_tree",
]
