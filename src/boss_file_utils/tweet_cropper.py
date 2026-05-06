"""Tweet screenshot cropper for Instagram posting.

Automatically crops tweet screenshots to remove iOS chrome (status bar,
the 'Post' header, the bottom nav bar, and the engagement metrics row),
leaving just the tweet content from the author down through the
timestamp/views line.

Uses Tesseract OCR (via pytesseract) to find two text anchors in the image:
- TOP    : bottom of the 'Post' header, or 'You reposted' line if present
           (whichever sits lower in the image)
- BOTTOM : the last line matching the tweet timestamp pattern, e.g.
           '1:11 PM . 5/4/26 . 1.7M Views' (matches on '... Views')

Then it crops the full image width between those two y-coordinates.

Tesseract binary must be installed system-wide:
    macOS:    brew install tesseract
    Ubuntu:   sudo apt-get install tesseract-ocr
    Windows:  https://github.com/UB-Mannheim/tesseract/wiki
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytesseract  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageOps

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL.Image import Image as PILImage

VIEWS_RE = re.compile(r"\d[\d.,]*\s*[KkMmBb]?\s+[Vv]iew[s]?\b")

TOP_GAP = 12
BOTTOM_GAP = 18
# Exact (case-insensitive, punctuation-stripped) words that mark a tweet header.
# Word-level matching avoids contamination from Tesseract grouping the retweet
# UI connector glyph '|' into the same line as 'You reposted', which would push
# the bucket bbox down through the author row.
TOP_MARKER_WORDS: frozenset[str] = frozenset({"post", "reposted"})
_PUNCT_TO_STRIP = ".:|()_-,*#@"

# Instagram aspect ratios as (width, height)
IG_PORTRAIT: tuple[int, int] = (1080, 1350)  # 4:5, the tallest IG allows
IG_SQUARE: tuple[int, int] = (1080, 1080)  # 1:1


@dataclass(frozen=True)
class OcrLine:
    """One line of OCR'd text with its bounding box (in image pixel coordinates)."""

    text: str
    top: int
    bottom: int
    left: int
    right: int


@dataclass
class _LineBucket:
    """Mutable accumulator used while grouping OCR words into lines."""

    words: list[str]
    top: int
    bottom: int
    left: int
    right: int


def find_top_y(words: list[OcrLine], image_height: int) -> int:
    """Return the y-coordinate just below the last header marker word.

    `words` is expected to be word-level OCR data (one OcrLine per OCR'd word).
    Word-level data is required because Tesseract groups the retweet UI's
    vertical connector glyph '|' into the same 'line' bucket as 'You reposted',
    which would inflate the bucket's bottom y down through the author row.

    Header marker words are 'Post' (the screen title) and 'reposted' (from the
    'You reposted' indicator). The function picks the LOWER of the two so a
    repost image's crop starts underneath the indicator. Only considers words
    in the top ~25% of the image to avoid matching a stray 'post' substring in
    the tweet body.
    """
    cutoff = image_height * 0.25
    candidate_bottom = 0
    for w in words:
        if w.top > cutoff:
            continue
        cleaned = w.text.lower().strip(_PUNCT_TO_STRIP)
        if cleaned in TOP_MARKER_WORDS:
            candidate_bottom = max(candidate_bottom, w.bottom)
    if candidate_bottom == 0:
        return 0
    return max(0, candidate_bottom + TOP_GAP)


def find_bottom_y(lines: list[OcrLine], image_height: int) -> int:
    """Return the y-coordinate just below the tweet's '... Views' timestamp line.

    Uses the LAST occurrence so quote-tweets (which contain an embedded inner
    tweet, sometimes itself bearing a 'Views' figure) still anchor on the
    OUTER tweet's timestamp.
    """
    last_bottom: int | None = None
    for ln in lines:
        if VIEWS_RE.search(ln.text):
            last_bottom = ln.bottom
    if last_bottom is None:
        return int(image_height * 0.88)
    return min(image_height, last_bottom + BOTTOM_GAP)


