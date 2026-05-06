"""Tests for the tweet_cropper module - OCR-based tweet screenshot cropping."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PIL import Image, ImageOps

from boss_file_utils.tweet_cropper import (
    BOTTOM_GAP,
    IG_PORTRAIT,
    IG_SQUARE,
    TOP_GAP,
    VIEWS_RE,
    OcrLine,
    crop_tweet,
    find_bottom_y,
    find_top_y,
    main,
    pad_for_instagram,
    parse_args,
    process_one,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
TESSERACT_AVAILABLE = shutil.which("tesseract") is not None


class TestViewsRegex:
    """Tests for the VIEWS_RE pattern that anchors the bottom-of-tweet timestamp line."""

    @pytest.mark.parametrize(
        "text",
        [
            "1.7M Views",
            "2.8M Views",
            "150K Views",
            "57 Views",
            "1:11 PM 5/4/26 1.7M Views",
            "8:31 AM 5/5/26 2.8M Views",
        ],
    )
    def test_matches_typical_view_count_lines(self, text: str) -> None:
        """VIEWS_RE matches the trailing 'N(K|M|B)? Views' on a real tweet timestamp line."""
        assert VIEWS_RE.search(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "View quotes",
            "Profile views",
            "Relevant views above",
            "the views from the bridge",
        ],
    )
    def test_rejects_views_word_without_leading_number(self, text: str) -> None:
        """VIEWS_RE only fires on '<digit>... Views' so body text containing the
        bare word 'view(s)' (no leading number) does not produce false bottom anchors."""
        assert VIEWS_RE.search(text) is None


class TestFindTopY:
    """Tests for find_top_y, which anchors the top of the crop to the
    'Post' header or the 'You reposted' indicator (whichever sits lower).

    find_top_y operates on WORD-level OCR data (one OcrLine per word) — see
    docstring for why line-level data is unsafe."""

    def test_picks_lower_of_post_and_reposted(self) -> None:
        """When both 'Post' header and 'reposted' word exist in the top
        25% of the image, find_top_y returns just below the LOWER one (so
        the crop sits underneath the repost indicator)."""
        words = [
            OcrLine(text="Post", top=100, bottom=130, left=200, right=300),
            OcrLine(text="You", top=200, bottom=230, left=10, right=80),
            OcrLine(text="reposted", top=200, bottom=230, left=85, right=200),
            OcrLine(text="Body", top=400, bottom=440, left=0, right=80),
            OcrLine(text="content", top=400, bottom=440, left=85, right=200),
        ]
        assert find_top_y(words, image_height=2000) == 230 + TOP_GAP

    def test_falls_back_to_zero_when_no_header_marker(self) -> None:
        """If neither 'post' nor 'reposted' is found as a whole word in the
        top 25%, the function returns 0 (caller will treat as 'crop from top')."""
        words = [
            OcrLine(text="Some", top=100, bottom=140, left=0, right=80),
            OcrLine(text="random", top=100, bottom=140, left=85, right=200),
            OcrLine(text="body", top=100, bottom=140, left=205, right=300),
            OcrLine(text="text", top=100, bottom=140, left=305, right=400),
        ]
        assert find_top_y(words, image_height=2000) == 0

    def test_ignores_markers_below_top_25_percent(self) -> None:
        """Words containing 'post' as a substring (e.g. 'post-game') in the
        body of the tweet (beyond the top 25%) must not be picked up as a
        header anchor — exact-word matching guarantees this."""
        words = [
            # below the 25% cutoff (cutoff = 500 for image_height=2000)
            OcrLine(text="post-game", top=900, bottom=940, left=0, right=200),
            OcrLine(text="thoughts", top=900, bottom=940, left=205, right=400),
            OcrLine(text="repost", top=1200, bottom=1240, left=0, right=120),
            OcrLine(text="this", top=1200, bottom=1240, left=125, right=220),
        ]
        assert find_top_y(words, image_height=2000) == 0


class TestFindBottomY:
    """Tests for find_bottom_y, which anchors the bottom of the crop to the
    tweet's '... Views' timestamp line."""

    def test_returns_last_views_line_for_quote_tweet(self) -> None:
        """For quote-tweets there can be multiple 'Views'-bearing lines; the
        function must pick the LAST one so the crop ends at the OUTER (quote-
        tweeter's) timestamp, not the inner quoted tweet's."""
        lines = [
            OcrLine(text="Body of outer tweet", top=200, bottom=240, left=0, right=400),
            OcrLine(text="Inner quoted: 500 Views", top=800, bottom=840, left=0, right=300),
            OcrLine(text="1:11 PM 5/4/26 1.7M Views", top=1500, bottom=1550, left=0, right=400),
            OcrLine(text="Reply or View quotes", top=1700, bottom=1740, left=0, right=300),
        ]
        assert find_bottom_y(lines, image_height=2000) == 1550 + BOTTOM_GAP

    def test_falls_back_to_88_percent_when_no_views_line(self) -> None:
        """If no '... Views' line is found anywhere in the OCR output, fall
        back to 88% of image height (chops off the iOS nav bar / engagement
        row by default)."""
        lines = [
            OcrLine(text="No timestamp line here", top=100, bottom=140, left=0, right=400),
            OcrLine(text="Another body line", top=200, bottom=240, left=0, right=300),
        ]
        assert find_bottom_y(lines, image_height=2000) == int(2000 * 0.88)


