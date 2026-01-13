"""Tests for the CLI module - AsyncDirectoryScanner and argument parsing."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from boss_file_utils.cli import (
    AsyncDirectoryScanner,
    FileMetadata,
    parse_args,
)

if TYPE_CHECKING:
    from collections.abc import Generator


# ============================================================================
# FileMetadata Tests
# ============================================================================


class TestFileMetadata:
    """Tests for the FileMetadata dataclass."""

    def test_create_file_metadata(self) -> None:
        """FileMetadata can be instantiated with all required fields."""
        metadata = FileMetadata(
            name="test.txt",
            path="/tmp/test.txt",
            parent_directory="/tmp",
            extension=".txt",
            size_bytes=1024,
            modified_timestamp=1700000000.0,
            modified_datetime="2023-11-14T22:13:20",
            created_timestamp=1699000000.0,
            created_datetime="2023-11-03T08:26:40",
            is_directory=False,
            is_symlink=False,
            depth=1,
        )

        assert metadata.name == "test.txt"
        assert metadata.path == "/tmp/test.txt"
        assert metadata.parent_directory == "/tmp"
        assert metadata.extension == ".txt"
        assert metadata.size_bytes == 1024
        assert metadata.is_directory is False
        assert metadata.is_symlink is False
        assert metadata.depth == 1

    def test_file_metadata_directory(self) -> None:
        """FileMetadata correctly represents a directory."""
        metadata = FileMetadata(
            name="subdir",
            path="/tmp/subdir",
            parent_directory="/tmp",
            extension="",
            size_bytes=4096,
            modified_timestamp=1700000000.0,
            modified_datetime="2023-11-14T22:13:20",
            created_timestamp=1699000000.0,
            created_datetime="2023-11-03T08:26:40",
            is_directory=True,
            is_symlink=False,
            depth=0,
        )

        assert metadata.is_directory is True
        assert metadata.extension == ""

    def test_file_metadata_symlink(self) -> None:
        """FileMetadata correctly represents a symlink."""
        metadata = FileMetadata(
            name="link",
            path="/tmp/link",
            parent_directory="/tmp",
            extension="",
            size_bytes=8,
            modified_timestamp=1700000000.0,
            modified_datetime="2023-11-14T22:13:20",
            created_timestamp=1699000000.0,
            created_datetime="2023-11-03T08:26:40",
            is_directory=False,
            is_symlink=True,
            depth=2,
        )

        assert metadata.is_symlink is True

    def test_file_metadata_to_dict(self) -> None:
        """FileMetadata can be converted to a dictionary."""
        metadata = FileMetadata(
            name="test.py",
            path="/project/test.py",
            parent_directory="/project",
            extension=".py",
            size_bytes=512,
            modified_timestamp=1700000000.0,
            modified_datetime="2023-11-14T22:13:20",
            created_timestamp=1699000000.0,
            created_datetime="2023-11-03T08:26:40",
            is_directory=False,
            is_symlink=False,
            depth=1,
        )

        data = asdict(metadata)
        assert isinstance(data, dict)
        assert data["name"] == "test.py"
        assert data["extension"] == ".py"

    def test_file_metadata_empty_extension(self) -> None:
        """FileMetadata handles files without extensions."""
        metadata = FileMetadata(
            name="Makefile",
            path="/project/Makefile",
            parent_directory="/project",
            extension="",
            size_bytes=256,
            modified_timestamp=1700000000.0,
            modified_datetime="2023-11-14T22:13:20",
            created_timestamp=1699000000.0,
            created_datetime="2023-11-03T08:26:40",
            is_directory=False,
            is_symlink=False,
            depth=0,
        )

        assert metadata.extension == ""
        assert metadata.name == "Makefile"


# ============================================================================
# AsyncDirectoryScanner Tests
# ============================================================================


class TestAsyncDirectoryScannerInit:
    """Tests for AsyncDirectoryScanner initialization."""

    def test_init_with_defaults(self, tmp_path: Path) -> None:
        """Scanner initializes with default values."""
        scanner = AsyncDirectoryScanner(str(tmp_path), max_depth=3)

        assert scanner.root_path == tmp_path.resolve()
        assert scanner.max_depth == 3
        assert scanner.db_path == "file_index.db"
        assert scanner.max_workers == 50
        assert scanner.batch_size == 500

    def test_init_with_custom_values(self, tmp_path: Path) -> None:
        """Scanner initializes with custom values."""
        scanner = AsyncDirectoryScanner(
            str(tmp_path),
            max_depth=10,
            db_path="custom.db",
            max_workers=100,
            batch_size=1000,
        )

        assert scanner.max_depth == 10
        assert scanner.db_path == "custom.db"
        assert scanner.max_workers == 100
        assert scanner.batch_size == 1000

    def test_init_with_infinite_depth(self, tmp_path: Path) -> None:
        """Scanner accepts infinite depth."""
        scanner = AsyncDirectoryScanner(str(tmp_path), max_depth=float("inf"))

        assert scanner.max_depth == float("inf")

    def test_init_resolves_path(self) -> None:
        """Scanner resolves the root path to absolute."""
        relative_path = "."
        scanner = AsyncDirectoryScanner(relative_path, max_depth=1)

        assert scanner.root_path.is_absolute()

    def test_init_expands_tilde_path(self) -> None:
        """Scanner expands tilde paths to user home directory."""
        tilde_path = "~/test_dir"
        scanner = AsyncDirectoryScanner(tilde_path, max_depth=1)

        # Should expand to home directory
        expected = Path.home() / "test_dir"
        assert scanner.root_path == expected
        assert scanner.root_path.is_absolute()
        assert "~" not in str(scanner.root_path)

    def test_init_creates_stats(self, tmp_path: Path) -> None:
        """Scanner initializes stats dictionary."""
        scanner = AsyncDirectoryScanner(str(tmp_path), max_depth=1)

        assert scanner._stats == {"files": 0, "directories": 0, "errors": 0, "symlinks": 0}


class TestAsyncDirectoryScannerMetadata:
    """Tests for metadata extraction methods."""

    @pytest.fixture
    def scanner(self, tmp_path: Path) -> AsyncDirectoryScanner:
        """Create a scanner instance for testing."""
        return AsyncDirectoryScanner(str(tmp_path), max_depth=3)

    @pytest.fixture
    def test_file(self, tmp_path: Path) -> Path:
        """Create a test file."""
        test_file = tmp_path / "test.jpg"
        test_file.write_text("Hello, World!")
        return test_file

    @pytest.fixture
    def test_dir(self, tmp_path: Path) -> Path:
        """Create a test directory."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()
        return test_dir

    def test_get_entry_metadata_file(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path, test_file: Path
    ) -> None:
        """_get_entry_metadata extracts file metadata correctly."""
        with os.scandir(tmp_path) as entries:
            for entry in entries:
                if entry.name == test_file.name:
                    metadata = scanner._get_entry_metadata(entry, depth=1)
                    break
            else:
                pytest.fail("Test file not found in scandir")

        assert metadata is not None
        assert metadata.name == "test.jpg"
        assert metadata.extension == ".jpg"
        assert metadata.is_directory is False
        assert metadata.is_symlink is False
        assert metadata.depth == 1
        assert metadata.size_bytes == 13  # "Hello, World!"

    def test_get_entry_metadata_directory(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path, test_dir: Path
    ) -> None:
        """_get_entry_metadata extracts directory metadata correctly."""
        with os.scandir(tmp_path) as entries:
            for entry in entries:
                if entry.name == test_dir.name:
                    metadata = scanner._get_entry_metadata(entry, depth=0)
                    break
            else:
                pytest.fail("Test directory not found in scandir")

        assert metadata is not None
        assert metadata.name == "subdir"
        assert metadata.is_directory is True
        assert metadata.depth == 0

    def test_get_entry_metadata_with_parent(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path, test_file: Path
    ) -> None:
        """_get_entry_metadata correctly captures parent directory."""
        with os.scandir(tmp_path) as entries:
            for entry in entries:
                if entry.name == test_file.name:
                    metadata = scanner._get_entry_metadata(entry, depth=1)
                    break
            else:
                pytest.fail("Test file not found in scandir")

        assert metadata is not None
        assert metadata.parent_directory == str(tmp_path)


