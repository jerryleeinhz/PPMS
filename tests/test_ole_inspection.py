from __future__ import annotations

import unittest

from ppms_control.ole_inspection import _methods_from_type_info


class _FakeTypeInfo:
    def GetTypeAttr(self):
        values = [None] * 9
        values[8] = 2
        return tuple(values)

    def GetFuncDesc(self, index: int):
        if index == 0:
            return (10, None, None, None, 1, None, 2, 0)
        return (20, None, None, None, 2, None, 1, 1)

    def GetNames(self, member_id: int):
        if member_id == 10:
            return ("StartSequence", "path", "mode")
        return ("GetSequenceStatus", "status")


class OleInspectionTests(unittest.TestCase):
    def test_type_information_is_converted_without_invoking_methods(self) -> None:
        methods = _methods_from_type_info(_FakeTypeInfo())
        self.assertEqual(
            [method.name for method in methods],
            ["GetSequenceStatus", "StartSequence"],
        )
        self.assertEqual(methods[1].parameter_names, ("path", "mode"))
        self.assertEqual(methods[0].optional_parameter_count, 1)


if __name__ == "__main__":
    unittest.main()
