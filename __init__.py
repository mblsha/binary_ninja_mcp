try:
    from .plugin import BinaryNinjaMCP
except ModuleNotFoundError as exc:
    if exc.name == "binaryninja":
        BinaryNinjaMCP = None
    else:
        raise

__all__ = ["BinaryNinjaMCP"]
