# boss-file-utils

Async directory scanner that indexes media file metadata into SQLite.

boss-file-utils is a Python CLI tool that efficiently scans directories for media files (images, videos, audio) and stores their metadata in a SQLite database. Built with async I/O, it uses breadth-first search with concurrent processing to maximize performance on large directory trees.

## Key Features

- ⚡ **Async I/O** with BFS traversal for efficient parallel directory scanning
- 🎬 **Media-focused**: indexes images, videos, and audio files
- 🗄️ **SQLite storage** with optimized indexes (path, extension, size, modified date)
- 📊 **OpenTelemetry instrumentation** for production observability
- 📝 **Structured logging** with trace context integration
- ⚙️ **Configurable** batch processing and concurrency control
- 🧹 **Automatic .DS_Store filtering**

## Installation

```bash
pip install boss-file-utils
```

Or with uv:

```bash
uv tool install boss-file-utils
```

## Quick Start

Scan a directory and create a SQLite database with indexed files:

```bash
uv run boss-file-utils /path/to/photos --depth 3
```

This creates `file_index.db` with metadata for all media files found.

## CLI Usage Examples

### Basic Scanning

```bash
# Scan current directory with depth limit
uv run boss-file-utils . --depth 3

# Scan specific directory
uv run boss-file-utils ~/Pictures --depth 5
```

### Depth Control

```bash
# Limited depth
uv run boss-file-utils /media --depth 2

# Unlimited depth (scan entire tree)
uv run boss-file-utils /media --depth -1
```

### Performance Tuning

```bash
# Increase workers for faster scanning
uv run boss-file-utils /media --workers 100

# Adjust batch size for memory/performance tradeoff
uv run boss-file-utils /media --batch-size 1000

# Combined: fast scanning with custom database
uv run boss-file-utils /media --depth -1 --workers 200 --batch-size 2000 --db media_index.db
```

### Production Usage

```bash
# JSON logging for production
uv run boss-file-utils /data --json --log-level INFO

# Custom database location
uv run boss-file-utils /data --db /var/lib/file-index.db
```

## Querying the Database

