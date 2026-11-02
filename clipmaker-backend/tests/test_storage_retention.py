"""Disk has to look after itself.

Three separate holes let media pile up. Expiry only sees files that have an
artifact row, so anything whose row never existed or was removed stays forever.
Expiry is also time-based, so a burst of long videos fills the disk while every
one of them is still hours from expiring. And there was no way to clear test
data short of `rm -rf` in a home directory.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import cleanup  # noqa: E402
from services.media_storage import LocalMediaStorage  # noqa: E402

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
JOB_A = "11111111-1111-4111-8111-111111111111"
JOB_B = "22222222-2222-4222-8222-222222222222"
JOB_C = "33333333-3333-4333-8333-333333333333"


class RetentionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = LocalMediaStorage(Path(self._tmp.name))

    def _job(self, job_id: str, megabytes: int = 1, age_hours: float = 24) -> Path:
        directory = self.storage.outputs_dir(job_id)
        (directory / "clip.mp4").write_bytes(b"\0" * megabytes * 1024 * 1024)
        stamp = (NOW - timedelta(hours=age_hours)).timestamp()
        for path in [self.storage.job_dir(job_id), directory, directory / "clip.mp4"]:
            import os
            os.utime(path, (stamp, stamp))
        return self.storage.job_dir(job_id)


class OrphanSweepTests(RetentionTestCase):
    def test_media_for_a_job_the_database_forgot_is_removed(self):
        self._job(JOB_A)
        with patch.object(cleanup, "get_job", return_value=None):
            result = cleanup.sweep_orphaned_jobs(storage=self.storage, dry_run=False, now=NOW)
        self.assertEqual(result.orphans, 1)
        self.assertFalse(self.storage.job_dir(JOB_A).exists())

    def test_media_for_a_live_job_is_left_alone(self):
        self._job(JOB_A)
        with patch.object(cleanup, "get_job", return_value=object()):
            result = cleanup.sweep_orphaned_jobs(storage=self.storage, dry_run=False, now=NOW)
        self.assertEqual(result.orphans, 0)
        self.assertTrue(self.storage.job_dir(JOB_A).exists())

    def test_a_job_still_being_written_is_not_swept(self):
        """A job mid-flight may have no database row yet. Deleting its source
        out from under ffmpeg would be a spectacular way to fail a render."""
        self._job(JOB_A, age_hours=0)
        with patch.object(cleanup, "get_job", return_value=None):
            result = cleanup.sweep_orphaned_jobs(
                storage=self.storage, dry_run=False, grace_hours=2, now=NOW
            )
        self.assertEqual(result.orphans, 0)
        self.assertTrue(self.storage.job_dir(JOB_A).exists())

    def test_recent_activity_deep_in_the_tree_counts(self):
        """A directory's own mtime does not move when a grandchild is written,
        so a busy job can look untouched."""
        self._job(JOB_A, age_hours=24)
        fresh = self.storage.outputs_dir(JOB_A) / "just_rendered.mp4"
        fresh.write_bytes(b"x")
        with patch.object(cleanup, "get_job", return_value=None):
            result = cleanup.sweep_orphaned_jobs(
                storage=self.storage, dry_run=False, grace_hours=2, now=NOW
            )
        self.assertEqual(result.orphans, 0)

    def test_dry_run_reports_without_deleting(self):
        self._job(JOB_A, megabytes=2)
        with patch.object(cleanup, "get_job", return_value=None):
            result = cleanup.sweep_orphaned_jobs(storage=self.storage, dry_run=True, now=NOW)
        self.assertEqual(result.orphans, 1)
        self.assertGreater(result.bytes_reclaimed, 0)
        self.assertTrue(self.storage.job_dir(JOB_A).exists())


class DiskBudgetTests(RetentionTestCase):
    def test_nothing_happens_under_budget(self):
        self._job(JOB_A, megabytes=1)
        result = cleanup.enforce_disk_budget(
            storage=self.storage, dry_run=False, max_bytes=100 * 1024**2, now=NOW
        )
        self.assertEqual(result.evicted_jobs, 0)

    def test_over_budget_evicts_the_least_recently_touched_first(self):
        self._job(JOB_A, megabytes=4, age_hours=100)   # oldest
        self._job(JOB_B, megabytes=4, age_hours=50)
        self._job(JOB_C, megabytes=4, age_hours=1)     # newest
        expired = []
        with patch.object(cleanup, "set_job_artifact_expiry",
                          side_effect=lambda job_id, when: expired.append(job_id)), \
             patch.object(cleanup, "cleanup_expired_artifacts",
                          return_value=cleanup.CleanupResult()), \
             patch.object(cleanup, "sweep_orphaned_jobs",
                          return_value=cleanup.CleanupResult()):
            cleanup.enforce_disk_budget(
                storage=self.storage, dry_run=False, max_bytes=9 * 1024**2, now=NOW
            )
        self.assertEqual(expired[0], JOB_A, "evicted a newer job before the oldest")
        self.assertNotIn(JOB_C, expired, "evicted the newest job unnecessarily")

    def test_it_stops_as_soon_as_it_is_under_budget(self):
        self._job(JOB_A, megabytes=4, age_hours=100)
        self._job(JOB_B, megabytes=4, age_hours=50)
        self._job(JOB_C, megabytes=4, age_hours=1)
        with patch.object(cleanup, "set_job_artifact_expiry"), \
             patch.object(cleanup, "cleanup_expired_artifacts",
                          return_value=cleanup.CleanupResult()), \
             patch.object(cleanup, "sweep_orphaned_jobs",
                          return_value=cleanup.CleanupResult()):
            result = cleanup.enforce_disk_budget(
                storage=self.storage, dry_run=False, max_bytes=9 * 1024**2, now=NOW
            )
        self.assertEqual(result.evicted_jobs, 1, "kept evicting past the budget")

    def test_eviction_expires_rows_rather_than_deleting_files_directly(self):
        """Files vanishing from under live database rows is how you get clips
        that 404 forever."""
        self._job(JOB_A, megabytes=4, age_hours=100)
        with patch.object(cleanup, "set_job_artifact_expiry") as expire, \
             patch.object(cleanup, "cleanup_expired_artifacts",
                          return_value=cleanup.CleanupResult()), \
             patch.object(cleanup, "sweep_orphaned_jobs",
                          return_value=cleanup.CleanupResult()):
            cleanup.enforce_disk_budget(
                storage=self.storage, dry_run=False, max_bytes=1024, now=NOW
            )
        expire.assert_called()
        self.assertTrue(self.storage.job_dir(JOB_A).exists(),
                        "budget enforcement deleted files itself")

    def test_dry_run_touches_nothing(self):
        self._job(JOB_A, megabytes=4, age_hours=100)
        with patch.object(cleanup, "set_job_artifact_expiry") as expire:
            result = cleanup.enforce_disk_budget(
                storage=self.storage, dry_run=True, max_bytes=1024, now=NOW
            )
        expire.assert_not_called()
        self.assertEqual(result.evicted_jobs, 1)


class PurgeTests(RetentionTestCase):
    def test_purge_removes_every_job_folder(self):
        self._job(JOB_A)
        self._job(JOB_B)
        result = cleanup.purge_all(storage=self.storage, dry_run=False)
        self.assertEqual(result.orphans, 2)
        self.assertEqual(list(self.storage.root.iterdir()), [])

    def test_purge_defaults_to_dry_run(self):
        """A command that empties the disk should not do so by accident."""
        self._job(JOB_A)
        cleanup.purge_all(storage=self.storage)
        self.assertTrue(self.storage.job_dir(JOB_A).exists())

    def test_purge_refuses_to_leave_its_own_root(self):
        outsider = Path(self._tmp.name).parent / "not-ours"
        outsider.mkdir(exist_ok=True)
        self.addCleanup(outsider.rmdir)
        with self.assertRaises(ValueError):
            self.storage.delete(outsider)


if __name__ == "__main__":
    unittest.main()
