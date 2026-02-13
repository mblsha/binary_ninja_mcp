"""Compatibility adapter for console capture execute_command signatures."""

from __future__ import annotations

from typing import Any, Dict


class ConsoleCaptureAdapter:
    """Normalize legacy/new console capture backends to one execute signature."""

    def __init__(self, backend: Any):
        self._backend = backend

    def execute_command(
        self,
        command: str,
        *,
        binary_view=None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if not hasattr(self._backend, "execute_command"):
            raise RuntimeError("console capture backend does not support command execution")

        execute_command = self._backend.execute_command
        errors = []

        try:
            return execute_command(command, binary_view=binary_view, timeout=timeout)
        except TypeError as exc:
            errors.append(str(exc))

        try:
            return execute_command(command, binary_view)
        except TypeError as exc:
            errors.append(str(exc))

        try:
            return execute_command(command)
        except TypeError as exc:
            errors.append(str(exc))
            detail = "; ".join(errors)
            raise RuntimeError(f"unsupported console backend execute_command signature: {detail}")
