import io
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server
from library import album_id, cover_info


def make_png_header(width, height):
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack(">II", width, height) + b'\x08\x02\x00\x00\x00'
    ihdr_length = struct.pack(">I", 13)
    ihdr_crc = b'\x00\x00\x00\x00'
    return signature + ihdr_length + b'IHDR' + ihdr_data + ihdr_crc


def make_jpeg_with_sof(width, height, sof_marker=0xC0):
    soi = b'\xff\xd8'
    app0 = (
        b'\xff\xe0'
        + struct.pack(">H", 16)
        + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    )
    seg_len = 11
    sof = (
        bytes([0xFF, sof_marker])
        + struct.pack(">HBH", seg_len, 8, height)
        + struct.pack(">H", width)
        + b'\x03\x01\x11\x00'
    )
    return soi + app0 + sof


@pytest.fixture
def tiny_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color="red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    server.albums.clear()
    server._scan_done.set()
    with server.app.test_client() as c:
        yield c
    server.albums.clear()


@pytest.fixture
def populated_client(client, tmp_path):
    album_dir = tmp_path / "Pink Floyd - DSOTM"
    album_dir.mkdir()
    media_dir = album_dir / ".media"
    media_dir.mkdir()

    cover = album_dir / "cover.jpg"
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), color="blue").save(buf, format="JPEG")
    cover.write_bytes(buf.getvalue())

    aid = album_id(album_dir)
    info = cover_info(cover)
    server.albums[aid] = {
        "id": aid,
        "path": album_dir,
        "name": "Pink Floyd - DSOTM",
        "artist": "Pink Floyd",
        "album_name": "DSOTM",
        "mbid": "76df3287-6cda-33eb-8e9a-044b5e15ffdd",
        "cover_path": cover,
        **info,
    }
    return client, aid, album_dir
