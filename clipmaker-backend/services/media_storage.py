"""Safe, job-scoped local media storage.

The rest of the application asks this service for paths instead of constructing
paths itself. A future cloud backend can implement the same public operations
without changing the video pipeline.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from config import JOBS_DIR, MAX_UPLOAD_BYTES


ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


class UploadTooLargeError(ValueError):
    pass


class UnsupportedVideoError(ValueError):
    pass


class LocalMediaStorage:
    def __init__(self, root: Path = JOBS_DIR):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_uuid(value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Invalid job or clip identifier") from exc

    def job_dir(self, job_id: str) -> Path:
        return self.root / self._safe_uuid(job_id)

    def source_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "source"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def temp_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "temp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def outputs_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def thumbnails_dir(self, job_id: str, clip_id: str) -> Path:
        path = self.job_dir(job_id) / "thumbnails" / self._safe_uuid(clip_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def source_path(self, job_id: str, original_filename: str | None) -> Path:
        suffix = Path(original_filename or "video.mp4").suffix.lower()
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            raise UnsupportedVideoError(
                "Unsupported video format. Upload MP4, MOV, M4V, WebM, MKV, or AVI."
            )
        return self.source_dir(job_id) / f"original{suffix}"

    def audio_path(self, job_id: str) -> Path:
        return self.temp_dir(job_id) / "audio.mp3"

    def subtitle_path(self, job_id: str, artifact_id: str) -> Path:
        return self.temp_dir(job_id) / f"{self._safe_uuid(artifact_id)}.ass"

    def output_path(self, job_id: str, artifact_id: str) -> Path:
        return self.outputs_dir(job_id) / f"{self._safe_uuid(artifact_id)}.mp4"

    def save_upload(
        self,
        job_id: str,
        original_filename: str | None,
        stream: BinaryIO,
        max_bytes: int = MAX_UPLOAD_BYTES,
    ) -> tuple[Path, int]:
        destination = self.source_path(job_id, original_filename)
        total = 0
        try:
            with destination.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise UploadTooLargeError(
                            f"Upload exceeds the {max_bytes // (1024 * 1024)} MB limit."
                        )
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            self._remove_empty_parents(destination.parent)
            raise
        return destination, total

    def size(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    def delete(self, path: Path, dry_run: bool = False) -> int:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Refusing to delete a path outside media storage")
        bytes_removed = self.size(resolved)
        if dry_run or not resolved.exists():
            return bytes_removed
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
        self._remove_empty_parents(resolved.parent)
        return bytes_removed

    def clear_temp(self, job_id: str, dry_run: bool = False) -> int:
        return self.delete(self.job_dir(job_id) / "temp", dry_run=dry_run)

    def _remove_empty_parents(self, start: Path) -> None:
        current = start
        while current != self.root and self.root in current.parents:
            try:
                current.rmdir()
            except (FileNotFoundError, OSError):
                break
            current = current.parent


media_storage = LocalMediaStorage()