class TestAsyncDirectoryScannerSyncMethods:
    """Tests for synchronous directory scanning methods."""

    @pytest.fixture
    def scanner(self, tmp_path: Path) -> AsyncDirectoryScanner:
        """Create a scanner instance for testing."""
        return AsyncDirectoryScanner(str(tmp_path), max_depth=3)

    def test_scan_directory_sync_empty(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path
    ) -> None:
        """_scan_directory_sync returns empty list for empty directory."""
        result = scanner._scan_directory_sync(tmp_path)

        assert result == []

    def test_scan_directory_sync_with_files(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path
    ) -> None:
        """_scan_directory_sync returns entries for directory with files."""
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        result = scanner._scan_directory_sync(tmp_path)

        assert len(result) == 2
        names = {entry.name for entry in result}
        assert names == {"file1.txt", "file2.txt"}

    def test_scan_directory_sync_nonexistent(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path
    ) -> None:
        """_scan_directory_sync returns empty list for nonexistent directory."""
        nonexistent = tmp_path / "nonexistent"

        result = scanner._scan_directory_sync(nonexistent)

        assert result == []


class TestAsyncDirectoryScannerAsync:
    """Tests for async scanning methods."""

    @pytest.fixture
    def scanner(self, tmp_path: Path) -> Generator[AsyncDirectoryScanner, None, None]:
        """Create a scanner instance for testing."""
        s = AsyncDirectoryScanner(str(tmp_path), max_depth=3)
        yield s
        s._executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_scan_directory_async(
        self, scanner: AsyncDirectoryScanner, tmp_path: Path
    ) -> None:
        """_scan_directory returns entries asynchronously."""
        (tmp_path / "async_file.txt").write_text("async content")

        result = await scanner._scan_directory(tmp_path)

        assert len(result) == 1
        assert result[0].name == "async_file.txt"

    @pytest.mark.asyncio
    async def test_run_in_executor(self, scanner: AsyncDirectoryScanner) -> None:
        """_run_in_executor runs blocking function in thread pool."""

        def blocking_func(x: int, y: int) -> int:
            return x + y

        result = await scanner._run_in_executor(blocking_func, 2, 3)

        assert result == 5

    @pytest.mark.asyncio
    async def test_scan_raises_for_nonexistent(self, tmp_path: Path) -> None:
        """scan() raises FileNotFoundError for nonexistent directory."""
        nonexistent = tmp_path / "nonexistent"
        scanner = AsyncDirectoryScanner(str(nonexistent), max_depth=1)

        with pytest.raises(FileNotFoundError, match="Directory not found"):
            async for _ in scanner.scan():
                pass

    @pytest.mark.asyncio
    async def test_scan_raises_for_file(self, tmp_path: Path) -> None:
        """scan() raises NotADirectoryError when given a file."""
        test_file = tmp_path / "not_a_dir.txt"
        test_file.write_text("I am a file")
        scanner = AsyncDirectoryScanner(str(test_file), max_depth=1)

        with pytest.raises(NotADirectoryError, match="Not a directory"):
            async for _ in scanner.scan():
                pass


