"""Helpers for selecting and syncing the active BinaryView from UI state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def extract_view_filename(view: Any) -> Optional[str]:
    """Best-effort filename extraction from a BinaryView-like object."""
    try:
        file_obj = getattr(view, "file", None)
        filename = getattr(file_obj, "filename", None) if file_obj is not None else None
        if filename:
            return str(filename)
    except Exception:
        return None
    return None


def make_filename_candidates(raw: Optional[str]) -> set[str]:
    """Build case-insensitive path/name candidates for robust filename matching."""
    if not raw:
        return set()
    text = str(raw)
    lowered = text.lower()
    candidates = {text, lowered}
    try:
        resolved = str(Path(text).resolve())
        candidates.add(resolved)
        candidates.add(resolved.lower())
    except Exception:
        pass
    base = Path(text).name
    candidates.add(base)
    candidates.add(base.lower())
    return candidates


def make_path_candidates(raw: Optional[str]) -> set[str]:
    """Build case-insensitive path-like candidates (excluding basename-only variants)."""
    if not raw:
        return set()
    text = str(raw)
    candidates = {text, text.lower()}
    try:
        resolved = str(Path(text).resolve())
        candidates.add(resolved)
        candidates.add(resolved.lower())
    except Exception:
        pass
    return candidates


def filename_match_tier(view: Any, requested_filename: Optional[str]) -> int:
    """Return match strength: 2 path/exact match, 1 basename/loose match, 0 no match."""
    if not requested_filename:
        return 0
    view_name = extract_view_filename(view)
    if not view_name:
        return 0

    wanted_path = make_path_candidates(requested_filename)
    observed_path = make_path_candidates(view_name)
    if wanted_path.intersection(observed_path):
        return 2

    wanted = make_filename_candidates(requested_filename)
    observed = make_filename_candidates(view_name)
    if wanted.intersection(observed):
        return 1
    return 0


def matches_requested_filename(view: Any, requested_filename: Optional[str]) -> bool:
    """Return True if a UI view appears to correspond to requested filename."""
    return filename_match_tier(view, requested_filename) > 0


def get_view_from_frame(view_frame: Any) -> Any:
    """Extract a BinaryView-like object from a UI view frame.

    Prefer the active interface payload (`getCurrentViewInterface().getData()`) and
    only then fallback to `getCurrentBinaryView()`.
    """
    if view_frame is None:
        return None
    try:
        view_iface = view_frame.getCurrentViewInterface()
        if view_iface is not None and hasattr(view_iface, "getData"):
            view = view_iface.getData()
            if view is not None:
                return view
    except Exception:
        pass
    try:
        view = view_frame.getCurrentBinaryView()
        if view is not None:
            return view
    except Exception:
        pass
    return None


def list_ui_views(binaryninjaui_module: Any) -> list[Any]:
    """Return UI BinaryViews in deterministic priority order.

    Order:
    1) active context current frame
    2) each context current frame
    3) each context tab frames
    4) UIContext.currentBinaryView fallback
    """
    if binaryninjaui_module is None:
        return []
    ui_context_cls = getattr(binaryninjaui_module, "UIContext", None)
    if ui_context_cls is None:
        return []

    seen_ids: set[int] = set()
    views: list[Any] = []

    def add_view(view: Any) -> None:
        if view is None:
            return
        ident = id(view)
        if ident in seen_ids:
            return
        seen_ids.add(ident)
        views.append(view)

    ordered_contexts: list[Any] = []
    try:
        if hasattr(ui_context_cls, "activeContext"):
            active_ctx = ui_context_cls.activeContext()
            if active_ctx is not None:
                ordered_contexts.append(active_ctx)
    except Exception:
        pass

    try:
        all_contexts = list(ui_context_cls.allContexts())
    except Exception:
        all_contexts = []
    for ctx in all_contexts:
        if ctx not in ordered_contexts:
            ordered_contexts.append(ctx)

    for ctx in ordered_contexts:
        try:
            frame = ctx.getCurrentViewFrame()
        except Exception:
            frame = None
        add_view(get_view_from_frame(frame))

    for ctx in ordered_contexts:
        try:
            tabs = list(ctx.getTabs())
        except Exception:
            tabs = []
        for tab in tabs:
            try:
                frame = ctx.getViewFrameForTab(tab)
            except Exception:
                frame = None
            add_view(get_view_from_frame(frame))

    try:
        if hasattr(ui_context_cls, "currentBinaryView"):
            add_view(ui_context_cls.currentBinaryView())
    except Exception:
        pass

    return views


def select_preferred_view(ui_views: list[Any], requested_filename: Optional[str] = None) -> Any:
    """Select best candidate: filename match first, otherwise first available view."""
    if requested_filename:
        for view in ui_views:
            if filename_match_tier(view, requested_filename) >= 2:
                return view
        for view in ui_views:
            if matches_requested_filename(view, requested_filename):
                return view
    return ui_views[0] if ui_views else None
