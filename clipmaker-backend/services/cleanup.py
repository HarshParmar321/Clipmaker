from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import CLEANUP_DRY_RUN, MAX_STORAGE_GB, ORPHAN_GRACE_HOURS
from services.job_store import (
    get_job,
    list_expired_artifacts,
    mark_artifact_deleted,
    set_job_artifact_expiry,
)
from services.media_storage import LocalMediaStorage, media_storage

logger = logging.getLogger("clipmaker.cleanup")


@dataclass
class CleanupResult:
    artifacts: int = 0
    bytes_reclaimed: int = 0
    errors: int = 0
    dry_run: bool = False
    orphans: int = 0
    evicted_jobs: int = 0


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


# ── Orphans ───────────────────────────────────────────────────────────

def _job_directories(storage: LocalMediaStorage) -> list[Path]:
    if not storage.root.exists():
        return []
    return [path for path in storage.root.iterdir() if path.is_dir()]


def _last_touched(path: Path) -> float:
    """Newest mtime anywhere under a directory.

    The directory's own mtime only moves when its immediate children change, so
    a job still writing clips into outputs/ can look untouched for an hour.
    """
    times = [path.stat().st_mtime]
    times.extend(item.stat().st_mtime for item in path.rglob("*"))
    return max(times)


def sweep_orphaned_jobs(
    *,
    storage: LocalMediaStorage = media_storage,
    dry_run: bool = CLEANUP_DRY_RUN,
    grace_hours: int = ORPHAN_GRACE_HOURS,
    now: datetime | None = None,
) -> CleanupResult:
    """Delete media for jobs the database no longer knows about.

    TTL cleanup works from artifact rows, so anything whose row never existed
    or has been removed is invisible to it and stays on disk forever. That
    covers crashed runs, jobs deleted from the dashboard, and a wiped table.
    """
    result = CleanupResult(dry_run=dry_run)
    moment = (now or datetime.now(timezone.utc)).timestamp()
    cutoff = moment - grace_hours * 3600

    for directory in _job_directories(storage):
        try:
            if _last_touched(directory) > cutoff:
                continue  # possibly still being written to
            if get_job(directory.name) is not None:
                continue  # a live job owns this
            bytes_removed = storage.delete(directory, dry_run=dry_run)
            result.orphans += 1
            result.bytes_reclaimed += bytes_removed
            logger.info(
                "Orphaned job media %s%s: %.1f MB",
                directory.name,
                " (dry-run)" if dry_run else " removed",
                bytes_removed / (1024 * 1024),
            )
        except Exception:
            result.errors += 1
            logger.exception("Could not sweep orphaned job media at %s", directory)
    return result


# ── Disk budget ───────────────────────────────────────────────────────

def enforce_disk_budget(
    *,
    storage: LocalMediaStorage = media_storage,
    dry_run: bool = CLEANUP_DRY_RUN,
    max_bytes: int | None = None,
    now: datetime | None = None,
) -> CleanupResult:
    """Evict least-recently-used jobs until storage is back under budget.

    Expiry alone cannot hold a ceiling: a burst of long videos fills the disk
    while every one of them is still hours from expiring. This evicts by
    setting a job's artifacts to expired and running the normal cleanup, so
    database rows stay consistent instead of files vanishing from under them.
    """
    result = CleanupResult(dry_run=dry_run)
    budget = int(MAX_STORAGE_GB * 1024**3) if max_bytes is None else max_bytes
    moment = now or datetime.now(timezone.utc)

    directories = _job_directories(storage)
    total = sum(storage.size(directory) for directory in directories)
    if total <= budget:
        return result

    logger.warning(
        "Media storage is %.2f GB, over the %.2f GB budget. Evicting oldest jobs.",
        total / 1024**3, budget / 1024**3,
    )

    # Oldest first. The job nobody has touched in longest is the one whose
    # clips someone is least likely to still be looking at.
    for directory in sorted(directories, key=_last_touched):
        if total <= budget:
            break
        try:
            size = storage.size(directory)
            if not dry_run:
                # Expire rather than delete: the next pass removes the files and
                # marks the rows, so nothing is left pointing at a missing file.
                set_job_artifact_expiry(directory.name, moment - timedelta(seconds=1))
            result.evicted_jobs += 1
            total -= size
            logger.info(
                "Evicting job %s to reclaim %.1f MB%s",
                directory.name, size / (1024 * 1024), " (dry-run)" if dry_run else "",
            )
        except Exception:
            result.errors += 1
            logger.exception("Could not evict job media at %s", directory)

    if result.evicted_jobs and not dry_run:
        expired = cleanup_expired_artifacts(storage=storage, dry_run=False, now=moment)
        result.artifacts += expired.artifacts
        result.bytes_reclaimed += expired.bytes_reclaimed
        result.errors += expired.errors
        # Evicted jobs whose rows were already gone leave the directory behind.
        swept = sweep_orphaned_jobs(storage=storage, dry_run=False, grace_hours=0, now=moment)
        result.orphans += swept.orphans
        result.bytes_reclaimed += swept.bytes_reclaimed
    return result


def run_maintenance(
    *,
    storage: LocalMediaStorage = media_storage,
    dry_run: bool = CLEANUP_DRY_RUN,
    now: datetime | None = None,
) -> CleanupResult:
    """The full pass: expired artifacts, then orphans, then the disk ceiling."""
    moment = now or datetime.now(timezone.utc)
    total = CleanupResult(dry_run=dry_run)
    for step in (
        cleanup_expired_artifacts(storage=storage, dry_run=dry_run, now=moment),
        sweep_orphaned_jobs(storage=storage, dry_run=dry_run, now=moment),
        enforce_disk_budget(storage=storage, dry_run=dry_run, now=moment),
    ):
        total.artifacts += step.artifacts
        total.orphans += step.orphans
        total.evicted_jobs += step.evicted_jobs
        total.bytes_reclaimed += step.bytes_reclaimed
        total.errors += step.errors
    logger.info(
        "Maintenance%s: %d artifact(s), %d orphan(s), %d evicted job(s), %.2f MB, %d error(s)",
        " dry-run" if dry_run else "",
        total.artifacts, total.orphans, total.evicted_jobs,
        total.bytes_reclaimed / (1024 * 1024), total.errors,
    )
    return total


def purge_all(*, storage: LocalMediaStorage = media_storage, dry_run: bool = True) -> CleanupResult:
    """Delete every job's media. For clearing test data, not for production.

    Database rows are left alone deliberately — this is a disk operation, and
    silently dropping someone's job history is not something a cleanup command
    should do without being asked.
    """
    result = CleanupResult(dry_run=dry_run)
    for directory in _job_directories(storage):
        try:
            result.bytes_reclaimed += storage.delete(directory, dry_run=dry_run)
            result.orphans += 1
        except Exception:
            result.errors += 1
            logger.exception("Could not purge %s", directory)
    logger.warning(
        "Purge%s: %d job folder(s), %.2f MB",
        " dry-run" if dry_run else "",
        result.orphans, result.bytes_reclaimed / (1024 * 1024),
    )
    return result
