import subprocess
import sys
import base64
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def decoded_rows(sql):
    match = re.search(r"decode\('([A-Za-z0-9+/=]+)','base64'\)", sql)
    if not match:
        raise AssertionError("generated SQL has no base64 JSON payload")
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


class SqlImportBatchTests(unittest.TestCase):
    def test_custom_dataset_offset_is_applied_and_payload_is_utf8_safe(self):
        data = {
            "products": [{"name": "รุ่น O'Brien", "description": "สีฟ้า", "source": {}}],
            "faqs": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            data_path = Path(temporary) / "zaapi.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "backend/scripts/sql_import_batch.py", "products", "0", "1",
                 "--data", str(data_path), "--offset", "44"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

        rows = decoded_rows(result.stdout)
        self.assertEqual(rows[0]["source_index"], 44)
        self.assertEqual(rows[0]["name"], "รุ่น O'Brien")

    def test_imported_product_rows_keep_explicit_category_annotations(self):
        for source_index, expected_category in ((0, "Pad"), (43, "หูฟัง Open-Ear")):
            with self.subTest(source_index=source_index):
                result = subprocess.run(
                    [sys.executable, "backend/scripts/sql_import_batch.py", "products", str(source_index), "1"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(decoded_rows(result.stdout)[0]["category"], expected_category)

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