def _ocr_data(image: PILImage) -> dict[str, list[Any]]:
    """Run Tesseract once and return its raw word-level data dict."""
    return cast(
        "dict[str, list[Any]]",
        pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT),
    )


def _word_passes_confidence(data: dict[str, list[Any]], i: int) -> bool:
    """Return True if the word at index `i` passes the confidence threshold."""
    try:
        conf = float(data["conf"][i])
    except (TypeError, ValueError):
        conf = -1.0
    return not (conf < 30 and conf != -1)


def ocr_words(image: PILImage) -> list[OcrLine]:
    """Return one OcrLine per OCR-detected word (NOT grouped into lines).

    Used by find_top_y to avoid Tesseract's line-bucket inflation when a
    line's bbox wraps non-text glyphs (e.g. the retweet UI connector).
    """
    data = _ocr_data(image)
    words: list[OcrLine] = []
    for i in range(len(data["text"])):
        text = str(data["text"][i])
        if not text.strip():
            continue
        if not _word_passes_confidence(data, i):
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        words.append(
            OcrLine(
                text=text,
                top=top,
                bottom=top + int(data["height"][i]),
                left=left,
                right=left + int(data["width"][i]),
            )
        )
    words.sort(key=lambda w: w.top)
    return words


def ocr_lines(image: PILImage) -> list[OcrLine]:
    """Run Tesseract on the image and group word-level results into lines.

    Used by find_bottom_y (whose regex needs to see multi-word phrases like
    '1.7M Views' as one line) and by save_debug_overlay.
    """
    data = _ocr_data(image)
    buckets: dict[tuple[int, int, int], _LineBucket] = {}
    for i in range(len(data["text"])):
        word = str(data["text"][i])
        if not word.strip():
            continue
        if not _word_passes_confidence(data, i):
            continue
        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        left = int(data["left"][i])
        top = int(data["top"][i])
        right = left + int(data["width"][i])
        bottom = top + int(data["height"][i])
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = _LineBucket(words=[word], top=top, bottom=bottom, left=left, right=right)
            continue
        bucket.words.append(word)
        bucket.top = min(bucket.top, top)
        bucket.bottom = max(bucket.bottom, bottom)
        bucket.left = min(bucket.left, left)
        bucket.right = max(bucket.right, right)
    lines = [
        OcrLine(
            text=" ".join(b.words),
            top=b.top,
            bottom=b.bottom,
            left=b.left,
            right=b.right,
        )
        for b in buckets.values()
    ]
    lines.sort(key=lambda ln: ln.top)
    return lines


def crop_tweet(image: PILImage) -> tuple[PILImage, int, int]:
    """Run OCR + anchor detection and return (cropped_image, top_y, bottom_y).

    Raises ValueError if anchor detection produced an inverted or empty range.
    """
    words = ocr_words(image)
    lines = ocr_lines(image)
    top_y = find_top_y(words, image.height)
    bottom_y = find_bottom_y(lines, image.height)
    if bottom_y <= top_y:
        msg = (
            f"Anchor detection failed: top={top_y}, bottom={bottom_y} "
            f"(image height={image.height}). Try --debug to inspect OCR output."
        )
        raise ValueError(msg)
    cropped = image.crop((0, top_y, image.width, bottom_y))
    return cropped, top_y, bottom_y