class TestAsyncDirectoryScannerDatabase:
    """Tests for database setup and operations."""

    @pytest.fixture
    def scanner(self, tmp_path: Path) -> AsyncDirectoryScanner:
        """Create a scanner instance for testing."""
        db_path = tmp_path / "test.db"
        return AsyncDirectoryScanner(str(tmp_path), max_depth=1, db_path=str(db_path))

    def test_setup_database_creates_table(self, scanner: AsyncDirectoryScanner) -> None:
        """_setup_database creates the files table."""
        import sqlite_utils

        db = sqlite_utils.Database(scanner.db_path)
        scanner._setup_database(db)

        assert "files" in db.table_names()

    def test_setup_database_creates_indexes(self, scanner: AsyncDirectoryScanner) -> None:
        """_setup_database creates expected indexes."""
        import sqlite_utils
        from sqlite_utils.db import Table

        db = sqlite_utils.Database(scanner.db_path)
        scanner._setup_database(db)

        table: Table = db.table("files")  # pyright: ignore[reportAssignmentType]
        indexes = {idx.name for idx in table.indexes}
        expected_indexes = {
            "idx_files_parent",
            "idx_files_extension",
            "idx_files_name",
            "idx_files_size",
            "idx_files_modified",
            "idx_files_is_directory",
        }
        assert expected_indexes.issubset(indexes)

    def test_setup_database_idempotent(self, scanner: AsyncDirectoryScanner) -> None:
        """_setup_database can be called multiple times safely."""
        import sqlite_utils

        db = sqlite_utils.Database(scanner.db_path)

        # Call twice
        scanner._setup_database(db)
        scanner._setup_database(db)

        assert "files" in db.table_names()


