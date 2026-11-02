"""Safely inspect or execute Clipmaker media cleanup.

Examples:
    python cleanup_storage.py --dry-run     # what a normal pass would remove
    python cleanup_storage.py --execute     # expired artifacts, orphans, disk budget
    python cleanup_storage.py --purge-all   # delete ALL job media (test data)
"""

import argparse

from services.cleanup import purge_all, run_maintenance


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Clipmaker media")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report without deleting files")
    mode.add_argument("--execute", action="store_true", help="Run the full maintenance pass")
    mode.add_argument(
        "--purge-all",
        action="store_true",
        help="Delete every job's media regardless of age. Test data only.",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt for --purge-all"
    )
    args = parser.parse_args()

    if args.purge_all:
        # Deliberately noisy: this ignores every TTL and takes the lot.
        preview = purge_all(dry_run=True)
        print(
            f"This will delete {preview.orphans} job folder(s), "
            f"{preview.bytes_reclaimed / (1024 ** 3):.2f} GB."
        )
        print("Database rows are left alone, so old jobs will show missing media.")
        if not args.yes and input("Type 'purge' to continue: ").strip() != "purge":
            print("Cancelled.")
            return
        result = purge_all(dry_run=False)
        print(f"Deleted: {result.bytes_reclaimed / (1024 ** 3):.2f} GB")
        print(f"Errors: {result.errors}")
        return

    result = run_maintenance(dry_run=not args.execute)
    verb = "Reclaimable" if result.dry_run else "Reclaimed"
    print(f"Expired artifacts: {result.artifacts}")
    print(f"Orphaned job folders: {result.orphans}")
    print(f"Jobs evicted for disk budget: {result.evicted_jobs}")
    print(f"{verb}: {result.bytes_reclaimed / (1024 * 1024):.2f} MB")
    print(f"Errors: {result.errors}")


if __name__ == "__main__":
    main()
