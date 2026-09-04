"""Failure-safe undo boundaries for built-in live-view mutations.

An undo commit is not a database save. Arbitrary console Python and concurrent
user edits are outside this helper's coordination boundary.
"""

from threading import RLock


_mutation_lock = RLock()


class MutationVerificationError(RuntimeError):
    """The observed live state did not match the requested mutation."""


class MutationRollbackError(RuntimeError):
    """Rollback failed; callers must not claim the original state was restored."""


class MutationTransaction:
    """Commit on success, revert on preview or any apply/verify exception.

    Resolve targets and parse all inputs before entering. Never silently fall
    back to unprotected writes when undo support is unavailable.
    """

    def __init__(self, view, *, preview=False):
        self.view = view
        self.preview = bool(preview)
        self.state = None
        self.committed = False
        self.rolled_back = False
        self.state_unknown = False

    def __enter__(self):
        _mutation_lock.acquire()
        try:
            for name in ("begin_undo_actions", "commit_undo_actions", "revert_undo_actions"):
                if not callable(getattr(self.view, name, None)):
                    raise RuntimeError(f"Safe mutation requires BinaryView.{name}")
            self.state = self.view.begin_undo_actions()
            if not self.state:
                raise RuntimeError("Binary Ninja did not return a scoped undo action ID")
            return self
        except BaseException:
            _mutation_lock.release()
            raise

    def _rollback(self, original_error=None):
        try:
            self.view.revert_undo_actions(self.state)
            self.rolled_back = True
        except BaseException as rollback_error:
            self.state_unknown = True
            message = f"Rollback failed; live state must be inspected: {rollback_error}"
            if original_error is not None:
                message += f" (original failure: {original_error})"
            raise MutationRollbackError(message) from rollback_error

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None or self.preview:
                self._rollback(exc)
            else:
                try:
                    self.view.commit_undo_actions(self.state)
                    self.committed = True
                except BaseException as commit_error:
                    self._rollback(commit_error)
                    raise
        finally:
            _mutation_lock.release()
        return False

    def as_dict(self):
        return {
            "preview": self.preview,
            "committed": self.committed,
            "rolled_back": self.rolled_back,
            "state_unknown": self.state_unknown,
        }
