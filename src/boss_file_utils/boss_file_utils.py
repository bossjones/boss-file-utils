"""Package entry point - delegates to CLI scanner."""

from __future__ import annotations

import asyncio

from boss_file_utils.cli import main as async_main


def main() -> None:
    """Entry point for boss-file-utils command."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