class TestCropTweet:
    """Tests for the high-level crop_tweet function (OCR + anchor detection + crop)."""

    def test_raises_value_error_on_inverted_anchors(self, mocker: MockerFixture) -> None:
        """If anchor detection produces bottom_y <= top_y (e.g. degenerate input),
        crop_tweet raises ValueError rather than silently producing an empty crop."""
        image = Image.new("RGB", (400, 600), "white")
        mocker.patch("boss_file_utils.tweet_cropper.ocr_words", return_value=[])
        mocker.patch("boss_file_utils.tweet_cropper.ocr_lines", return_value=[])
        mocker.patch("boss_file_utils.tweet_cropper.find_top_y", return_value=500)
        mocker.patch("boss_file_utils.tweet_cropper.find_bottom_y", return_value=400)
        with pytest.raises(ValueError, match="Anchor detection failed"):
            crop_tweet(image)


class TestPadForInstagram:
    """Tests for pad_for_instagram, which embeds the cropped tweet into a canvas
    matching Instagram's target aspect ratio without scaling the content."""

    def test_pads_top_and_bottom_when_content_wider_than_4_to_5(self) -> None:
        """When content aspect > target aspect (e.g. wide screenshot vs 4:5),
        the function pads top/bottom so width is preserved and height grows."""
        # 1000x100 is much wider than the 4:5 (0.8) target
        image = Image.new("RGB", (1000, 100), "white")
        result = pad_for_instagram(image, target=IG_PORTRAIT)
        # new_h = round(width / target_aspect) = round(1000 / 0.8) = 1250
        assert result.size == (1000, 1250)

    def test_pads_left_and_right_when_content_taller_than_4_to_5(self) -> None:
        """When content aspect < target aspect (e.g. tall tweet crop vs 4:5),
        the function pads left/right so height is preserved and width grows."""
        # 100x1000 is much taller than the 4:5 (0.8) target
        image = Image.new("RGB", (100, 1000), "white")
        result = pad_for_instagram(image, target=IG_PORTRAIT)
        # new_w = round(height * target_aspect) = round(1000 * 0.8) = 800
        assert result.size == (800, 1000)

    def test_honors_square_target(self) -> None:
        """IG_SQUARE target produces a 1:1 canvas matching the longer image dimension."""
        # 200x100 is wider than 1:1
        image = Image.new("RGB", (200, 100), "white")
        result = pad_for_instagram(image, target=IG_SQUARE)
        assert result.size == (200, 200)


class TestParseArgs:
    """Tests for the CLI argument parser."""

    def test_accepts_pad_to_instagram_square_and_debug(self) -> None:
        """parse_args correctly recognises --pad-to-instagram, --square, --debug,
        and -o/--output-dir, plus one or more positional input paths."""
        args = parse_args(
            [
                "shot1.png",
                "shot2.png",
                "--output-dir",
                "/tmp/out",
                "--pad-to-instagram",
                "--square",
                "--debug",
            ]
        )
        assert args.inputs == ["shot1.png", "shot2.png"]
        assert args.output_dir == "/tmp/out"
        assert args.pad_to_instagram is True
        assert args.square is True
        assert args.debug is True

    def test_default_flag_values(self) -> None:
        """When optional flags are omitted, parse_args returns sensible defaults."""
        args = parse_args(["shot.png"])
        assert args.inputs == ["shot.png"]
        assert args.output_dir == "./cropped"
        assert args.pad_to_instagram is False
        assert args.square is False
        assert args.debug is False


