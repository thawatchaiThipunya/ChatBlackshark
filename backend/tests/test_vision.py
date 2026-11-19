import unittest

from backend.app.vision import VisionResult, describe_with_key_rotation


class VisionKeyRotationTests(unittest.TestCase):
    def test_tries_another_key_after_provider_error(self):
        keys = iter([("bad-key", 3, 2), ("good-key", 4, 2)])
        used = []

        def describe(data, mime_type, question, key):
            used.append(key)
            if key == "bad-key":
                raise RuntimeError("provider rejected key")
            return VisionResult("เห็นนาฬิกาหน้าจอมีรอย", "gemini-test")

        result = describe_with_key_rotation(b"image", "image/webp", "จอเป็นอะไร", lambda: next(keys), describe)

        self.assertEqual(result.description, "เห็นนาฬิกาหน้าจอมีรอย")
        self.assertEqual(used, ["bad-key", "good-key"])

    def test_does_not_repeat_a_failed_key(self):
        keys = iter([("bad-key", 3, 3), ("bad-key", 3, 3), ("good-key", 4, 3)])
        used = []

        def describe(data, mime_type, question, key):
            used.append(key)
            if key == "bad-key":
                raise RuntimeError("provider rejected key")
            return VisionResult("เห็นสินค้า", "gemini-test")

        result = describe_with_key_rotation(b"image", "image/png", "นี่คืออะไร", lambda: next(keys), describe)

        self.assertEqual(result.description, "เห็นสินค้า")
        self.assertEqual(used, ["bad-key", "good-key"])


if __name__ == "__main__":
    unittest.main()
