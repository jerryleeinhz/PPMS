from __future__ import annotations

import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = PROJECT_ROOT / "notebooks" / "transport_analysis.ipynb"


class AnalysisNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.code_cells = [
            cell for cell in cls.notebook["cells"] if cell["cell_type"] == "code"
        ]
        cls.source = "\n".join("".join(cell["source"]) for cell in cls.code_cells)

    def test_code_cells_compile_and_committed_outputs_are_empty(self) -> None:
        for index, cell in enumerate(self.code_cells):
            compile("".join(cell["source"]), f"notebook-cell-{index}", "exec")
            self.assertIsNone(cell["execution_count"])
            self.assertEqual(cell["outputs"], [])

    def test_notebook_uses_analysis_api_without_hardware_control_imports(self) -> None:
        for forbidden in (
            "import subprocess",
            "os.system(",
            "SafeStation",
            "real_instruments",
            "hardware_run import",
            "pyvisa",
        ):
            self.assertNotIn(forbidden, self.source)
        for required in (
            "load_sqlite_run",
            "load_eto_path",
            "list_sqlite_runs",
            "generate_publication_plots",
            "SOURCE_PATH_TEXT = ''",
        ):
            self.assertIn(required, self.source)

    def test_default_configuration_does_not_select_experimental_data(self) -> None:
        self.assertIn("SOURCE_PATH_TEXT = ''", self.source)
        self.assertIn("GATE_CALIBRATION_PATH_TEXT = ''", self.source)
        self.assertEqual(self.notebook["metadata"]["kernelspec"]["name"], "ai")

    def test_helpers_do_not_mix_databases_or_draw_false_continuous_lines(self) -> None:
        self.assertIn("database_from_config", self.source)
        self.assertNotIn("DATABASE_FOR_MONITOR", self.source)
        self.assertIn("set(CHANNEL_ROLES.values()) <= {'xx', 'xy'}", self.source)
        self.assertIn("plt.scatter(", self.source)
        self.assertNotIn("plt.plot(", self.source)


if __name__ == "__main__":
    unittest.main()
