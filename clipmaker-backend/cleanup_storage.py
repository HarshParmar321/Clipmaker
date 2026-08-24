"""Safely inspect or execute artifact cleanup.

Examples:
    python cleanup_storage.py --dry-run
    python cleanup_storage.py --execute
"""

import argparse

from services.cleanup import cleanup_expired_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean expired Clipmaker media artifacts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report without deleting files")
    mode.add_argument("--execute", action="store_true", help="Delete expired files")
    args = parser.parse_args()

    result = cleanup_expired_artifacts(dry_run=not args.execute)
    verb = "Reclaimable" if result.dry_run else "Reclaimed"
    print(f"Artifacts: {result.artifacts}")
    print(f"{verb}: {result.bytes_reclaimed / (1024 * 1024):.2f} MB")
    print(f"Errors: {result.errors}")


if __name__ == "__main__":
    main()