Use [sqlite-utils](https://github.com/simonw/sqlite-utils) to query the indexed files:

```bash
# List tables in the database
sqlite-utils tables file_index.db

# View first 10 indexed files
sqlite-utils rows file_index.db files --limit 10

# Count files by extension
sqlite-utils "SELECT extension, COUNT(*) as count FROM files GROUP BY extension ORDER BY count DESC" file_index.db

# Find large files (>1GB)
sqlite-utils "SELECT name, size_bytes/1024/1024/1024 as size_gb FROM files WHERE size_bytes > 1000000000 ORDER BY size_bytes DESC" file_index.db

# Find recently modified files
sqlite-utils "SELECT name, modified_datetime FROM files ORDER BY modified_timestamp DESC LIMIT 20" file_index.db

# Export to JSON
sqlite-utils rows file_index.db files --json

# Export to CSV
sqlite-utils rows file_index.db files --csv > files.csv
```

## Database Schema

The `files` table contains:

| Column | Type | Description |
|--------|------|-------------|
| `path` | TEXT | Full file path (UNIQUE) |
| `name` | TEXT | Filename |
| `parent_directory` | TEXT | Parent directory path (indexed) |
| `extension` | TEXT | File extension (indexed) |
| `size_bytes` | INTEGER | File size in bytes (indexed) |
| `modified_timestamp` | REAL | Last modified Unix timestamp (indexed) |
| `modified_datetime` | TEXT | Last modified ISO 8601 datetime |
| `created_timestamp` | REAL | Creation Unix timestamp |
| `created_datetime` | TEXT | Creation ISO 8601 datetime |
| `is_directory` | BOOLEAN | True if directory (indexed) |
| `is_symlink` | BOOLEAN | True if symbolic link |
| `depth` | INTEGER | Nesting depth from scan root |
| `indexed_at` | TEXT | When this record was created |

## Python Library Usage

Use boss-file-utils programmatically:

```python
import asyncio
from boss_file_utils.cli import AsyncDirectoryScanner

async def main():
    scanner = AsyncDirectoryScanner(
        root_path="/path/to/photos",
        max_depth=3,
        db_path="photos.db",
        max_workers=50,
        batch_size=500
    )

    stats = await scanner.run()
    print(f"Indexed {stats['files']} files and {stats['directories']} directories")
    print(f"Processed {stats['items_per_second']:.1f} items/second")

asyncio.run(main())
```

## Supported Media Formats

### Images
JPG, JPEG, PNG, GIF, WebP, HEIC, BMP, TIFF, TIF, SVG, ICO

### Videos
MP4, MOV, AVI, MKV, WebM, FLV, WMV, M4V, MPG, MPEG, 3GP

### Audio
MP3, M4A, WAV, FLAC, AAC, OGG, WMA, Opus, ALAC

**Note:** `.DS_Store` files are automatically excluded

## CLI Arguments Reference

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `directory` | positional | required | Root directory to scan |
| `-d, --depth` | integer | 3 | Maximum nesting depth (-1 for unlimited) |
| `--db` | string | "file_index.db" | SQLite database file path |
| `-w, --workers` | integer | 50 | Maximum concurrent workers |
| `-b, --batch-size` | integer | 500 | Records per batch insert |
| `--json` | flag | false | Output logs in JSON format |
| `--log-level` | choice | "INFO" | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Requirements

- Python 3.13+
- sqlite-utils 3.39+
- OpenTelemetry instrumentation
- structlog for logging

## Related Projects

- [sqlite-utils](https://github.com/simonw/sqlite-utils) - Python library and CLI for manipulating SQLite databases (powers boss-file-utils' database layer)

## Development

For development setup, testing, and contribution guidelines:

```bash
# Install with development dependencies
make install

# Run tests
make test

# Run linting and type checking
make lint
```

## tweet-cropper

Crop iOS tweet screenshots to remove chrome (status bar, "Post" header, bottom nav bar, and engagement metrics row), leaving just the tweet content from the author through the timestamp/views line. Uses Tesseract OCR to locate crop anchors automatically.

### Requirements

Tesseract must be installed system-wide:

```bash
# macOS
brew install tesseract

# Ubuntu
sudo apt-get install tesseract-ocr
```

### Usage Examples

```bash
# Crop a single screenshot (output goes to ./cropped/)
tweet-cropper screenshot.png

# Crop multiple files at once
tweet-cropper shot1.png shot2.png shot3.png

# Write output to a custom directory
tweet-cropper screenshot.png --output-dir ~/Desktop/cropped
tweet-cropper screenshot.png -o ~/Desktop/cropped

# Pad onto a 4:5 Instagram portrait canvas (1080x1350)
tweet-cropper screenshot.png --pad-to-instagram

# Pad onto a 1:1 Instagram square canvas (1080x1080)
tweet-cropper screenshot.png --pad-to-instagram --square

# Save a debug overlay showing OCR boxes and chosen crop lines
tweet-cropper screenshot.png --debug

# All options combined
tweet-cropper shot1.png shot2.png -o ~/Desktop/ig --pad-to-instagram --square --debug
```

### Output Files

| Scenario | Output filename |
|----------|----------------|
| Default crop | `{stem}_cropped.jpg` |
| With `--pad-to-instagram` | `{stem}_ig.jpg` |
| With `--debug` | `{stem}_debug.png` (alongside the cropped output) |

### CLI Arguments Reference

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `inputs` | positional (1+) | required | Screenshot file(s) to process |
| `-o, --output-dir` | string | `./cropped` | Directory to write cropped images into |
| `--pad-to-instagram` | flag | false | Pad result onto a 4:5 portrait canvas (1080x1350) for Instagram |
| `--square` | flag | false | With `--pad-to-instagram`, use 1:1 square (1080x1080) instead of 4:5 |
| `--debug` | flag | false | Save a debug overlay image showing OCR boxes and chosen crop lines |

## License

MIT License
