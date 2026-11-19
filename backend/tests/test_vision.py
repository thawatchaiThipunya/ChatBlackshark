import unittest

from backend.app.vision import VisionResult, describe_with_key_rotation, describe_batch_with_key_rotation


class VisionKeyRotationTests(unittest.TestCase):
    def test_batch_retries_all_keys_until_last_key_succeeds(self):
        keys = iter([(f"key-{i}", i, 5) for i in range(5)])
        used = []
        def describe(items, question, key):
            used.append(key)
            if key != "key-4":
                raise RuntimeError("provider failed")
            return [VisionResult("เห็นสาย", "gemini-test")]

        result = describe_batch_with_key_rotation([(b"image", "image/png")], "ดูสาย",
            lambda: next(keys), describe)

        self.assertEqual(result[0].description, "เห็นสาย")
        self.assertEqual(used, [f"key-{i}" for i in range(5)])

    def test_batch_describes_five_media_in_one_provider_call(self):
        media = [(bytes([index]), "image/webp") for index in range(5)]
        calls = []
        def describe(items, question, key):
            calls.append((items, question, key))
            return [VisionResult(f"ภาพ {index}", "gemini-test") for index in range(len(items))]

        result = describe_batch_with_key_rotation(media, "ดูภาพ", lambda: ("key", 1, 1), describe)

        self.assertEqual(len(calls), 1)
        self.assertEqual([item.description for item in result], [f"ภาพ {index}" for index in range(5)])
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
