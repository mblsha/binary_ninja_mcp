import importlib.util
from pathlib import Path


def load_save_guard_module():
    path = Path(__file__).parent / "plugin" / "core" / "save_guard.py"
    spec = importlib.util.spec_from_file_location("save_guard_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_fake_binaryninja():
    class FakeBinaryView:
        def __init__(self):
            self.save_calls = []

        def save(self, *args, **kwargs):
            self.save_calls.append((args, kwargs))
            return "saved"

    class FakeBinaryNinja:
        BinaryView = FakeBinaryView
        errors = []

        @classmethod
        def log_error(cls, message):
            cls.errors.append(message)

    return FakeBinaryNinja


def test_install_blocks_binaryview_save():
    save_guard = load_save_guard_module()
    fake_bn = make_fake_binaryninja()

    assert save_guard.install_binaryview_save_guard(fake_bn) is True

    bv = fake_bn.BinaryView()
    try:
        bv.save("/tmp/anything.bndb")
    except RuntimeError as exc:
        assert "BinaryView.save(...)" in str(exc)
        assert "save_auto_snapshot" in str(exc)
        assert "create_database" in str(exc)
    else:
        raise AssertionError("BinaryView.save should be blocked")

    assert bv.save_calls == []
    assert fake_bn.errors


def test_install_is_idempotent():
    save_guard = load_save_guard_module()
    fake_bn = make_fake_binaryninja()

    assert save_guard.install_binaryview_save_guard(fake_bn) is True
    first_guard = fake_bn.BinaryView.save

    assert save_guard.install_binaryview_save_guard(fake_bn) is False
    assert fake_bn.BinaryView.save is first_guard
