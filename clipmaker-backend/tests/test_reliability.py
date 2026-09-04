import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from main import app
from services.job_store import (
    Job,
    JobStatus,
    MemoryJobRepository,
)
from services.pipeline import (
    PipelineCancelled,
    _checkpoint,
    _retry_operation,
)
from services.render_settings import CaptionStyle, OutputFormat


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryJobRepository()
        self.repository_patch = patch("services.job_store.repository", self.repository)
        self.repository_patch.start()

    def tearDown(self):
        self.repository_patch.stop()

    def create_job(self):
        return self.repository.create_job(
            "url",
            "https://youtu.be/example123",
            None,
            None,
            OutputFormat.VERTICAL,
            CaptionStyle.BOLD,
            5,
            20,
            60,
        )

    def test_status_response_exposes_persisted_progress(self):
        job = self.create_job()
        job.status = JobStatus.TRANSCRIBING
        job.current_stage = JobStatus.TRANSCRIBING.value
        job.progress_percent = 34
        job.progress_message = "Transcribing speech…"
        job.heartbeat_at = datetime.now(timezone.utc)
        self.repository.update_job(job)

        response = TestClient(app).get(f"/api/jobs/{job.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["progress"]["percent"], 34)
        self.assertEqual(payload["progress"]["current_stage"], "transcribing")
        self.assertTrue(payload["can_cancel"])

    def test_cancel_records_a_cooperative_request(self):
        job = self.create_job()
        job.status = JobStatus.DETECTING_HIGHLIGHTS
        job.current_stage = JobStatus.DETECTING_HIGHLIGHTS.value
        self.repository.update_job(job)

        response = TestClient(app).post(f"/api/jobs/{job.id}/cancel")

        self.assertEqual(response.status_code, 202)
        saved = self.repository.get_job(job.id)
        self.assertIsNotNone(saved.cancel_requested_at)
        self.assertIn("Cancellation requested", saved.progress_message)

    def test_retry_resets_failure_and_reuses_same_job(self):
        job = self.create_job()
        job.status = JobStatus.FAILED
        job.current_stage = JobStatus.TRANSCRIBING.value
        job.failed_stage = JobStatus.TRANSCRIBING.value
        job.error = "Temporary provider failure"
        job.retryable = True
        self.repository.update_job(job)

        with patch("routers.jobs.run_pipeline") as run_pipeline:
            response = TestClient(app).post(f"/api/jobs/{job.id}/retry")

        self.assertEqual(response.status_code, 202)
        saved = self.repository.get_job(job.id)
        self.assertEqual(saved.status, JobStatus.QUEUED)
        self.assertIsNone(saved.error)
        self.assertIsNone(saved.failed_stage)
        run_pipeline.assert_called_once()
        self.assertEqual(run_pipeline.call_args.args[0].id, job.id)

    def test_retry_rejects_a_permanent_failure(self):
        job = self.create_job()
        job.status = JobStatus.FAILED
        job.failed_stage = JobStatus.DOWNLOADING.value
        job.error = "Video is too long"
        job.retryable = False
        self.repository.update_job(job)

        response = TestClient(app).post(f"/api/jobs/{job.id}/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Video is too long")

    def test_zero_highlight_failure_can_retry_from_saved_transcript(self):
        job = self.create_job()
        job.status = JobStatus.FAILED
        job.failed_stage = JobStatus.DETECTING_HIGHLIGHTS.value
        job.error = "No strong moments passed validation"
        job.retryable = False
        job.transcript = [{"word": "Complete.", "start": 0, "end": 8}]
        self.repository.update_job(job)

        status = TestClient(app).get(f"/api/jobs/{job.id}")
        self.assertTrue(status.json()["can_retry"])

        with patch("routers.jobs.run_pipeline") as run_pipeline:
            response = TestClient(app).post(f"/api/jobs/{job.id}/retry")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.repository.get_job(job.id).status, JobStatus.QUEUED)
        run_pipeline.assert_called_once()

    def test_restart_recovery_marks_only_preexisting_active_jobs(self):
        cutoff = datetime.now(timezone.utc)
        interrupted = self.create_job()
        interrupted.created_at = cutoff - timedelta(minutes=5)
        interrupted.status = JobStatus.BURNING_CAPTIONS
        interrupted.current_stage = JobStatus.BURNING_CAPTIONS.value
        self.repository.update_job(interrupted)

        new_job = self.create_job()
        new_job.created_at = cutoff + timedelta(seconds=1)
        self.repository.update_job(new_job)

        recovered = self.repository.recover_interrupted_jobs(cutoff)

        self.assertEqual(recovered, 1)
        saved = self.repository.get_job(interrupted.id)
        self.assertEqual(saved.status, JobStatus.FAILED)
        self.assertEqual(saved.failed_stage, JobStatus.BURNING_CAPTIONS.value)
        self.assertTrue(saved.retryable)
        self.assertEqual(self.repository.get_job(new_job.id).status, JobStatus.QUEUED)

    def test_checkpoint_honors_a_cancel_request(self):
        job = Job(source_type="url", source_value="https://youtu.be/example123")
        cancelled = job.model_copy(deep=True)
        cancelled.cancel_requested_at = datetime.now(timezone.utc)

        with patch("services.pipeline.get_job", return_value=cancelled):
            with self.assertRaises(PipelineCancelled):
                _checkpoint(job, JobStatus.TRANSCRIBING, 30, "Transcribing…")

    def test_non_interruptible_checkpoint_preserves_cancel_request(self):
        job = Job(source_type="url", source_value="https://youtu.be/example123")
        cancelled = job.model_copy(deep=True)
        cancelled.cancel_requested_at = datetime.now(timezone.utc)

        with (
            patch("services.pipeline.get_job", return_value=cancelled),
            patch("services.pipeline.update_job") as update_job,
        ):
            _checkpoint(
                job,
                JobStatus.BURNING_CAPTIONS,
                80,
                "Finishing current render…",
                check_cancel=False,
            )

        self.assertEqual(job.cancel_requested_at, cancelled.cancel_requested_at)
        update_job.assert_called_once_with(job)

    @patch("services.pipeline.time.sleep")
    @patch("services.pipeline.update_job")
    @patch("services.pipeline.get_job")
    def test_temporary_provider_failure_retries(self, get_job, update_job, sleep):
        job = Job(source_type="url", source_value="https://youtu.be/example123")
        get_job.return_value = job
        operation = Mock(
            side_effect=[httpx.ConnectError("temporary connection failure"), "ok"]
        )

        result = _retry_operation(
            job,
            operation,
            status=JobStatus.TRANSCRIBING,
            progress_percent=32,
            message="transcription",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(1.5)
        update_job.assert_called_once()

    @patch("services.pipeline.time.sleep")
    @patch("services.pipeline.update_job")
    @patch("services.pipeline.get_job")
    def test_json_generation_failure_retries(self, get_job, update_job, sleep):
        job = Job(source_type="url", source_value="https://youtu.be/example123")
        get_job.return_value = job
        validation_error = RuntimeError(
            "json_validate_failed: Failed to validate JSON. Please adjust your prompt."
        )
        validation_error.status_code = 400
        operation = Mock(side_effect=[validation_error, "ok"])

        result = _retry_operation(
            job,
            operation,
            status=JobStatus.DETECTING_HIGHLIGHTS,
            progress_percent=58,
            message="highlight selection",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(1.5)
        update_job.assert_called_once()


if __name__ == "__main__":
    unittest.main()
