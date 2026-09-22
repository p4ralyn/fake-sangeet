"""Synthetic JPEG and PNG builders for the M4 tests.

Real photographs are not checked into the repo — an ID card fixture would be
either somebody's actual ID or a meaningless blob. These build the smallest
files that are genuinely well formed for the parts `services/verification/
images.py` reads: the magic bytes, the dimension headers, and the metadata
segments it is supposed to strip.

Not a decoder test. It is a parser test, and these are the parser's input.
"""
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png_bytes(width: int = 640, height: int = 400, *, text: bool = False) -> bytes:
    """A valid 8-bit RGB PNG. `text=True` adds a tEXt chunk to strip."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    out = PNG_SIGNATURE + _png_chunk(b"IHDR", header)
    if text:
        out += _png_chunk(b"tEXt", b"Comment\x0038.9 N, 77.0 W")
    scanlines = b"".join(b"\x00" + b"\x7f" * (width * 3) for _ in range(height))
    out += _png_chunk(b"IDAT", zlib.compress(scanlines, 1))
    return out + _png_chunk(b"IEND", b"")


def jpeg_bytes(width: int = 640, height: int = 400, *, exif: bool = True) -> bytes:
    """A JPEG with a JFIF APP0, an optional EXIF APP1, a real SOF0 carrying the
    dimensions, and a token scan segment."""
    out = bytearray(b"\xff\xd8")  # SOI

    jfif = b"JFIF\x00" + bytes([1, 1, 0, 0, 1, 0, 1, 0, 0])
    out += b"\xff\xe0" + struct.pack(">H", len(jfif) + 2) + jfif

    if exif:
        # The payload's contents do not matter — what matters is that the
        # segment is found by walking the table and removed whole.
        payload = b"Exif\x00\x00" + b"GPSLatitude=28.5449;GPSLongitude=77.5806"
        out += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    sof = bytes([8]) + struct.pack(">HH", height, width) + bytes([3])
    for component in (1, 2, 3):
        sof += bytes([component, 0x11, 0])
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof

    sos = bytes([3, 1, 0, 2, 0, 3, 0, 0, 63, 0])
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    out += b"\x00" * 24  # entropy-coded data stand-in
    return bytes(out + b"\xff\xd9")  # EOI