@pytest.mark.integration
@pytest.mark.skipif(not TESSERACT_AVAILABLE, reason="tesseract binary not installed")
class TestIntegration:
    """End-to-end OCR-based crop tests on the real example screenshots in docs/.

    These run real Tesseract against the JPEG fixtures and assert the crop
    rectangle lands in plausible locations (loose bounds, since OCR output
    can vary across tesseract versions).
    """

    def test_crops_quote_tweet_screenshot(self) -> None:
        """imagasfe.jpg (Ava's quote-tweet, no 'You reposted') should crop
        from just below the 'Post' header to just below the OUTER '1.7M Views'
        line, leaving plenty of body content between them."""
        image_path = DOCS_DIR / "imagasfe.jpg"
        assert image_path.exists(), f"missing test fixture: {image_path}"
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        cropped, top_y, bottom_y = crop_tweet(image)
        assert top_y > 50, f"top_y={top_y} suggests 'Post' header not found"
        assert top_y < image.height * 0.25
        assert bottom_y > image.height * 0.5
        assert bottom_y < image.height, "fell back to 88% — no Views line OCR'd"
        assert cropped.height < image.height
        assert cropped.height > 300

    def test_crops_repost_screenshot_below_reposted_indicator(self) -> None:
        """image.jpg (Woofers' kamikaze repost) has BOTH a 'Post' header AND
        a 'You reposted' indicator. find_top_y must pick the LOWER one, so
        the resulting top_y is meaningfully below where 'Post' alone would land,
        BUT must not slice through the author row (Woofers/@NotWoofers) — the
        crop must still include the author info."""
        image_path = DOCS_DIR / "image.jpg"
        assert image_path.exists(), f"missing test fixture: {image_path}"
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        cropped, top_y, bottom_y = crop_tweet(image)
        # 'You reposted' sits below 'Post', so top_y should be > 10% of height
        assert top_y > image.height * 0.10
        # The Woofers author row starts at roughly y=426 in the 2622px image
        # (~16% from the top). Crop top MUST sit above that row.
        assert top_y < image.height * 0.165, (
            f"top_y={top_y} cuts through the author row (should be < {image.height * 0.165:.0f})"
        )
        assert bottom_y > image.height * 0.5
        assert bottom_y < image.height
        assert cropped.height < image.height
        assert cropped.height > 300

    def test_process_one_writes_cropped_jpeg(self, tmp_path: Path) -> None:
        """process_one reads <input>, crops, writes <stem>_cropped.jpg into output_dir."""
        args = parse_args(["dummy", "--output-dir", str(tmp_path)])
        image_path = DOCS_DIR / "imagasfe.jpg"
        process_one(image_path, tmp_path, args)
        output = tmp_path / "imagasfe_cropped.jpg"
        assert output.exists(), f"expected output file not written: {output}"
        # Sanity-check the output is a real, smaller-than-source JPEG
        out_img = Image.open(output)
        assert out_img.format == "JPEG"
        src_img = Image.open(image_path)
        assert out_img.height < src_img.height

    def test_process_one_with_debug_writes_debug_png(self, tmp_path: Path) -> None:
        """--debug causes process_one to also write <stem>_debug.png with OCR overlay."""
        args = parse_args(["dummy", "--output-dir", str(tmp_path), "--debug"])
        image_path = DOCS_DIR / "imagasfe.jpg"
        process_one(image_path, tmp_path, args)
        debug = tmp_path / "imagasfe_debug.png"
        assert debug.exists(), f"expected debug overlay not written: {debug}"

    def test_process_one_with_pad_writes_ig_canvas(self, tmp_path: Path) -> None:
        """--pad-to-instagram causes process_one to write <stem>_ig.jpg whose
        aspect ratio is the 4:5 portrait IG target."""
        args = parse_args(["dummy", "--output-dir", str(tmp_path), "--pad-to-instagram"])
        image_path = DOCS_DIR / "imagasfe.jpg"
        process_one(image_path, tmp_path, args)
        output = tmp_path / "imagasfe_ig.jpg"
        assert output.exists()
        out_img = Image.open(output)
        # 4:5 portrait => width / height == 0.8 (within rounding tolerance)
        aspect = out_img.width / out_img.height
        assert abs(aspect - 0.8) < 0.01, f"got aspect {aspect}, expected 0.8 (4:5)"

    def test_main_processes_argv_and_writes_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() reads sys.argv, processes both real screenshots, returns 0."""
        out = tmp_path / "out"
        monkeypatch.setattr(
            "sys.argv",
            [
                "tweet-cropper",
                str(DOCS_DIR / "imagasfe.jpg"),
                str(DOCS_DIR / "image.jpg"),
                "--output-dir",
                str(out),
            ],
        )
        rc = main()
        assert rc == 0
        assert (out / "imagasfe_cropped.jpg").exists()
        assert (out / "image_cropped.jpg").exists()
