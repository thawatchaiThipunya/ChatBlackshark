import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SqlImportBatchTests(unittest.TestCase):
    def test_imported_product_rows_keep_explicit_category_annotations(self):
        for source_index, expected_category in ((0, '"category":"Pad"'),
                                                (43, '"category":"หูฟัง Open-Ear"')):
            with self.subTest(source_index=source_index):
                result = subprocess.run(
                    [sys.executable, "backend/scripts/sql_import_batch.py", "products", str(source_index), "1"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                self.assertIn(expected_category, result.stdout)

    def test_product_import_preserves_existing_category_when_source_has_none(self):
        result = subprocess.run(
            [sys.executable, "backend/scripts/sql_import_batch.py", "products", "0", "1"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn(
            "category=coalesce(excluded.category,bot_products.category)",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
