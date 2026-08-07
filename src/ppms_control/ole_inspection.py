from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class OleInspectionError(RuntimeError):
    """Raised when an active MultiVu OLE object cannot be inspected safely."""


@dataclass(frozen=True)
class OleMethod:
    name: str
    parameter_names: tuple[str, ...]
    parameter_count: int
    optional_parameter_count: int
    invocation_kind: int

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "parameter_names": list(self.parameter_names),
            "parameter_count": self.parameter_count,
            "optional_parameter_count": self.optional_parameter_count,
            "invocation_kind": self.invocation_kind,
        }


def _methods_from_type_info(type_info: Any) -> tuple[OleMethod, ...]:
    type_attributes = type_info.GetTypeAttr()
    function_count = int(type_attributes[8])
    methods: list[OleMethod] = []
    for index in range(function_count):
        function = type_info.GetFuncDesc(index)
        member_id = int(function[0])
        invocation_kind = int(function[4])
        parameter_count = int(function[6])
        optional_parameter_count = int(function[7])
        names = tuple(str(name) for name in type_info.GetNames(member_id))
        if not names:
            continue
        methods.append(
            OleMethod(
                name=names[0],
                parameter_names=names[1:],
                parameter_count=parameter_count,
                optional_parameter_count=optional_parameter_count,
                invocation_kind=invocation_kind,
            )
        )
    return tuple(sorted(methods, key=lambda method: method.name.casefold()))


def inspect_active_multivu_ole(
    progid: str = "QD.MULTIVU.DYNACOOL.1",
) -> dict[str, object]:
    """Enumerate type information without invoking any MultiVu method."""

    try:
        import win32com.client
    except ImportError as exc:
        raise OleInspectionError("pywin32 is required for OLE inspection.") from exc
    try:
        active_object = win32com.client.GetActiveObject(progid)
    except Exception as exc:
        raise OleInspectionError(
            f"No active MultiVu OLE object was available for {progid}: {exc}"
        ) from exc
    try:
        type_info = active_object._oleobj_.GetTypeInfo()
        methods = _methods_from_type_info(type_info)
    except Exception as exc:
        raise OleInspectionError(f"Could not read MultiVu OLE type information: {exc}") from exc
    return {
        "mode": "read-only-active-object-type-inspection",
        "progid": progid,
        "method_count": len(methods),
        "methods": [method.as_dict() for method in methods],
        "sequence_candidates": [
            method.as_dict()
            for method in methods
            if any(
                token in method.name.casefold()
                for token in ("sequence", "seq", "run", "start", "stop", "abort")
            )
        ],
    }
