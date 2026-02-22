"""Helpers for selecting and syncing the active BinaryView from UI state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional


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


def extract_view_id(view: Any) -> Optional[str]:
    """Best-effort BinaryView id extraction.

    Uses explicit attributes when available and falls back to Python object identity.
    """
    if view is None:
        return None

    for attr in ("view_id", "session_id", "identifier"):
        try:
            raw = getattr(view, attr, None)
            if raw is not None:
                text = str(raw).strip()
                if text:
                    return text
        except Exception:
            continue

    try:
        return str(id(view))
    except Exception:
        return None


def make_view_id_candidates(raw: Optional[str]) -> set[str]:
    """Build normalized candidate strings for robust view-id matching."""
    if raw is None:
        return set()
    text = str(raw).strip()
    if not text:
        return set()

    candidates = {text, text.lower()}

    try:
        value = int(text, 0)
        candidates.add(str(value))
        candidates.add(hex(value))
    except Exception:
        pass

    return candidates


def matches_requested_view_id(view: Any, requested_view_id: Optional[str]) -> bool:
    """Return True if a UI view appears to correspond to requested view id."""
    observed = extract_view_id(view)
    if not observed or requested_view_id is None:
        return False
    return bool(
        make_view_id_candidates(observed).intersection(make_view_id_candidates(requested_view_id))
    )


def _coerce_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except Exception:
        pass

    text = _coerce_text(value)
    if not text:
        return None
    try:
        return int(text, 0)
    except Exception:
        return None


def _coerce_analysis_state_name(value: Any, fallback_text: Optional[str]) -> Optional[str]:
    direct_name = _coerce_text(getattr(value, "name", None))
    if direct_name:
        return direct_name

    text = _coerce_text(fallback_text)
    if not text:
        return None

    if "." in text:
        suffix = text.rsplit(".", 1)[-1].strip()
        if suffix:
            return suffix

    # Avoid reporting raw numeric strings as state names.
    if _coerce_int(text) is not None:
        return None

    return text


def _analysis_state_name_from_code(state_code: Optional[int]) -> Optional[str]:
    if state_code is None:
        return None

    try:
        from binaryninja.enums import AnalysisState as _BNAnalysisState
    except Exception as exc:
        raise RuntimeError(
            "binaryninja.enums.AnalysisState import failed while resolving analysis_state_name"
        ) from exc

    try:
        return str(_BNAnalysisState(int(state_code)).name)
    except Exception as exc:
        raise RuntimeError(
            f"unable to resolve analysis_state_name for analysis_state_code={state_code!r}"
        ) from exc


def _extract_view_type(view: Any) -> Optional[str]:
    if view is None:
        return None

    for attr in ("view_type", "view_type_name"):
        try:
            raw = getattr(view, attr, None)
        except Exception:
            raw = None
        if raw is None:
            continue
        if isinstance(raw, str):
            text = _coerce_text(raw)
            if text:
                return text
        name = _coerce_text(getattr(raw, "name", None))
        if name:
            return name
        text = _coerce_text(raw)
        if text:
            return text
    return None


def _extract_architecture(view: Any) -> Optional[str]:
    if view is None:
        return None
    try:
        arch = getattr(view, "arch", None)
    except Exception:
        arch = None
    if arch is None:
        return None
    name = _coerce_text(getattr(arch, "name", None))
    if name:
        return name
    return _coerce_text(arch)


def _extract_analysis_state_fields(view: Any) -> tuple[Optional[int], Optional[str], str]:
    if view is None:
        return None, None, "none"

    raw_values: list[Any] = []

    for attr in ("analysis_state", "analysis_status"):
        try:
            raw = getattr(view, attr, None)
        except Exception:
            raw = None
        if raw is not None:
            raw_values.append(raw)

    info = None
    try:
        info = getattr(view, "analysis_info", None)
        if callable(info):
            info = info()
    except Exception:
        info = None
    if info is not None:
        try:
            state = getattr(info, "state", None)
        except Exception:
            state = None
        if state is not None:
            raw_values.append(state)
        raw_values.append(info)

    try:
        progress = getattr(view, "analysis_progress", None)
    except Exception:
        progress = None
    if progress is not None:
        raw_values.append(progress)

    state_code: Optional[int] = None
    state_name: Optional[str] = None
    state_status: Optional[str] = None

    for raw in raw_values:
        text = _coerce_text(raw)
        if state_code is None:
            state_code = _coerce_int(raw)
        if state_name is None:
            state_name = _coerce_analysis_state_name(raw, text)
        if state_status is None and text:
            state_status = text

    if state_name is None:
        state_name = _analysis_state_name_from_code(state_code)

    if state_status is None:
        state_status = "unknown"

    return state_code, state_name, state_status


def describe_view(view: Any) -> dict[str, Any]:
    """Return normalized metadata for a BinaryView-like object."""
    filename = extract_view_filename(view)
    basename = Path(filename).name if filename else None
    analysis_state_code, analysis_state_name, analysis_status = _extract_analysis_state_fields(view)
    return {
        "view_id": extract_view_id(view),
        "filename": filename,
        "basename": basename,
        "view_type": _extract_view_type(view),
        "architecture": _extract_architecture(view),
        # Keep analysis_status for backward compatibility; add structured fields for robust parsing.
        "analysis_status": analysis_status,
        "analysis_state_code": analysis_state_code,
        "analysis_state_name": analysis_state_name,
    }


def resolve_target_view(
    requested_view_id: Optional[str],
    requested_filename: Optional[str],
    *,
    get_view_by_id: Callable[[str], Any],
    get_view_by_filename: Callable[[str], Any],
    fallback_view: Any = None,
) -> tuple[Any, Optional[dict]]:
    """Resolve requested target view using optional view-id/filename selectors."""
    selected_by_id = None
    if requested_view_id:
        selected_by_id = get_view_by_id(str(requested_view_id))
        if selected_by_id is None:
            return None, {
                "error": "Requested BinaryView not found",
                "view_id": requested_view_id,
                "help": "Open the target file first or use `--filename` to select by path.",
            }

    selected_by_filename = None
    if requested_filename:
        selected_by_filename = get_view_by_filename(str(requested_filename))
        if selected_by_filename is None:
            return None, {
                "error": "Requested filename is not loaded",
                "filename": requested_filename,
                "help": "Open the target file first or provide a matching --view-id.",
            }

    if selected_by_id is not None and selected_by_filename is not None:
        if selected_by_id is not selected_by_filename:
            if not (
                matches_requested_view_id(selected_by_filename, str(requested_view_id))
                and matches_requested_filename(selected_by_id, str(requested_filename))
            ):
                return None, {
                    "error": "Conflicting BinaryView targets",
                    "view_id": requested_view_id,
                    "filename": requested_filename,
                    "help": "Use either --view-id or --filename, or ensure they point to the same view.",
                }
        return selected_by_id, None

    if selected_by_id is not None:
        return selected_by_id, None
    if selected_by_filename is not None:
        return selected_by_filename, None
    return fallback_view, None


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


def select_preferred_view(
    ui_views: list[Any],
    requested_filename: Optional[str] = None,
    requested_view_id: Optional[str] = None,
) -> Any:
    """Select best candidate: view-id match, then filename match, otherwise first available view."""
    if requested_view_id:
        for view in ui_views:
            if matches_requested_view_id(view, requested_view_id):
                return view

    if requested_filename:
        for view in ui_views:
            if filename_match_tier(view, requested_filename) >= 2:
                return view
        for view in ui_views:
            if matches_requested_filename(view, requested_filename):
                return view
    return ui_views[0] if ui_views else None
