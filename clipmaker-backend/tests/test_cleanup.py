import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.cleanup import cleanup_expired_artifacts
from services.job_store import Artifact, ArtifactKind
from services.media_storage import LocalMediaStorage


JOB_ID = "22222222-2222-4222-8222-222222222222"


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalMediaStorage(Path(self.temp.name))
        self.path = self.storage.output_path(JOB_ID, "33333333-3333-4333-8333-333333333333")
        self.path.write_bytes(b"123456")
        self.artifact = Artifact(
            job_id=JOB_ID,
            kind=ArtifactKind.FINAL_CLIP,
            local_path=str(self.path),
            size_bytes=6,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

    def tearDown(self):
        self.temp.cleanup()

    @patch("services.cleanup.mark_artifact_deleted")
    @patch("services.cleanup.list_expired_artifacts")
    def test_dry_run_reports_without_deleting(self, list_expired, mark_deleted):
        list_expired.return_value = [self.artifact]
        result = cleanup_expired_artifacts(storage=self.storage, dry_run=True)
        self.assertTrue(self.path.exists())
        self.assertEqual(result.artifacts, 1)
        self.assertEqual(result.bytes_reclaimed, 6)
        mark_deleted.assert_not_called()

    @patch("services.cleanup.mark_artifact_deleted")
    @patch("services.cleanup.list_expired_artifacts")
    def test_execute_deletes_and_marks_artifact(self, list_expired, mark_deleted):
        list_expired.return_value = [self.artifact]
        result = cleanup_expired_artifacts(storage=self.storage, dry_run=False)
        self.assertFalse(self.path.exists())
        self.assertEqual(result.artifacts, 1)
        self.assertEqual(result.bytes_reclaimed, 6)
        mark_deleted.assert_called_once_with(self.artifact.id)


if __name__ == "__main__":
    unittest.main()
