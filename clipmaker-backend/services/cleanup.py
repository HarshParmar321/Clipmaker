from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import CLEANUP_DRY_RUN
from services.job_store import list_expired_artifacts, mark_artifact_deleted
from services.media_storage import LocalMediaStorage, media_storage

logger = logging.getLogger("clipmaker.cleanup")


@dataclass
class CleanupResult:
    artifacts: int = 0
    bytes_reclaimed: int = 0
    errors: int = 0
    dry_run: bool = False


def cleanup_expired_artifacts(
    *,
    storage: LocalMediaStorage = media_storage,
    dry_run: bool = CLEANUP_DRY_RUN,
    now: datetime | None = None,
) -> CleanupResult:
    result = CleanupResult(dry_run=dry_run)
    try:
        artifacts = list_expired_artifacts(now or datetime.now(timezone.utc))
    except Exception:
        result.errors += 1
        logger.exception("Could not load expired artifacts; cleanup will retry later")
        return result

    for artifact in artifacts:
        try:
            bytes_removed = storage.delete(Path(artifact.local_path), dry_run=dry_run)
            result.artifacts += 1
            result.bytes_reclaimed += bytes_removed
            if not dry_run:
                mark_artifact_deleted(artifact.id)
        except Exception:
            result.errors += 1
            logger.exception("Could not clean artifact %s at %s", artifact.id, artifact.local_path)

    logger.info(
        "Cleanup%s: %d artifact(s), %.2f MB reclaimable/reclaimed, %d error(s)",
        " dry-run" if dry_run else "",
        result.artifacts,
        result.bytes_reclaimed / (1024 * 1024),
        result.errors,
    )
    return result