def pad_for_instagram(
    image: PILImage,
    target: tuple[int, int] = IG_PORTRAIT,
    bg: tuple[int, int, int] = (255, 255, 255),
) -> PILImage:
    """Pad the cropped tweet onto a canvas with Instagram's target aspect ratio.

    The cropped tweet is centered and the rest is filled with `bg`. We never
    SCALE DOWN content, only pad - so text stays crisp.
    """
    target_w, target_h = target
    target_aspect = target_w / target_h
    cur_aspect = image.width / image.height

    if cur_aspect > target_aspect:
        # Wider than target -> pad top/bottom
        new_h = round(image.width / target_aspect)
        canvas = Image.new("RGB", (image.width, new_h), bg)
        canvas.paste(image, (0, (new_h - image.height) // 2))
    else:
        # Taller (or equal) -> pad left/right
        new_w = round(image.height * target_aspect)
        canvas = Image.new("RGB", (new_w, image.height), bg)
        canvas.paste(image, ((new_w - image.width) // 2, 0))

    return canvas


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the tweet-cropper CLI."""
    parser = argparse.ArgumentParser(
        description="Crop tweet screenshots for Instagram posting.",
    )
    parser.add_argument("inputs", nargs="+", help="Screenshot file(s) to process")
    parser.add_argument(
        "--output-dir",
        "-o",
        default="./cropped",
        help="Directory to write cropped images into (default: ./cropped)",
    )
    parser.add_argument(
        "--pad-to-instagram",
        action="store_true",
        help="Pad result onto a 4:5 portrait canvas (1080x1350) suitable for IG",
    )
    parser.add_argument(
        "--square",
        action="store_true",
        help="With --pad-to-instagram, use 1:1 square (1080x1080) instead of 4:5",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save a debug overlay showing OCR boxes + chosen crop lines",
    )
    return parser.parse_args(argv)


def save_debug_overlay(
    image: PILImage,
    lines: list[OcrLine],
    top_y: int,
    bottom_y: int,
    path: Path,
) -> None:
    """Draw OCR boxes + chosen anchors onto a copy of the image for debugging."""
    overlay = image.copy().convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for ln in lines:
        draw.rectangle([ln.left, ln.top, ln.right, ln.bottom], outline="red", width=2)
    draw.line([(0, top_y), (overlay.width, top_y)], fill="lime", width=4)
    draw.line([(0, bottom_y), (overlay.width, bottom_y)], fill="cyan", width=4)
    overlay.save(path)


def process_one(input_path: Path, out_dir: Path, args: argparse.Namespace) -> None:
    """Crop a single screenshot, write the result (and optional debug overlay)."""
    image = ImageOps.exif_transpose(Image.open(input_path)).convert("RGB")

    words = ocr_words(image)
    lines = ocr_lines(image)
    top_y = find_top_y(words, image.height)
    bottom_y = find_bottom_y(lines, image.height)

    if args.debug:
        debug_path = out_dir / f"{input_path.stem}_debug.png"
        save_debug_overlay(image, lines, top_y, bottom_y, debug_path)
        print(f"  debug overlay -> {debug_path}")
        print(f"  anchors: top={top_y}px  bottom={bottom_y}px (image h={image.height})")

    if bottom_y <= top_y:
        msg = (
            f"Anchor detection failed: top={top_y}, bottom={bottom_y} "
            f"(image height={image.height})."
        )
        raise ValueError(msg)

    cropped = image.crop((0, top_y, image.width, bottom_y))
    if args.pad_to_instagram:
        target = IG_SQUARE if args.square else IG_PORTRAIT
        cropped = pad_for_instagram(cropped, target=target)

    suffix = "_ig" if args.pad_to_instagram else "_cropped"
    output_path = out_dir / f"{input_path.stem}{suffix}.jpg"
    cropped.save(output_path, quality=95)
    print(f"  {input_path.name} -> {output_path.name}  ({cropped.width}x{cropped.height})")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the tweet-cropper console script."""
    args = parse_args(argv)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs = [Path(p) for p in args.inputs]
    print(f"Processing {len(inputs)} screenshot(s) -> {out_dir}/")

    failures = 0
    for input_path in inputs:
        if not input_path.exists():
            print(f"  SKIP {input_path}: not found", file=sys.stderr)
            failures += 1
            continue
        try:
            process_one(input_path, out_dir, args)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {input_path.name}: {e}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
