"""
Async Directory Scanner with SQLite Storage

Scans a directory to a specified depth and stores file metadata in a SQLite database
using sqlite-utils. Uses asyncio for maximum parallel I/O operations.

Usage:
    boss-file-utils /path/to/scan --depth 3 --db myfiles.db --workers 50
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import sqlite_utils
from sqlite_utils.db import Table

T = TypeVar("T")

from boss_file_utils.logging_config import configure_logging, get_logger, stop_queue_listener
from boss_file_utils.telemetry import (
    get_tracer,
    instrument_all,
    record_exception_on_span,
    setup_telemetry,
)


@dataclass
class FileMetadata:
    """Represents metadata for a single file."""

    name: str
    path: str
    parent_directory: str
    extension: str
    size_bytes: int
    modified_timestamp: float
    modified_datetime: str
    created_timestamp: float
    created_datetime: str
    is_directory: bool
    is_symlink: bool
    depth: int


class AsyncDirectoryScanner:
    """
    Asynchronous directory scanner that collects file metadata
    and stores it in a SQLite database.
    """

    def __init__(
        self,
        root_path: str,
        max_depth: int | float,
        db_path: str = "file_index.db",
        max_workers: int = 50,
        batch_size: int = 500,
    ):
        self.root_path = Path(root_path).resolve()
        self.max_depth = max_depth
        self.db_path = db_path
        self.max_workers = max_workers
        self.batch_size = batch_size
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._semaphore = asyncio.Semaphore(max_workers)
        self._stats = {"files": 0, "directories": 0, "errors": 0, "symlinks": 0}
        self._log = get_logger(__name__)

    async def _run_in_executor(self, func: Callable[..., T], *args: Any) -> T:
        """Run a blocking function in the thread pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    def _get_entry_metadata(self, entry: os.DirEntry[str], depth: int) -> FileMetadata | None:
        """
        Extract metadata from a directory entry.
        Returns None if the entry cannot be accessed.
        """
        try:
            stat_info = entry.stat(follow_symlinks=False)
            path = Path(entry.path)

            return FileMetadata(
                name=entry.name,
                path=str(path),
                parent_directory=str(path.parent),
                extension=path.suffix.lower() if path.suffix else "",
                size_bytes=stat_info.st_size,
                modified_timestamp=stat_info.st_mtime,
                modified_datetime=datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
                created_timestamp=getattr(stat_info, "st_birthtime", stat_info.st_ctime),
                created_datetime=datetime.fromtimestamp(
                    getattr(stat_info, "st_birthtime", stat_info.st_ctime)
                ).isoformat(),
                is_directory=entry.is_dir(follow_symlinks=False),
                is_symlink=entry.is_symlink(),
                depth=depth,
            )
        except (OSError, PermissionError) as e:
            self._log.warning("Cannot access path", path=entry.path, error=str(e))
            return None

    def _scan_directory_sync(self, dir_path: Path) -> list[os.DirEntry[str]]:
        """Synchronously scan a directory and return entries."""
        try:
            with os.scandir(dir_path) as entries:
                return list(entries)
        except (OSError, PermissionError) as e:
            self._log.warning("Cannot scan directory", dir_path=str(dir_path), error=str(e))
            return []

    async def _scan_directory(self, dir_path: Path) -> list[os.DirEntry[str]]:
        """Asynchronously scan a directory using the thread pool."""
        async with self._semaphore:
            return await self._run_in_executor(self._scan_directory_sync, dir_path)

    async def _collect_metadata_batch(
        self, entries: list[os.DirEntry[str]], depth: int
    ) -> list[FileMetadata]:
        """Collect metadata for a batch of entries asynchronously."""
        tracer = get_tracer()

        with tracer.start_as_current_span("collect_metadata_batch") as span:
            span.set_attribute("batch.size", len(entries))
            span.set_attribute("batch.depth", depth)

            tasks = []
            for entry in entries:
                task = self._run_in_executor(self._get_entry_metadata, entry, depth)
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            metadata_list: list[FileMetadata] = []
            for result in results:
                if isinstance(result, BaseException):
                    self._stats["errors"] += 1
                elif isinstance(result, FileMetadata):
                    metadata_list.append(result)
                    if result.is_directory:
                        self._stats["directories"] += 1
                    if result.is_symlink:
                        self._stats["symlinks"] += 1
                    if not result.is_directory:
                        self._stats["files"] += 1

            span.set_attribute("batch.collected", len(metadata_list))
            return metadata_list

    async def scan(self) -> AsyncIterator[list[FileMetadata]]:
        """
        Scan the directory tree asynchronously and yield batches of metadata.
        Uses BFS with async processing at each level.
        """
        if not self.root_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.root_path}")

        if not self.root_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {self.root_path}")

        # Queue of (path, depth) tuples to process
        queue: asyncio.Queue[tuple[Path, int]] = asyncio.Queue()
        await queue.put((self.root_path, 0))

        pending_batch: list[FileMetadata] = []

        while not queue.empty():
            # Collect all items at current level for parallel processing
            current_level: list[tuple[Path, int]] = []
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                    current_level.append(item)
                except asyncio.QueueEmpty:
                    break

            # Process directories in parallel
            scan_tasks = [self._scan_directory(path) for path, _ in current_level]
            scan_results = await asyncio.gather(*scan_tasks)

            # Process entries for each directory
            for (_dir_path, depth), entries in zip(current_level, scan_results, strict=True):
                if not entries:
                    continue

                # Collect metadata for entries
                metadata_batch = await self._collect_metadata_batch(entries, depth)
                pending_batch.extend(metadata_batch)

                # Yield batch if it's large enough
                if len(pending_batch) >= self.batch_size:
                    yield pending_batch
                    pending_batch = []

                # Queue subdirectories if we haven't reached max depth
                if depth < self.max_depth:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                                await queue.put((Path(entry.path), depth + 1))
                        except (OSError, PermissionError):
                            self._stats["errors"] += 1

        # Yield remaining items
        if pending_batch:
            yield pending_batch

    def _setup_database(self, db: sqlite_utils.Database) -> Table:
        """Set up the database table with proper schema and indexes."""
        table: Table = db.table("files")  # pyright: ignore[reportAssignmentType]

        # Create table if it doesn't exist
        if "files" not in db.table_names():
            db.execute("""
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL,
                    parent_directory TEXT NOT NULL,
                    extension TEXT,
                    size_bytes INTEGER,
                    modified_timestamp REAL,
                    modified_datetime TEXT,
                    created_timestamp REAL,
                    created_datetime TEXT,
                    is_directory BOOLEAN,
                    is_symlink BOOLEAN,
                    depth INTEGER,
                    indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for common queries
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent_directory)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_size ON files(size_bytes)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_timestamp)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_files_is_directory ON files(is_directory)")

        return table

    async def run(self) -> dict[str, Any]:
        """
        Run the scanner and store results in the database.
        Returns statistics about the scan.
        """
        tracer = get_tracer()

        with tracer.start_as_current_span("directory_scan") as span:
            span.set_attribute("scan.root_path", str(self.root_path))
            span.set_attribute("scan.max_depth", self.max_depth)
            span.set_attribute("scan.db_path", self.db_path)

            start_time = time.time()

            # Create/open database
            db = sqlite_utils.Database(self.db_path)
            table = self._setup_database(db)

            total_inserted = 0

            try:
                async for batch in self.scan():
                    # Convert dataclass instances to dicts for insertion
                    records = [
                        {
                            "name": m.name,
                            "path": m.path,
                            "parent_directory": m.parent_directory,
                            "extension": m.extension,
                            "size_bytes": m.size_bytes,
                            "modified_timestamp": m.modified_timestamp,
                            "modified_datetime": m.modified_datetime,
                            "created_timestamp": m.created_timestamp,
                            "created_datetime": m.created_datetime,
                            "is_directory": m.is_directory,
                            "is_symlink": m.is_symlink,
                            "depth": m.depth,
                        }
                        for m in batch
                    ]

                    # Use insert_all with replace for handling re-scanning
                    with tracer.start_as_current_span("database_insert") as insert_span:
                        insert_span.set_attribute("insert.count", len(records))
                        table.insert_all(records, alter=True, replace=True)  # pyright: ignore[reportArgumentType]
                    total_inserted += len(records)

                    self._log.debug("Batch inserted", total=total_inserted, batch_size=len(records))

            finally:
                self._executor.shutdown(wait=False)

            elapsed = time.time() - start_time

            span.set_attribute("scan.total_items", total_inserted)
            span.set_attribute("scan.elapsed_seconds", round(elapsed, 2))

            return {
                "root_path": str(self.root_path),
                "database": self.db_path,
                "max_depth": self.max_depth,
                "total_items": total_inserted,
                "files": self._stats["files"],
                "directories": self._stats["directories"],
                "symlinks": self._stats["symlinks"],
                "errors": self._stats["errors"],
                "elapsed_seconds": round(elapsed, 2),
                "items_per_second": round(total_inserted / elapsed, 2) if elapsed > 0 else 0,
            }


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Async directory scanner with SQLite storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scan current directory, depth 2, default database
    python dir_scanner.py . --depth 2

    # Scan with custom database name
    python dir_scanner.py /home/user --depth 5 --db my_files.db

    # Scan with more workers for faster I/O
    python dir_scanner.py /data --depth 10 --workers 100

After scanning, query with sqlite-utils CLI:
    sqlite-utils tables file_index.db
    sqlite-utils rows file_index.db files --limit 10
    sqlite-utils "SELECT extension, COUNT(*) FROM files GROUP BY extension" file_index.db
        """,
    )

    parser.add_argument(
        "directory",
        type=str,
        help="Root directory to scan",
    )

    parser.add_argument(
        "-d",
        "--depth",
        type=int,
        default=3,
        help="Maximum depth to scan (default: 3, use -1 for unlimited)",
    )

    parser.add_argument(
        "--db",
        type=str,
        default="file_index.db",
        help="SQLite database file path (default: file_index.db)",
    )

    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=50,
        help="Maximum concurrent workers (default: 50)",
    )

    parser.add_argument(
        "-b",
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for database inserts (default: 500)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output logs in JSON format (for production)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity level (default: INFO)",
    )

    return parser.parse_args()


