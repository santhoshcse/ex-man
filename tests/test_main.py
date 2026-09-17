from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "main.py"
MODULE_SPEC = importlib.util.spec_from_file_location("exman_main", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
exman = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = exman
MODULE_SPEC.loader.exec_module(exman)


class DiscoveryAndCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "tools"
        self.root.mkdir()
        (self.root / "claude.exe").write_bytes(b"exe")
        nested_directory = self.root / "nested"
        nested_directory.mkdir()
        (nested_directory / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
        (nested_directory / "notes.txt").write_text("not executable\n", encoding="utf-8")
        self.cache_path = Path(self.temporary_directory.name) / "catalog.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_recursive_scan_records_supported_extensions(self) -> None:
        records, diagnostics = exman.scan_roots(
            [exman.RootSpec(self.root, "Custom", True)],
            (".exe", ".cmd"),
            workers=2,
        )

        self.assertEqual([], diagnostics)
        self.assertEqual(["claude.cmd", "claude.exe"], [record["filename"] for record in records])
        self.assertEqual(["claude", "claude"], [record["normalized_name"] for record in records])

    def test_catalog_search_and_inspection_round_trip(self) -> None:
        records, diagnostics = exman.scan_roots(
            [exman.RootSpec(self.root, "Custom", True)],
            (".exe", ".cmd"),
            workers=1,
        )
        exman.write_catalog(self.cache_path, records, diagnostics)

        catalog = exman.load_catalog(self.cache_path)
        matches = exman.find_records(catalog["records"], "CLAUDE", "Custom", ".exe")

        self.assertEqual(1, len(matches))
        self.assertEqual("claude.exe", matches[0]["filename"])
        self.assertTrue(matches[0]["id"])
        self.assertTrue(json.loads(self.cache_path.read_text(encoding="utf-8"))["records"])

    def test_missing_catalog_has_actionable_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "run 'scan' first"):
            exman.load_catalog(self.cache_path)


if __name__ == "__main__":
    unittest.main()