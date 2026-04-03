import io
import struct

import pytest
from PIL import Image

from conftest import make_jpeg_with_sof, make_png_header
from server import _read_jpeg_dimensions, _read_png_dimensions


class TestPngDimensions:
    def test_standard_square(self):
        assert _read_png_dimensions(make_png_header(1200, 1200)) == (1200, 1200)

    def test_non_square(self):
        assert _read_png_dimensions(make_png_header(800, 600)) == (800, 600)

    def test_large_dimensions(self):
        assert _read_png_dimensions(make_png_header(6000, 4000)) == (6000, 4000)

    def test_too_short_returns_none(self):
        assert _read_png_dimensions(b'\x89PNG\r\n\x1a\n\x00') is None

    def test_wrong_signature_returns_none(self):
        assert _read_png_dimensions(make_jpeg_with_sof(100, 100)) is None


class TestJpegDimensions:
    def test_sof0_baseline(self):
        assert _read_jpeg_dimensions(make_jpeg_with_sof(1200, 1200, 0xC0)) == (1200, 1200)

    def test_sof2_progressive(self):
        assert _read_jpeg_dimensions(make_jpeg_with_sof(800, 600, 0xC2)) == (800, 600)

    @pytest.mark.parametrize("marker", [
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    ])
    def test_all_sof_markers(self, marker):
        assert _read_jpeg_dimensions(make_jpeg_with_sof(640, 480, marker)) == (640, 480)

    def test_non_square(self):
        assert _read_jpeg_dimensions(make_jpeg_with_sof(1920, 1080)) == (1920, 1080)

    def test_too_short_returns_none(self):
        assert _read_jpeg_dimensions(b'\xff\xd8\xff') is None

    def test_wrong_signature_returns_none(self):
        assert _read_jpeg_dimensions(make_png_header(100, 100)) is None

    def test_no_sof_marker_returns_none(self):
        soi = b'\xff\xd8'
        # DQT marker (0xDB) — not an SOF marker
        dqt = b'\xff\xdb' + struct.pack(">H", 4) + b'\x00\x00'
        assert _read_jpeg_dimensions(soi + dqt) is None


class TestCrossFormat:
    def test_real_jpeg_from_pillow(self):
        img = Image.new("RGB", (320, 240), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        assert _read_jpeg_dimensions(buf.getvalue()) == (320, 240)

    def test_real_png_from_pillow(self):
        img = Image.new("RGB", (320, 240), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        assert _read_png_dimensions(buf.getvalue()) == (320, 240)

    def test_empty_bytes_returns_none(self):
        assert _read_jpeg_dimensions(b"") is None
        assert _read_png_dimensions(b"") is None