async def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Initialize telemetry, instrumentation, and logging
    setup_telemetry(service_name="boss-file-utils")
    instrument_all()
    configure_logging(json_output=args.json, log_level=args.log_level)

    log = get_logger(__name__)

    # Handle unlimited depth
    max_depth = args.depth if args.depth >= 0 else float("inf")

    scanner = AsyncDirectoryScanner(
        root_path=args.directory,
        max_depth=max_depth,
        db_path=args.db,
        max_workers=args.workers,
        batch_size=args.batch_size,
    )

    log.info(
        "Starting scan",
        directory=args.directory,
        max_depth=args.depth if args.depth >= 0 else "unlimited",
        db=args.db,
        workers=args.workers,
    )

    try:
        stats = await scanner.run()

        log.info(
            "Scan complete",
            total_items=stats["total_items"],
            files=stats["files"],
            directories=stats["directories"],
            symlinks=stats["symlinks"],
            errors=stats["errors"],
            elapsed_seconds=stats["elapsed_seconds"],
            items_per_second=stats["items_per_second"],
            database=stats["database"],
        )

    except FileNotFoundError as e:
        record_exception_on_span(e)
        log.error("Scan failed", error=str(e))
        sys.exit(1)
    except NotADirectoryError as e:
        record_exception_on_span(e)
        log.error("Scan failed", error=str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        log.warning("Scan interrupted by user")
        sys.exit(130)
    finally:
        stop_queue_listener()


if __name__ == "__main__":
    asyncio.run(main())