class TestAsyncDirectoryScannerRun:
    """Tests for the main run() method."""

    @pytest.mark.asyncio
    async def test_run_empty_directory(self, tmp_path: Path) -> None:
        """run() handles empty directory."""
        # Create an explicit empty subdirectory to avoid any system artifacts
        empty_dir = tmp_path / "empty_subdir"
        empty_dir.mkdir()
        db_path = tmp_path / "empty.db"
        scanner = AsyncDirectoryScanner(str(empty_dir), max_depth=1, db_path=str(db_path))

        stats = await scanner.run()

        assert stats["total_items"] == 0
        assert stats["files"] == 0
        assert stats["directories"] == 0

    @pytest.mark.asyncio
    async def test_run_with_files(self, tmp_path: Path) -> None:
        """run() scans and stores file metadata."""
        # Create test structure
        (tmp_path / "file1.jpg").write_text("content1")
        (tmp_path / "file2.mp4").write_text("print('hello')")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.mp3").write_text("nested")

        db_path = tmp_path / "scan.db"
        scanner = AsyncDirectoryScanner(str(tmp_path), max_depth=2, db_path=str(db_path))

        stats = await scanner.run()

        assert stats["total_items"] > 0
        assert stats["files"] >= 3  # At least our 3 files
        assert stats["directories"] >= 1  # At least subdir
        assert stats["root_path"] == str(tmp_path.resolve())
        assert "elapsed_seconds" in stats
        assert "items_per_second" in stats

    @pytest.mark.asyncio
    async def test_run_respects_max_depth(self, tmp_path: Path) -> None:
        """run() respects max_depth limit."""
        # Create nested structure
        level1 = tmp_path / "level1"
        level1.mkdir()
        (level1 / "file1.txt").write_text("1")

        level2 = level1 / "level2"
        level2.mkdir()
        (level2 / "file2.txt").write_text("2")

        level3 = level2 / "level3"
        level3.mkdir()
        (level3 / "file3.txt").write_text("3")

        db_path = tmp_path / "depth.db"

        # Scan with depth=1 (should not reach level3)
        scanner = AsyncDirectoryScanner(str(tmp_path), max_depth=1, db_path=str(db_path))

        stats = await scanner.run()

        # Verify scan completed successfully
        assert stats["total_items"] > 0

        # Verify the database doesn't contain level3 content
        import sqlite_utils

        db = sqlite_utils.Database(str(db_path))
        paths = [row["path"] for row in db["files"].rows]
        assert not any("level3" in p for p in paths)


# ============================================================================
# parse_args Tests
# ============================================================================


class TestParseArgs:
    """Tests for the parse_args function."""

    @pytest.fixture(autouse=True)
    def reset_argv(self) -> Generator[None, None, None]:
        """Save and restore sys.argv."""
        original_argv = sys.argv
        yield
        sys.argv = original_argv

    def test_parse_args_required_directory(self) -> None:
        """parse_args requires directory argument."""
        sys.argv = ["boss-file-utils", "/tmp/test"]

        args = parse_args()

        assert args.directory == "/tmp/test"

    def test_parse_args_default_values(self) -> None:
        """parse_args has expected default values."""
        sys.argv = ["boss-file-utils", "/tmp"]

        args = parse_args()

        assert args.depth == 3
        assert args.db == "file_index.db"
        assert args.workers == 50
        assert args.batch_size == 500
        assert args.json is False
        assert args.log_level == "INFO"

    def test_parse_args_custom_depth(self) -> None:
        """parse_args accepts custom depth."""
        sys.argv = ["boss-file-utils", "/tmp", "--depth", "10"]

        args = parse_args()

        assert args.depth == 10

    def test_parse_args_short_depth(self) -> None:
        """parse_args accepts -d for depth."""
        sys.argv = ["boss-file-utils", "/tmp", "-d", "5"]

        args = parse_args()

        assert args.depth == 5

    def test_parse_args_unlimited_depth(self) -> None:
        """parse_args accepts -1 for unlimited depth."""
        sys.argv = ["boss-file-utils", "/tmp", "--depth", "-1"]

        args = parse_args()

        assert args.depth == -1

    def test_parse_args_custom_database(self) -> None:
        """parse_args accepts custom database path."""
        sys.argv = ["boss-file-utils", "/tmp", "--db", "custom.db"]

        args = parse_args()

        assert args.db == "custom.db"

    def test_parse_args_workers(self) -> None:
        """parse_args accepts custom worker count."""
        sys.argv = ["boss-file-utils", "/tmp", "-w", "100"]

        args = parse_args()

        assert args.workers == 100

    def test_parse_args_long_workers(self) -> None:
        """parse_args accepts --workers flag."""
        sys.argv = ["boss-file-utils", "/tmp", "--workers", "200"]

        args = parse_args()

        assert args.workers == 200

    def test_parse_args_batch_size(self) -> None:
        """parse_args accepts custom batch size."""
        sys.argv = ["boss-file-utils", "/tmp", "-b", "1000"]

        args = parse_args()

        assert args.batch_size == 1000

    def test_parse_args_long_batch_size(self) -> None:
        """parse_args accepts --batch-size flag."""
        sys.argv = ["boss-file-utils", "/tmp", "--batch-size", "2000"]

        args = parse_args()

        assert args.batch_size == 2000

    def test_parse_args_json_flag(self) -> None:
        """parse_args accepts --json flag."""
        sys.argv = ["boss-file-utils", "/tmp", "--json"]

        args = parse_args()

        assert args.json is True

    def test_parse_args_log_level_debug(self) -> None:
        """parse_args accepts DEBUG log level."""
        sys.argv = ["boss-file-utils", "/tmp", "--log-level", "DEBUG"]

        args = parse_args()

        assert args.log_level == "DEBUG"

    def test_parse_args_log_level_error(self) -> None:
        """parse_args accepts ERROR log level."""
        sys.argv = ["boss-file-utils", "/tmp", "--log-level", "ERROR"]

        args = parse_args()

        assert args.log_level == "ERROR"

    def test_parse_args_combined(self) -> None:
        """parse_args handles multiple arguments together."""
        sys.argv = [
            "boss-file-utils",
            "/data",
            "-d",
            "5",
            "--db",
            "my.db",
            "-w",
            "75",
            "-b",
            "250",
            "--json",
            "--log-level",
            "WARNING",
        ]

        args = parse_args()

        assert args.directory == "/data"
        assert args.depth == 5
        assert args.db == "my.db"
        assert args.workers == 75
        assert args.batch_size == 250
        assert args.json is True
        assert args.log_level == "WARNING"

    def test_parse_args_missing_directory_exits(self) -> None:
        """parse_args exits when directory is missing."""
        sys.argv = ["boss-file-utils"]

        with pytest.raises(SystemExit):
            parse_args()

    def test_parse_args_invalid_log_level_exits(self) -> None:
        """parse_args exits for invalid log level."""
        sys.argv = ["boss-file-utils", "/tmp", "--log-level", "INVALID"]

        with pytest.raises(SystemExit):
            parse_args()


