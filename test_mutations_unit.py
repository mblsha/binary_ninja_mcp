"""The shared undo boundary must never leave an unreported partial mutation."""

import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "mutations_unit_target", Path(__file__).parent / "plugin/core/mutations.py"
)
mutations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutations)


class View:
    def __init__(self):
        self.value = "before"
        self.events = []

    def begin_undo_actions(self):
        self.events.append("begin")
        self.before = self.value
        return "test-id"

    def commit_undo_actions(self, state):
        assert state == "test-id"
        self.events.append("commit")

    def revert_undo_actions(self, state):
        assert state == "test-id"
        self.events.append("revert")
        self.value = self.before


def test_commit_does_not_save():
    view = View()
    with mutations.MutationTransaction(view) as transaction:
        view.value = "after"
    assert view.value == "after"
    assert transaction.committed
    assert view.events == ["begin", "commit"]


@pytest.mark.parametrize("error", [ValueError("apply failed"), KeyboardInterrupt()])
def test_every_apply_failure_reverts(error):
    view = View()
    transaction = mutations.MutationTransaction(view)
    with pytest.raises(type(error)):
        with transaction:
            view.value = "partial"
            raise error
    assert view.value == "before"
    assert transaction.rolled_back
    assert not transaction.committed


def test_preview_restores_before_state():
    view = View()
    with mutations.MutationTransaction(view, preview=True) as transaction:
        view.value = "preview"
    assert view.value == "before"
    assert transaction.rolled_back
    assert not transaction.committed


def test_missing_undo_support_refuses_to_enter():
    with pytest.raises(RuntimeError, match="requires BinaryView"):
        with mutations.MutationTransaction(object()):
            pytest.fail("must not execute")


def test_commit_failure_attempts_rollback():
    view = View()

    def fail_commit(_state):
        raise RuntimeError("commit failed")

    view.commit_undo_actions = fail_commit
    transaction = mutations.MutationTransaction(view)
    with pytest.raises(RuntimeError, match="commit failed"):
        with transaction:
            view.value = "partial"
    assert view.value == "before"
    assert transaction.rolled_back


def test_rollback_failure_preserves_original_failure_in_diagnostic():
    view = View()

    def fail_rollback(_state):
        raise RuntimeError("rollback failed")

    view.revert_undo_actions = fail_rollback
    transaction = mutations.MutationTransaction(view)
    with pytest.raises(mutations.MutationRollbackError, match="original failure: apply failed"):
        with transaction:
            view.value = "partial"
            raise ValueError("apply failed")
    assert transaction.state_unknown
    assert not transaction.rolled_back
    assert not transaction.committed
