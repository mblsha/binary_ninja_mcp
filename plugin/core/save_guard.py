"""Safety guard for dangerous BinaryView save operations."""

from __future__ import annotations

from functools import wraps
from typing import Any

_ORIGINAL_SAVE_ATTR = "_binja_mcp_original_save"
_PATCHED_ATTR = "_binja_mcp_save_guard_installed"

_ERROR_MESSAGE = (
    "BinaryView.save(...) is blocked by the Binary Ninja MCP plugin. "
    "This API writes raw original binary bytes and can corrupt BNDB files when "
    "used as a database save operation. Use bv.save_auto_snapshot() for an "
    "existing BNDB, or bv.create_database(path) to create a new BNDB."
)


def install_binaryview_save_guard(bn_module: Any | None = None) -> bool:
    """Monkey-patch BinaryView.save so MCP automation cannot call it."""
    if bn_module is None:
        import binaryninja as bn_module

    binary_view_cls = getattr(bn_module, "BinaryView", None)
    if binary_view_cls is None:
        return False

    if getattr(binary_view_cls, _PATCHED_ATTR, False):
        return False

    original_save = getattr(binary_view_cls, "save", None)
    if original_save is None:
        return False

    setattr(binary_view_cls, _ORIGINAL_SAVE_ATTR, original_save)

    @wraps(original_save)
    def guarded_save(self: Any, *args: Any, **kwargs: Any) -> Any:
        log_error = getattr(bn_module, "log_error", None)
        if callable(log_error):
            log_error(f"[MCP] {_ERROR_MESSAGE}")
        raise RuntimeError(_ERROR_MESSAGE)

    guarded_save.__doc__ = _ERROR_MESSAGE
    setattr(binary_view_cls, "save", guarded_save)
    setattr(binary_view_cls, _PATCHED_ATTR, True)
    return True


__all__ = ["install_binaryview_save_guard"]
