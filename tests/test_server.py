import io
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from PIL import Image

import server
from fetch_cover_art import FetchError


# ---- GET /api/albums ----


def test_albums_empty(client):
    resp = client.get("/api/albums")
    data = resp.get_json()
    assert data == {"scanning": False, "albums": []}


def test_albums_scanning_state(client):
    server._scan_done.clear()
    resp = client.get("/api/albums")
    data = resp.get_json()
    assert data == {"scanning": True, "albums": []}
    server._scan_done.set()


def test_albums_populated(populated_client):
    client, album_id, _ = populated_client
    resp = client.get("/api/albums")
    data = resp.get_json()
    assert data["scanning"] is False
    assert len(data["albums"]) == 1
    assert data["albums"][0]["id"] == album_id
    assert data["albums"][0]["artist"] == "Pink Floyd"


def test_albums_sorted_case_insensitive(client, tmp_path):
    for name in ["Zeppelin - II", "abba - Gold", "Beatles - Abbey"]:
        d = tmp_path / name
        d.mkdir()
        aid = server.album_id(d)
        server.albums[aid] = {
            "id": aid,
            "path": d,
            "name": name,
            "artist": name.split(" - ")[0],
            "album_name": name.split(" - ")[1],
            "mbid": None,
            "cover_path": None,
            "has_cover": False,
            "cover_size_kb": 0,
            "cover_width": 0,
            "cover_height": 0,
        }
    resp = client.get("/api/albums")
    names = [a["name"] for a in resp.get_json()["albums"]]
    assert names == ["abba - Gold", "Beatles - Abbey", "Zeppelin - II"]


# ---- GET /api/albums (additional) ----


def test_albums_response_includes_all_fields(populated_client):
    client, album_id, _ = populated_client
    resp = client.get("/api/albums")
    album = resp.get_json()["albums"][0]
    for key in ("id", "name", "artist", "album_name", "mbid",
                "has_cover", "cover_size_kb", "cover_width", "cover_height"):
        assert key in album


# ---- GET /api/albums/<id>/cover ----


