"""Image inspection and sanitisation (M4).

The one that matters is EXIF stripping. A student photographs their ID at home
and the file carries where home is; LLD-v1 §9.1 says strip it on the client AND
again on the server, because the client's word is not a control.
"""
import pytest

from app.services.verification.images import (
    CONTENT_TYPE_JPEG,
    CONTENT_TYPE_PNG,
    MAX_UPLOAD_BYTES,
    MIN_IMAGE_HEIGHT,
    MIN_IMAGE_WIDTH,
    UnsupportedImage,
    inspect,
    jpeg_dimensions,
    png_dimensions,
    sniff_content_type,
    strip_jpeg_metadata,
    strip_png_metadata,
)
from tests.images import jpeg_bytes, png_bytes


# ── format sniffing ──────────────────────────────────────────────────────────
def test_the_declared_type_is_ignored_in_favour_of_the_magic_bytes():
    assert sniff_content_type(jpeg_bytes()) == CONTENT_TYPE_JPEG
    assert sniff_content_type(png_bytes()) == CONTENT_TYPE_PNG


@pytest.mark.parametrize(
    "payload",
    [
        b"<!doctype html><html>not an image</html>",
        b"GIF89a" + b"\x00" * 64,
        b"%PDF-1.7\n",
        b"",
    ],
)
def test_anything_that_is_not_a_jpeg_or_png_is_refused(payload):
    with pytest.raises(UnsupportedImage):
        inspect(payload)


# ── metadata stripping ───────────────────────────────────────────────────────
def test_jpeg_exif_is_removed_and_the_image_still_parses():
    raw = jpeg_bytes(exif=True)
    assert b"GPSLatitude" in raw  # the fixture really does carry location

    stripped = strip_jpeg_metadata(raw)

    assert b"GPSLatitude" not in stripped
    assert b"Exif\x00\x00" not in stripped
    # JFIF (APP0) survives: it carries density, not history.
    assert b"JFIF\x00" in stripped
    # Still a readable JPEG afterwards — stripping must not corrupt the file.
    assert stripped.startswith(b"\xff\xd8")
    assert stripped.endswith(b"\xff\xd9")
    assert jpeg_dimensions(stripped) == (640, 400)


def test_stripping_a_jpeg_with_no_exif_changes_nothing():
    raw = jpeg_bytes(exif=False)
    assert strip_jpeg_metadata(raw) == raw


def test_png_text_chunks_are_removed_and_the_image_still_parses():
    raw = png_bytes(text=True)
    assert b"38.9 N" in raw

    stripped = strip_png_metadata(raw)

    assert b"38.9 N" not in stripped
    assert b"tEXt" not in stripped
    assert stripped.startswith(b"\x89PNG\r\n\x1a\n")
    assert stripped.endswith(b"IEND\xae\x42\x60\x82")
    assert png_dimensions(stripped) == (640, 400)


def test_inspect_strips_before_hashing_so_the_same_photo_hashes_the_same():
    """Two phones write different EXIF onto one photograph. The duplicate flag
    is about the picture, not about the metadata wrapped around it."""
    with_exif = inspect(jpeg_bytes(exif=True))
    without = inspect(jpeg_bytes(exif=False))
    assert with_exif.sha256 == without.sha256
    assert b"GPSLatitude" not in with_exif.data


# ── dimensions and limits ────────────────────────────────────────────────────
@pytest.mark.parametrize("builder", [jpeg_bytes, png_bytes])
def test_dimensions_are_read_off_the_header(builder):
    image = inspect(builder(1024, 768))
    assert (image.width, image.height) == (1024, 768)


@pytest.mark.parametrize("builder", [jpeg_bytes, png_bytes])
def test_a_thumbnail_is_refused_with_its_actual_size_in_the_message(builder):
    with pytest.raises(UnsupportedImage) as refused:
        inspect(builder(MIN_IMAGE_WIDTH - 1, MIN_IMAGE_HEIGHT - 1))
    assert "too small" in str(refused.value)
    assert str(MIN_IMAGE_WIDTH - 1) in str(refused.value)


def test_a_file_over_the_ceiling_is_refused_before_anything_is_parsed():
    with pytest.raises(UnsupportedImage) as refused:
        inspect(b"\xff\xd8\xff" + b"\x00" * MAX_UPLOAD_BYTES)
    assert "8MB" in str(refused.value)


def test_a_truncated_jpeg_is_refused_rather_than_guessed_at():
    """No SOF marker means no dimensions. Better to ask for another photo than
    to store something nobody can open."""
    with pytest.raises(UnsupportedImage):
        inspect(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
