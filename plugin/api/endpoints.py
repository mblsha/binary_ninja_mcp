from typing import Dict, Any, List, Optional
import binaryninja as bn
from ..core.annotation_archive import export_user_annotations
from ..core.binary_operations import BinaryOperations
from ..core.mutations import MutationTransaction, MutationVerificationError
from shared.build_info import snapshot_source

LOADED_SOURCE = snapshot_source(__file__)
SIGNATURE_WORKFLOW_VERSION = 2


class FunctionSignatureParseError(ValueError):
    """Raised when Binary Ninja rejects a complete function declaration."""


class BinaryNinjaEndpoints:
    def __init__(self, binary_ops: BinaryOperations):
        self.binary_ops = binary_ops

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the binary view"""
        return {
            "loaded": self.binary_ops.current_view is not None,
            "filename": self.binary_ops.current_view.file.filename
            if self.binary_ops.current_view
            else None,
        }

    def get_function_info(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a function"""
        try:
            return self.binary_ops.get_function_info(identifier)
        except Exception as e:
            bn.log_error(f"Error getting function info: {e}")
            return None

    def get_imports(self, offset: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of imported functions"""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        imports = []
        for sym in self.binary_ops.current_view.get_symbols_of_type(
            bn.SymbolType.ImportedFunctionSymbol
        ):
            imports.append(
                {
                    "name": sym.name,
                    "address": hex(sym.address),
                    "raw_name": sym.raw_name if hasattr(sym, "raw_name") else sym.name,
                    "full_name": sym.full_name if hasattr(sym, "full_name") else sym.name,
                }
            )
        return imports[offset : offset + limit]

    def get_exports(self, offset: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of exported symbols"""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        exports = []
        for sym in self.binary_ops.current_view.get_symbols():
            if sym.type not in [
                bn.SymbolType.ImportedFunctionSymbol,
                bn.SymbolType.ExternalSymbol,
            ]:
                exports.append(
                    {
                        "name": sym.name,
                        "address": hex(sym.address),
                        "raw_name": sym.raw_name if hasattr(sym, "raw_name") else sym.name,
                        "full_name": sym.full_name if hasattr(sym, "full_name") else sym.name,
                        "type": str(sym.type),
                    }
                )
        return exports[offset : offset + limit]

    def export_annotations(
        self,
        output_path: str,
        *,
        type_library_path: Optional[str] = None,
        overwrite: bool = False,
        include_unannotated_function_types: bool = False,
        source_id: Optional[str] = None,
        source_filename: Optional[str] = None,
        source_size: Optional[int] = None,
        source_mtime_ns: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Serialize portable user annotations and exact native types."""

        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")
        return export_user_annotations(
            self.binary_ops.current_view,
            output_path,
            type_library_path=type_library_path,
            overwrite=overwrite,
            include_unannotated_function_types=include_unannotated_function_types,
            source_id=source_id,
            source_filename=source_filename,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
        )

    def get_namespaces(self, offset: int = 0, limit: int = 100) -> List[str]:
        """Get list of C++ namespaces"""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        namespaces = set()
        for sym in self.binary_ops.current_view.get_symbols():
            if "::" in sym.name:
                parts = sym.name.split("::")
                if len(parts) > 1:
                    namespace = "::".join(parts[:-1])
                    namespaces.add(namespace)

        sorted_namespaces = sorted(list(namespaces))
        return sorted_namespaces[offset : offset + limit]

    def get_defined_data(self, offset: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of defined data variables"""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        data_items = []
        for var in self.binary_ops.current_view.data_vars:
            data_type = self.binary_ops.current_view.get_type_at(var)
            value = None

            try:
                if data_type and data_type.width <= 8:
                    value = str(self.binary_ops.current_view.read_int(var, data_type.width))
                else:
                    value = "(complex data)"
            except (ValueError, TypeError):
                value = "(unreadable)"

            sym = self.binary_ops.current_view.get_symbol_at(var)
            data_items.append(
                {
                    "address": hex(var),
                    "name": sym.name if sym else "(unnamed)",
                    "raw_name": sym.raw_name if sym and hasattr(sym, "raw_name") else None,
                    "value": value,
                    "type": str(data_type) if data_type else None,
                }
            )

        return data_items[offset : offset + limit]

    def search_functions(
        self, search_term: str, offset: int = 0, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search functions by name"""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        if not search_term:
            return []

        matches = []
        for func in self.binary_ops.current_view.functions:
            if search_term.lower() in func.name.lower():
                matches.append(
                    {
                        "name": func.name,
                        "address": hex(func.start),
                        "raw_name": func.raw_name if hasattr(func, "raw_name") else func.name,
                        "symbol": {
                            "type": str(func.symbol.type) if func.symbol else None,
                            "full_name": func.symbol.full_name if func.symbol else None,
                        }
                        if func.symbol
                        else None,
                    }
                )

        matches.sort(key=lambda x: x["name"])
        return matches[offset : offset + limit]

    def decompile_function(
        self, identifier: str, *, allow_analysis_skipped: bool = False
    ) -> Optional[str]:
        """Decompile a function by name or address"""
        try:
            return self.binary_ops.decompile_function(
                identifier,
                allow_analysis_skipped=allow_analysis_skipped,
            )
        except Exception as e:
            bn.log_error(f"Error decompiling function: {e}")
            return None

    def get_assembly_function(self, identifier: str) -> Optional[str]:
        """Get the assembly representation of a function by name or address"""
        try:
            return self.binary_ops.get_assembly_function(identifier)
        except Exception as e:
            bn.log_error(f"Error getting assembly for function: {e}")
            return None

    def define_types(self, c_code: str) -> Dict[str, str]:
        """Define types from C code string

        Args:
            c_code: C code string containing type definitions

        Returns:
            Dictionary mapping type names to their string representations

        Raises:
            RuntimeError: If no binary is loaded
            ValueError: If parsing the types fails
        """
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        try:
            # Parse the C code string to get type objects
            parse_result = self.binary_ops.current_view.parse_types_from_string(c_code)

            # Parse the complete declaration before opening an undo boundary.
            defined_types = {}
            view = self.binary_ops.current_view
            with MutationTransaction(view):
                for name, type_obj in parse_result.types.items():
                    view.define_user_type(name, type_obj)
                    defined_types[str(name)] = str(type_obj)
                for name, type_obj in parse_result.types.items():
                    actual = view.get_type_by_name(name)
                    if actual is None or self._normalize_type_text(
                        actual
                    ) != self._normalize_type_text(type_obj):
                        raise MutationVerificationError(
                            f"Type definition readback did not match: {name}"
                        )

            return defined_types
        except Exception as e:
            raise ValueError(f"Failed to define types: {str(e)}")

    def rename_variable(self, function_name: str, old_name: str, new_name: str) -> Dict[str, str]:
        """Rename a variable inside a function

        Args:
            function_name: Name of the function containing the variable
            old_name: Current name of the variable
            new_name: New name for the variable

        Returns:
            Dictionary with status message

        Raises:
            RuntimeError: If no binary is loaded
            ValueError: If the function is not found or variable cannot be renamed
        """
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        # Find the function by name
        function = self.binary_ops.get_function_by_name_or_address(function_name)
        if not function:
            raise ValueError(f"Function '{function_name}' not found")
        if function.analysis_skipped:
            raise ValueError("Function analysis is skipped; variable rename was not attempted")
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("Variable name must be a non-empty string")

        # Try to rename the variable
        try:
            # Get the variable by name and rename it
            variable = function.get_variable_by_name(old_name)
            if not variable:
                raise ValueError(f"Variable '{old_name}' not found in function '{function_name}'")

            with MutationTransaction(self.binary_ops.current_view):
                variable.name = new_name
                if variable.name != new_name:
                    raise MutationVerificationError("Variable rename readback did not match")
            return {
                "status": f"Successfully renamed variable '{old_name}' to '{new_name}' in function '{function_name}'"
            }
        except Exception as e:
            raise ValueError(f"Failed to rename variable: {str(e)}")

    def retype_variable(self, function_name: str, name: str, type_str: str) -> Dict[str, str]:
        """Retype a variable inside a function

        Args:
            function_name: Name of the function containing the variable
            name: Current name of the variable
            type: C type for the variable

        Returns:
            Dictionary with status message

        Raises:
            RuntimeError: If no binary is loaded
            ValueError: If the function is not found or variable cannot be retyped
        """
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        # Find the function by name
        function = self.binary_ops.get_function_by_name_or_address(function_name)
        if not function:
            raise ValueError(f"Function '{function_name}' not found")
        if function.analysis_skipped:
            raise ValueError("Function analysis is skipped; variable retype was not attempted")
        parsed_type, _ = self.binary_ops.current_view.parse_type_string(type_str)

        # Try to rename the variable
        try:
            # Get the variable by name and rename it
            variable = function.get_variable_by_name(name)
            if not variable:
                raise ValueError(f"Variable '{name}' not found in function '{function_name}'")

            with MutationTransaction(self.binary_ops.current_view):
                variable.type = parsed_type
                if self._normalize_type_text(variable.type) != self._normalize_type_text(
                    parsed_type
                ):
                    raise MutationVerificationError("Variable type readback did not match")
            return {
                "status": f"Successfully retyped variable '{name}' to '{type_str}' in function '{function_name}'"
            }
        except Exception as e:
            raise ValueError(f"Failed to retype variable: {str(e)}")

    @staticmethod
    def _normalize_type_text(value: Any) -> str:
        return " ".join(str(value).split())

    @classmethod
    def _signature_types_match(cls, requested, observed):
        """Compare declarations without treating inferred purity/return as input.

        BN may infer __pure or __noreturn after reanalysis. Only disregard that
        attribute when its confidence in the parsed request is zero (unspecified).
        Explicit attributes and every other rendered signature component remain
        part of verification. Keep the untouched observed type in the response.
        """
        normalized = observed
        for attribute in ("pure", "can_return"):
            requested_value = getattr(requested, attribute, None)
            if getattr(requested_value, "confidence", None) == 0:
                if normalized is observed:
                    normalized = observed.mutable_copy()
                setattr(normalized, attribute, requested_value)
        return cls._normalize_type_text(normalized) == cls._normalize_type_text(requested)

    def edit_function_signature(
        self,
        function_name: str,
        signature: str,
        *,
        apply_name: bool = False,
        reanalyze: bool = True,
        wait: bool = True,
        verify: bool = True,
        dry_run: bool = False,
        preview: bool = False,
    ) -> Dict[str, Any]:
        """Parse and safely apply a complete function declaration."""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        if dry_run and preview:
            raise ValueError("Choose dry_run (parse only) or preview (apply, verify, revert)")
        if preview and (not wait or not verify):
            raise ValueError("Preview requires wait=true and verify=true")
        if verify and not wait and not dry_run:
            raise ValueError("Verification requires wait=true; use verify=false for queued work")

        view = self.binary_ops.current_view
        function = self.binary_ops.get_function_by_name_or_address(function_name)
        if not function:
            raise ValueError(f"Function '{function_name}' not found")

        try:
            parsed_type, parsed_name = view.parse_type_string(signature)
            if parsed_type.type_class != bn.TypeClass.FunctionTypeClass:
                raise ValueError("The declaration must describe a function, not a data type")
        except Exception as exc:
            raise FunctionSignatureParseError(
                f"Binary Ninja could not parse the function declaration: {exc}"
            ) from exc
        parsed_name_text = str(parsed_name or "")
        requested_type = str(parsed_type)
        before_name = function.name
        before_type = str(function.type)
        analysis_skipped = bool(function.analysis_skipped)
        before_user_type = bool(function.has_user_type)

        result: Dict[str, Any] = {
            "success": True,
            "dry_run": bool(dry_run),
            "function_start": hex(function.start),
            "before_name": before_name,
            "before_type": before_type,
            "before_user_type": before_user_type,
            "parsed_name": parsed_name_text,
            "requested_type": requested_type,
            "apply_name": bool(apply_name),
            "reanalyze_requested": bool(reanalyze),
            "wait_requested": bool(wait),
            "verify_requested": bool(verify),
            "analysis_skipped": analysis_skipped,
            "preview": bool(preview),
            "committed": False,
            "rolled_back": False,
            "state_unknown": False,
            "restoration_verified": None,
        }
        if dry_run:
            result.update(
                {
                    "after_name": before_name,
                    "after_type": before_type,
                    "verified": None,
                    "message": "Signature parsed successfully; no changes applied.",
                }
            )
            return result

        if preview and not before_user_type:
            result.update(
                success=False,
                verified=False,
                error="Preview requires an existing user-defined signature; native undo may promote an automatic signature to a user type. Use --dry-run for parse-only validation.",
                error_code="AUTOMATIC_SIGNATURE_PREVIEW_UNSAFE",
            )
            return result

        if reanalyze and analysis_skipped:
            result.update(
                {
                    "success": False,
                    "verified": False,
                    "error": "Function analysis is skipped; signature was not changed.",
                    "help": (
                        "Clear the function's analysis-skipped state explicitly, then retry. "
                        "The signature endpoint will not clear it automatically."
                    ),
                }
            )
            return result

        transaction = MutationTransaction(view, preview=preview)
        try:
            with transaction:
                function.type = parsed_type
                if apply_name and parsed_name_text:
                    function.name = parsed_name_text

                if reanalyze:
                    function.reanalyze(bn.FunctionUpdateType.UserFunctionUpdate)
                if wait:
                    view.update_analysis_and_wait()

                fresh = view.get_function_at(function.start, function.platform)
                if fresh is None:
                    raise MutationVerificationError("Function disappeared during signature update")
                after_name = fresh.name
                after_type = str(fresh.type)
                type_matches = self._signature_types_match(parsed_type, fresh.type)
                name_matches = (
                    not apply_name or not parsed_name_text or after_name == parsed_name_text
                )
                verified = bool(type_matches and name_matches) if verify else None
                result.update(
                    after_name=after_name,
                    after_type=after_type,
                    type_matches=type_matches,
                    name_matches=name_matches,
                    verified=verified,
                )
                if verify and not verified:
                    raise MutationVerificationError(
                        "Function signature readback did not match the requested declaration."
                    )
            result["message"] = (
                "Signature preview verified and reverted; no changes committed."
                if preview
                else "Function signature applied and verified."
                if verify
                else "Function signature applied; verification was not requested."
            )
        except Exception as exc:
            result.update(success=False, error=str(exc), message=str(exc))
            result.setdefault("verified", False)
        if transaction.rolled_back:
            try:
                if wait:
                    view.update_analysis_and_wait()
                restored = view.get_function_at(function.start, function.platform)
                if restored is None:
                    raise MutationVerificationError(
                        "Function disappeared while verifying signature undo"
                    )
                result.update(
                    restored_name=restored.name,
                    restored_type=str(restored.type),
                    restored_user_type=bool(restored.has_user_type),
                )
                result["restoration_verified"] = (
                    restored.name == before_name
                    and self._normalize_type_text(restored.type)
                    == self._normalize_type_text(before_type)
                    and bool(restored.has_user_type) == before_user_type
                    and bool(restored.analysis_skipped) == analysis_skipped
                )
                if not result["restoration_verified"]:
                    raise MutationVerificationError(
                        "Undo returned, but original signature annotations were not restored"
                    )
            except Exception as exc:
                transaction.state_unknown = True
                message = f"Signature restoration verification failed: {exc}"
                if result.get("error"):
                    message = f"{result['error']}; {message}"
                result.update(
                    success=False,
                    verified=False,
                    restoration_verified=False,
                    error=message,
                    message=message,
                )
        result.update(transaction.as_dict())
        return result

    def reanalyze_function(
        self,
        function_name: str,
        *,
        wait: bool = True,
    ) -> Dict[str, Any]:
        """Explicitly reanalyze one function without changing its annotations."""
        if not self.binary_ops.current_view:
            raise RuntimeError("No binary loaded")

        view = self.binary_ops.current_view
        function = self.binary_ops.get_function_by_name_or_address(function_name)
        if not function:
            raise ValueError(f"Function '{function_name}' not found")
        if function.analysis_skipped:
            return {
                "success": False,
                "function": function.name,
                "function_start": hex(function.start),
                "analysis_skipped": True,
                "error": "Function analysis is skipped; reanalysis was not queued.",
                "help": "Clear the analysis-skipped state explicitly, then retry.",
            }

        function.reanalyze(bn.FunctionUpdateType.UserFunctionUpdate)
        if wait:
            view.update_analysis_and_wait()
        fresh = view.get_function_at(function.start, function.platform) or function
        return {
            "success": True,
            "function": fresh.name,
            "function_start": hex(fresh.start),
            "function_type": str(fresh.type),
            "analysis_skipped": bool(fresh.analysis_skipped),
            "wait_requested": bool(wait),
            "message": "Function reanalysis completed." if wait else "Function reanalysis queued.",
        }