def test_album_cover_serves_image(populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/cover")
    assert resp.status_code == 200
    assert resp.content_type.startswith("image/")


def test_album_cover_unknown_album(client):
    resp = client.get("/api/albums/nonexistent/cover")
    assert resp.status_code == 404


def test_album_cover_no_cover_path(client, tmp_path):
    d = tmp_path / "empty-album"
    d.mkdir()
    aid = server.album_id(d)
    server.albums[aid] = {
        "id": aid,
        "path": d,
        "name": "empty-album",
        "artist": "",
        "album_name": "empty-album",
        "mbid": None,
        "cover_path": None,
        "has_cover": False,
        "cover_size_kb": 0,
        "cover_width": 0,
        "cover_height": 0,
    }
    resp = client.get(f"/api/albums/{aid}/cover")
    assert resp.status_code == 404


# ---- GET /api/albums/<id>/media/<filename> ----


def test_media_file_serves_valid(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    media_file = album_dir / ".media" / "Front-001.jpg"
    media_file.write_bytes(tiny_jpeg_bytes)
    resp = client.get(f"/api/albums/{album_id}/media/Front-001.jpg")
    assert resp.status_code == 200
    assert resp.content_type.startswith("image/")


def test_media_file_unknown_album(client):
    resp = client.get("/api/albums/nonexistent/media/file.jpg")
    assert resp.status_code == 404


def test_media_file_nonexistent(populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/media/nope.jpg")
    assert resp.status_code == 404


def test_media_file_path_traversal_dotdot(populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/media/../cover.jpg")
    assert resp.status_code in (403, 404)


def test_media_file_path_traversal_encoded_slash(populated_client):
    client, album_id, album_dir = populated_client
    resp = client.get(f"/api/albums/{album_id}/media/..%2Fcover.jpg")
    assert resp.status_code in (403, 404)
    assert resp.data != (album_dir / "cover.jpg").read_bytes()


def test_media_file_absolute_path(populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/media//etc/passwd", follow_redirects=True)
    assert resp.status_code in (403, 404)


def test_media_file_nested_traversal(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    subdir = album_dir / ".media" / "subdir"
    subdir.mkdir()
    secret = album_dir / "secret.txt"
    secret.write_bytes(b"secret")
    resp = client.get(f"/api/albums/{album_id}/media/subdir/../../secret.txt")
    assert resp.status_code in (403, 404)


# ---- DELETE /api/albums/<id>/media/<filename> ----


def test_delete_media_success(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    target = album_dir / ".media" / "Back-001.jpg"
    target.write_bytes(tiny_jpeg_bytes)
    assert target.exists()
    resp = client.delete(f"/api/albums/{album_id}/media/Back-001.jpg")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert not target.exists()


def test_delete_media_unknown_album(client):
    resp = client.delete("/api/albums/nonexistent/media/file.jpg")
    assert resp.status_code == 404


def test_delete_media_nonexistent_file(populated_client):
    client, album_id, _ = populated_client
    resp = client.delete(f"/api/albums/{album_id}/media/nope.jpg")
    assert resp.status_code == 404


def test_delete_media_path_traversal(populated_client):
    client, album_id, _ = populated_client
    resp = client.delete(f"/api/albums/{album_id}/media/../cover.jpg")
    assert resp.status_code in (403, 404)


def test_delete_media_deeper_traversal(populated_client):
    client, album_id, album_dir = populated_client
    secret = album_dir / "secret.txt"
    secret.write_bytes(b"do not delete")
    resp = client.delete(f"/api/albums/{album_id}/media/a/../../secret.txt")
    assert resp.status_code in (403, 404)
    assert secret.exists()


def test_delete_media_file_actually_removed(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    target = album_dir / ".media" / "Remove-001.jpg"
    target.write_bytes(tiny_jpeg_bytes)
    client.delete(f"/api/albums/{album_id}/media/Remove-001.jpg")
    assert not target.exists()


# ---- GET /api/mbid/<mbid>/sources ----


@patch("server.fetch_sources", return_value=[])
@patch("server._rate_limited_mb")
def test_mbid_sources_valid(mock_mb, mock_fs, client):
    mock_mb.return_value = {"artist": "Pink Floyd", "album": "DSOTM"}
    resp = client.get("/api/mbid/76df3287-6cda-33eb-8e9a-044b5e15ffdd/sources")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sources" in data
    assert "release" in data


def test_mbid_sources_invalid_format(client):
    resp = client.get("/api/mbid/not-a-uuid/sources")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


@patch("server.fetch_sources", return_value=[])
@patch("server._rate_limited_mb")
def test_mbid_sources_uppercase_accepted(mock_mb, mock_fs, client):
    mock_mb.return_value = {"artist": "Pink Floyd", "album": "DSOTM"}
    resp = client.get("/api/mbid/76DF3287-6CDA-33EB-8E9A-044B5E15FFDD/sources")
    assert resp.status_code == 200


def test_mbid_sources_partial_uuid(client):
    resp = client.get("/api/mbid/76df3287-6cda/sources")
    assert resp.status_code == 400


@patch("server._rate_limited_mb", side_effect=FetchError("not found"))
def test_mbid_sources_not_found(mock_mb, client):
    resp = client.get("/api/mbid/76df3287-6cda-33eb-8e9a-044b5e15ffdd/sources")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


# ---- POST /api/albums/<id>/replace ----


@patch("server.fetch_bytes")
def test_replace_success(mock_fetch, populated_client, tiny_jpeg_bytes):
    mock_fetch.return_value = tiny_jpeg_bytes
    client, album_id, album_dir = populated_client
    resp = client.post(
        f"/api/albums/{album_id}/replace",
        json={"url": "https://example.com/image.jpg"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert (album_dir / "cover.jpg").exists()


def test_replace_missing_url(populated_client):
    client, album_id, _ = populated_client
    resp = client.post(f"/api/albums/{album_id}/replace", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


@patch("server.fetch_bytes", side_effect=Exception("network error"))
def test_replace_download_failure(mock_fetch, populated_client):
    client, album_id, _ = populated_client
    resp = client.post(
        f"/api/albums/{album_id}/replace",
        json={"url": "https://example.com/image.jpg"},
    )
    assert resp.status_code == 502


@patch("server.fetch_bytes", return_value=b"not an image")
def test_replace_invalid_image(mock_fetch, populated_client):
    client, album_id, _ = populated_client
    resp = client.post(
        f"/api/albums/{album_id}/replace",
        json={"url": "https://example.com/image.jpg"},
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.get_json()["error"]


@patch("server.fetch_bytes")
def test_replace_removes_old_cover(mock_fetch, populated_client, tiny_jpeg_bytes):
    mock_fetch.return_value = tiny_jpeg_bytes
    client, album_id, album_dir = populated_client
    old_png = album_dir / "cover.png"
    old_png.write_bytes(b"fake")
    client.post(
        f"/api/albums/{album_id}/replace",
        json={"url": "https://example.com/new.jpg"},
    )
    assert not old_png.exists()


# ---- POST /api/albums/<id>/use-media ----


def test_use_media_success(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    media_file = album_dir / ".media" / "Front-001.jpg"
    media_file.write_bytes(tiny_jpeg_bytes)
    resp = client.post(
        f"/api/albums/{album_id}/use-media",
        json={"filename": "Front-001.jpg"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert (album_dir / "cover.jpg").exists()


def test_use_media_archives_old_cover(populated_client, tiny_jpeg_bytes):
    client, album_id, album_dir = populated_client
    old_cover_data = (album_dir / "cover.jpg").read_bytes()
    media_file = album_dir / ".media" / "Front-001.jpg"
    media_file.write_bytes(tiny_jpeg_bytes)
    client.post(
        f"/api/albums/{album_id}/use-media",
        json={"filename": "Front-001.jpg"},
    )
    archived = album_dir / ".media" / "Cover-001.jpg"
    assert archived.exists()
    assert archived.read_bytes() == old_cover_data


def test_use_media_path_traversal(populated_client):
    client, album_id, _ = populated_client
    resp = client.post(
        f"/api/albums/{album_id}/use-media",
        json={"filename": "../cover.jpg"},
    )
    assert resp.status_code == 403


def test_use_media_nonexistent_file(populated_client):
    client, album_id, _ = populated_client
    resp = client.post(
        f"/api/albums/{album_id}/use-media",
        json={"filename": "nonexistent.jpg"},
    )
    assert resp.status_code == 404


def test_use_media_invalid_image(populated_client):
    client, album_id, album_dir = populated_client
    bad_file = album_dir / ".media" / "bad.jpg"
    bad_file.write_bytes(b"not image data at all")
    resp = client.post(
        f"/api/albums/{album_id}/use-media",
        json={"filename": "bad.jpg"},
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.get_json()["error"]


# ---- POST /api/albums/<id>/replace (additional) ----


def test_replace_unknown_album(client):
    resp = client.post("/api/albums/nonexistent/replace", json={"url": "https://x.com/i.jpg"})
    assert resp.status_code == 404


@patch("server.fetch_bytes")
def test_replace_updates_album_cache(mock_fetch, populated_client, tiny_jpeg_bytes):
    mock_fetch.return_value = tiny_jpeg_bytes
    client, album_id, _ = populated_client
    client.post(
        f"/api/albums/{album_id}/replace",
        json={"url": "https://example.com/image.jpg"},
    )
    assert server.albums[album_id]["has_cover"] is True
    assert server.albums[album_id]["cover_width"] == 1
    assert server.albums[album_id]["cover_height"] == 1


# ---- POST /api/albums/<id>/use-media (additional) ----


def test_use_media_unknown_album(client):
    resp = client.post("/api/albums/nonexistent/use-media", json={"filename": "x.jpg"})
    assert resp.status_code == 404


# ---- POST /api/rescan ----


@patch("server.scan_library")
def test_rescan_success(mock_scan, client, tmp_path):
    server.MUSIC_DIR = tmp_path
    mock_scan.return_value = {"abc": {"id": "abc", "name": "test"}}
    resp = client.post("/api/rescan")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 1


def test_rescan_no_music_dir(client):
    original = server.MUSIC_DIR
    server.MUSIC_DIR = None
    resp = client.post("/api/rescan")
    assert resp.status_code == 500
    assert "error" in resp.get_json()
    server.MUSIC_DIR = original


# ---- GET /api/albums/<id>/sources ----


@patch("server.fetch_sources", return_value=[{"source": "CAA", "images": []}])
def test_album_sources_success(mock_fs, populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/sources")
    assert resp.status_code == 200
    assert "sources" in resp.get_json()


def test_album_sources_unknown_album(client):
    resp = client.get("/api/albums/nonexistent/sources")
    assert resp.status_code == 404


@patch("server.fetch_sources", return_value=[{"source": "CAA", "images": []}])
def test_album_sources_response_shape(mock_fs, populated_client):
    client, album_id, _ = populated_client
    resp = client.get(f"/api/albums/{album_id}/sources")
    data = resp.get_json()
    assert "sources" in data
    assert isinstance(data["sources"], list)


# ---- GET / ----


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()
