"""Inspect and sanitise an uploaded ID card. Pure functions, no I/O.

Three jobs, in this order:

1. **Sniff the real format.** The declared `Content-Type` is whatever the client
   said; the magic bytes are what the file is. A `.jpg` that is actually an
   HTML error page must not reach the review queue.
2. **Strip metadata.** LLD-v1 §9.1: "strip EXIF GPS on the client **and**
   re-strip on the server (never trust the client)." A student photographs
   their ID at home; the EXIF carries where home is. Nobody reviewing entry to
   a dance show needs that, so it never lands in the bucket.
3. **Measure.** Width and height, to refuse the things that are obviously not a
   photograph of a card — a 64px thumbnail, a favicon, a stray screenshot.

Deliberately no Pillow. Reading two headers and dropping a few well-defined
segments is about sixty lines of stdlib; a native imaging dependency to do it
is a bigger surface than the problem.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

CONTENT_TYPE_JPEG = "image/jpeg"
CONTENT_TYPE_PNG = "image/png"

#: §9.1's ceiling. A phone photo that survives the client downscale is well
#: under this; anything over it is not a photograph of a card.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

#: A floor, not §9.1's 1000x600 client gate. With OCR cut there is no machine
#: that needs a particular resolution — there is a person who has to recognise
#: a face. This size refuses thumbnails and icons and leaves legibility to the
#: reviewer, who can ask for another photo. Turning away a real attendee at the
#: door over a 900px image would be the worse failure.
MIN_IMAGE_WIDTH = 640
MIN_IMAGE_HEIGHT = 400

# JPEG segments that carry only metadata. APP1 is EXIF (and XMP), APP13 is the
# Photoshop/IPTC block. APP0 (JFIF) stays: it carries density, not history.
_JPEG_METADATA_MARKERS = frozenset(
    {0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF}
)
# Start-of-frame markers carry the dimensions. C4 (Huffman table), C8 (JPG
# extension) and CC (arithmetic conditioning) share the range and do not.
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# PNG chunks that hold text or EXIF. Dropping a whole chunk leaves every other
# chunk's CRC untouched, which is why this is safe without re-encoding.
_PNG_METADATA_CHUNKS = frozenset({b"eXIf", b"tEXt", b"iTXt", b"zTXt"})


class UnsupportedImage(Exception):
    """The bytes are not a JPEG or PNG we can read. Carries an operator-facing
    reason, because this message is shown to the person who just uploaded."""


@dataclass(frozen=True)
class InspectedImage:
    content_type: str
    data: bytes
    width: int
    height: int
    sha256: str

    @property
    def bytes_len(self) -> int:
        return len(self.data)


def sniff_content_type(data: bytes) -> str:
    if data.startswith(JPEG_MAGIC):
        return CONTENT_TYPE_JPEG
    if data.startswith(PNG_MAGIC):
        return CONTENT_TYPE_PNG
    raise UnsupportedImage("That file isn’t a JPEG or PNG photo. Take a photo and try again.")


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Rebuild the JPEG without its APP1..APP15 segments.

    Walks the segment table rather than searching for byte patterns: an EXIF
    payload can contain anything, including something that looks like a marker.
    """
    if not data.startswith(JPEG_MAGIC):
        return data
    out = bytearray(data[:2])  # SOI
    index = 2
    end = len(data)
    while index + 4 <= end:
        if data[index] != 0xFF:
            break  # Not a segment boundary; hand the rest over untouched.
        marker = data[index + 1]
        if marker == 0xDA:  # Start of scan — entropy-coded data to the end.
            out += data[index:]
            return bytes(out)
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2 or index + 2 + length > end:
            break
        if marker not in _JPEG_METADATA_MARKERS:
            out += data[index : index + 2 + length]
        index += 2 + length
    else:
        return bytes(out)
    # Ran into something unparseable: keep what we have plus the remainder
    # rather than corrupting the file.
    out += data[index:]
    return bytes(out)


def strip_png_metadata(data: bytes) -> bytes:
    """Rebuild the PNG without its text and EXIF chunks."""
    if not data.startswith(PNG_MAGIC):
        return data
    out = bytearray(data[:8])
    index = 8
    end = len(data)
    while index + 8 <= end:
        length = int.from_bytes(data[index : index + 4], "big")
        chunk_type = data[index + 4 : index + 8]
        chunk_end = index + 12 + length  # length + type + data + crc
        if chunk_end > end:
            break
        if chunk_type not in _PNG_METADATA_CHUNKS:
            out += data[index:chunk_end]
        index = chunk_end
        if chunk_type == b"IEND":
            return bytes(out)
    out += data[index:]
    return bytes(out)


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    end = len(data)
    while index + 4 <= end:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xDA:
            return None
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in _JPEG_SOF_MARKERS:
            if index + 9 > end:
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if length < 2:
            return None
        index += 2 + length
    return None


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    # IHDR is always the first chunk: 8 signature + 4 length + 4 type.
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def inspect(raw: bytes) -> InspectedImage:
    """Validate, sanitise and measure. Raises `UnsupportedImage` with a message
    the uploader can act on.

    The SHA-256 is taken over the *stripped* bytes — the ones actually stored —
    so "this exact file was uploaded twice" means what it says even when two
    phones wrote different EXIF onto the same photograph.
    """
    if not raw:
        raise UnsupportedImage("That file was empty. Take a photo and try again.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UnsupportedImage(
            f"That photo is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB. "
            "Try again — your phone's normal photo quality is plenty."
        )

    content_type = sniff_content_type(raw)
    if content_type == CONTENT_TYPE_JPEG:
        data = strip_jpeg_metadata(raw)
        size = jpeg_dimensions(data)
    else:
        data = strip_png_metadata(raw)
        size = png_dimensions(data)

    if size is None:
        raise UnsupportedImage("That photo couldn’t be read. Take another one and try again.")
    width, height = size
    if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
        raise UnsupportedImage(
            f"That photo is too small ({width}×{height}). Fill the frame with your ID card."
        )

    return InspectedImage(
        content_type=content_type,
        data=data,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )
