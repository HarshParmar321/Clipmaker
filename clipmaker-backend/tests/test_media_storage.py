import io
import tempfile
import unittest
from pathlib import Path

from services.media_storage import (
    LocalMediaStorage,
    UnsupportedVideoError,
    UploadTooLargeError,
)


JOB_ID = "11111111-1111-4111-8111-111111111111"


class LocalMediaStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalMediaStorage(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_upload_ignores_untrusted_filename_paths(self):
        path, size = self.storage.save_upload(
            JOB_ID, "../../another-directory/secret.mp4", io.BytesIO(b"video")
        )
        expected = (Path(self.temp.name) / JOB_ID / "source" / "original.mp4").resolve()
        self.assertEqual(path, expected)
        self.assertEqual(size, 5)
        self.assertEqual(path.read_bytes(), b"video")

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(UnsupportedVideoError):
            self.storage.save_upload(JOB_ID, "payload.exe", io.BytesIO(b"bad"))

    def test_oversized_upload_removes_partial_file(self):
        with self.assertRaises(UploadTooLargeError):
            self.storage.save_upload(JOB_ID, "video.mp4", io.BytesIO(b"0123456789"), max_bytes=5)
        self.assertFalse((Path(self.temp.name) / JOB_ID / "source" / "original.mp4").exists())

    def test_refuses_to_delete_outside_storage_root(self):
        outside = Path(self.temp.name).parent / "outside.mp4"
        with self.assertRaises(ValueError):
            self.storage.delete(outside)


if __name__ == "__main__":
    unittest.main()
