import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.job_store import Clip, Job, MemoryJobRepository
from services.render_settings import OutputFormat
from services.media_storage import LocalMediaStorage
from routers.jobs import _editor_preview_id, _parse_byte_range, _serialize_clip


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.repository_patch = patch(
            "services.job_store.repository", MemoryJobRepository()
        )
        self.repository_patch.start()

    def tearDown(self):
        self.repository_patch.stop()

    def test_serialized_clip_exposes_quality_evidence(self):
        job = Job(source_type="upload", source_value="source.mp4")
        clip = Clip(
            id="11111111-1111-4111-8111-111111111111",
            title="Founder lesson",
            start=2,
            end=12,
            score=84,
            score_breakdown={
                "hook": 86,
                "relevance": 91,
                "completeness": 82,
                "shareability": 75,
            },
        )
        payload = _serialize_clip(job, clip)
        self.assertEqual(payload["score_breakdown"]["relevance"], 91)
        self.assertTrue(payload["reframe_analysis_url"].endswith("/reframe-analysis"))

    def test_rejects_non_youtube_url(self):
        client = TestClient(app)
        response = client.post("/api/jobs", json={"url": "http://127.0.0.1/private"})
        self.assertEqual(response.status_code, 422)

    def test_upload_uses_generated_job_path(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalMediaStorage(Path(temp))
            with (
                patch("routers.jobs.media_storage", storage),
                patch("routers.jobs.run_pipeline") as run_pipeline,
            ):
                client = TestClient(app)
                response = client.post(
                    "/api/jobs/upload",
                    files={"file": ("../../unsafe.mp4", io.BytesIO(b"video"), "video/mp4")},
                )
            self.assertEqual(response.status_code, 200)
            job_id = response.json()["job_id"]
            self.assertEqual(
                (Path(temp) / job_id / "source" / "original.mp4").read_bytes(),
                b"video",
            )
            run_pipeline.assert_called_once()

    def test_url_job_accepts_optional_creative_and_render_settings(self):
        with patch("routers.jobs.run_pipeline") as run_pipeline:
            client = TestClient(app)
            response = client.post(
                "/api/jobs",
                json={
                    "url": "https://www.youtube.com/watch?v=example123",
                    "creative_prompt": "Find practical founder advice",
                    "output_format": "landscape",
                    "caption_style": "clean",
                    "target_clip_count": 3,
                    "min_clip_duration": 15,
                    "max_clip_duration": 30,
                },
            )
        self.assertEqual(response.status_code, 200)
        queued_job = run_pipeline.call_args.args[0]
        self.assertEqual(queued_job.creative_prompt, "Find practical founder advice")
        self.assertEqual(queued_job.output_format.value, "landscape")
        self.assertEqual(queued_job.caption_style.value, "clean")
        self.assertEqual(queued_job.target_clip_count, 3)
        self.assertEqual(queued_job.min_clip_duration, 15)
        self.assertEqual(queued_job.max_clip_duration, 30)

    def test_rejects_invalid_clip_duration_range(self):
        client = TestClient(app)
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/example123",
                "min_clip_duration": 60,
                "max_clip_duration": 30,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_editor_preview_id_is_stable_and_source_based(self):
        clip_id = "11111111-1111-1111-1111-111111111111"
        first = _editor_preview_id(clip_id)
        second = _editor_preview_id(clip_id)
        self.assertEqual(first, second)

    def test_media_byte_ranges_support_seeking(self):
        self.assertEqual(_parse_byte_range("bytes=100-199", 1000), (100, 199))
        self.assertEqual(_parse_byte_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(_parse_byte_range("bytes=-100", 1000), (900, 999))
        with self.assertRaises(ValueError):
            _parse_byte_range("bytes=1000-1200", 1000)


if __name__ == "__main__":
    unittest.main()
