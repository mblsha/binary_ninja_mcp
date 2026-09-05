"""Read-only, ambiguity-aware identifiers, independent of the Binary Ninja SDK."""

import re

from shared.build_info import snapshot_source

LOADED_SOURCE = snapshot_source(__file__)


class AnalysisError(ValueError):
    def __init__(self, code, message, *, candidates=None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates

    def as_dict(self):
        result = {"code": self.code, "message": str(self)}
        if self.candidates is not None:
            result["candidates"] = self.candidates
        return result


def function_key(func):
    return int(func.start), str(func.arch.name), str(getattr(func, "platform", ""))


def function_identity(func):
    return {
        "name": str(func.name),
        "raw_name": str(getattr(func, "raw_name", func.name)),
        "address": hex(func.start),
        "architecture": str(func.arch.name),
    }


class IdentifierResolver:
    def __init__(self, view):
        self.view = view
        self._functions = None

    def _named_addresses(self, name):
        if self._functions is None:
            self._functions = list(self.view.functions)
        addresses = {int(f.start) for f in self._functions if str(f.name) == name}
        for lookup in (self.view.get_symbols_by_name, self.view.get_symbols_by_raw_name):
            addresses.update(int(sym.address) for sym in lookup(name))
        # Retain the legacy case-insensitive function-name convenience, but never
        # pick the first ambiguous match. Exact spellings always take precedence.
        if not addresses:
            addresses = {
                int(f.start) for f in self._functions if str(f.name).casefold() == name.casefold()
            }
        return sorted(addresses)

    @staticmethod
    def _checked_address(address):
        if not 0 <= address <= 0xFFFFFFFFFFFFFFFF:
            raise AnalysisError("invalid_address", "Address is outside the unsigned 64-bit range")
        return address

    def address(self, identifier):
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise AnalysisError(
                "invalid_identifier", "Identifier must be a name or integer address"
            )
        if isinstance(identifier, int):
            return self._checked_address(identifier)
        text = identifier.strip()
        if not text:
            raise AnalysisError("invalid_identifier", "Identifier must not be empty")
        try:
            return self._checked_address(int(text, 16 if text.lower().startswith("0x") else 10))
        except ValueError as exc:
            if isinstance(exc, AnalysisError):
                raise
        addresses = self._named_addresses(text)
        if not addresses:
            match = re.fullmatch(r"(.+?)\s*([+-])\s*(0[xX][0-9a-fA-F]+|[0-9]+)", text)
            if match:
                base, sign, offset = match.groups()
                displacement = int(offset, 16 if offset.lower().startswith("0x") else 10)
                return self._checked_address(
                    self.address(base.strip()) + (displacement if sign == "+" else -displacement)
                )
            raise AnalysisError("not_found", f"No symbol or function matches {text!r}")
        if len(addresses) != 1:
            raise AnalysisError(
                "ambiguous_identifier",
                f"Ambiguous identifier {text!r}; use an address",
                candidates=[hex(a) for a in addresses],
            )
        return addresses[0]

    def function(self, identifier, *, allow_containing=True):
        address = self.address(identifier)
        functions = list(self.view.get_functions_at(address))
        if not functions and allow_containing:
            functions = list(self.view.get_functions_containing(address))
        unique = {function_key(func): func for func in functions}
        if not unique:
            raise AnalysisError(
                "not_found",
                f"No function {'at or containing' if allow_containing else 'at'} {hex(address)}",
            )
        if len(unique) != 1:
            raise AnalysisError(
                "ambiguous_function",
                f"Multiple functions match {hex(address)}",
                candidates=[function_identity(f) for f in unique.values()],
            )
        return next(iter(unique.values()))