# ============================================================================
# main() Integration Tests
# ============================================================================


class TestMain:
    """Tests for the main() entry point function."""

    @pytest.fixture(autouse=True)
    def reset_argv(self) -> Generator[None, None, None]:
        """Save and restore sys.argv."""
        original_argv = sys.argv
        yield
        sys.argv = original_argv

    @pytest.mark.asyncio
    async def test_main_successful_scan(self, tmp_path: Path) -> None:
        """main() completes a successful scan."""
        from boss_file_utils.cli import main

        # Create test files
        (tmp_path / "test.txt").write_text("test content")
        db_path = tmp_path / "output.db"

        sys.argv = [
            "boss-file-utils",
            str(tmp_path),
            "--db",
            str(db_path),
            "--depth",
            "1",
        ]

        with (
            patch("boss_file_utils.cli.setup_telemetry"),
            patch("boss_file_utils.cli.instrument_all"),
            patch("boss_file_utils.cli.configure_logging"),
            patch("boss_file_utils.cli.stop_queue_listener"),
        ):
            await main()

        # Verify database was created
        assert db_path.exists()

    @pytest.mark.asyncio
    async def test_main_nonexistent_directory_exits(self, tmp_path: Path) -> None:
        """main() exits with code 1 for nonexistent directory."""
        from boss_file_utils.cli import main

        nonexistent = tmp_path / "nonexistent"
        sys.argv = ["boss-file-utils", str(nonexistent)]

        with (
            patch("boss_file_utils.cli.setup_telemetry"),
            patch("boss_file_utils.cli.instrument_all"),
            patch("boss_file_utils.cli.configure_logging"),
            patch("boss_file_utils.cli.stop_queue_listener"),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_main_file_instead_of_dir_exits(self, tmp_path: Path) -> None:
        """main() exits with code 1 when given a file instead of directory."""
        from boss_file_utils.cli import main

        test_file = tmp_path / "not_a_dir.txt"
        test_file.write_text("content")
        sys.argv = ["boss-file-utils", str(test_file)]

        with (
            patch("boss_file_utils.cli.setup_telemetry"),
            patch("boss_file_utils.cli.instrument_all"),
            patch("boss_file_utils.cli.configure_logging"),
            patch("boss_file_utils.cli.stop_queue_listener"),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_main_unlimited_depth(self, tmp_path: Path) -> None:
        """main() handles -1 depth for unlimited scanning."""
        from boss_file_utils.cli import main

        db_path = tmp_path / "unlimited.db"
        sys.argv = [
            "boss-file-utils",
            str(tmp_path),
            "--db",
            str(db_path),
            "--depth",
            "-1",
        ]

        with (
            patch("boss_file_utils.cli.setup_telemetry"),
            patch("boss_file_utils.cli.instrument_all"),
            patch("boss_file_utils.cli.configure_logging"),
            patch("boss_file_utils.cli.stop_queue_listener"),
        ):
            await main()

        assert db_path.exists()
