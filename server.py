#!/usr/bin/env python3
"""Web app for browsing album cover art and finding higher-resolution replacements."""

import argparse
import io
import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort
from PIL import Image

from fetch_cover_art import (
    read_mbid_from_file,
    first_music_file,
    fetch_release_info,
    fetch_release_metadata,
    fetch_cover_art_listing,
    fetch_bytes,
    ext_from_url,
    get as caa_get,
    MUSIC_EXTENSIONS,
    USER_AGENT,
    FetchError,
)
from library import _album_id, _find_cover, _cover_info, _parse_artist_album, scan_library
from probing import _detect_duplicates
from sources import fetch_sources, _search_itunes, _search_discogs, _search_caa
import sources as _sources_mod

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MUSIC_DIR: Path | None = None
DISCOGS_TOKEN: str | None = os.environ.get("DISCOGS_TOKEN")

# ---------------------------------------------------------------------------
# In-memory album index
# ---------------------------------------------------------------------------

albums: dict[str, dict] = {}
_albums_lock = threading.Lock()
_scan_done = threading.Event()

# MusicBrainz rate-limit guard (max 1 req/sec)
_mb_lock = threading.Lock()
_mb_last_call = 0.0


def _rate_limited_mb(fn, *args, **kwargs):
    global _mb_last_call
    with _mb_lock:
        elapsed = time.time() - _mb_last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _mb_last_call = time.time()
    return fn(*args, **kwargs)


_sources_mod.init(_rate_limited_mb)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/albums")
def api_albums():
    if not _scan_done.is_set():
        return jsonify({"scanning": True, "albums": []})
    with _albums_lock:
        album_list = []
        for a in sorted(albums.values(), key=lambda x: x["name"].lower()):
            album_list.append({
                "id": a["id"],
                "name": a["name"],
                "artist": a["artist"],
                "album_name": a["album_name"],
                "mbid": a["mbid"],
                "has_cover": a["has_cover"],
                "cover_size_kb": a["cover_size_kb"],
                "cover_width": a["cover_width"],
                "cover_height": a["cover_height"],
            })
    return jsonify({"scanning": False, "albums": album_list})


@app.route("/api/albums/<album_id>/cover")
def api_album_cover(album_id):
    with _albums_lock:
        album = albums.get(album_id)
    if not album or not album.get("cover_path") or not album["cover_path"].exists():
        abort(404)
    return send_file(album["cover_path"])


@app.route("/api/albums/<album_id>/sources")
def api_album_sources(album_id):
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    sources = fetch_sources(album)
    return jsonify({"sources": sources})


@app.route("/api/mbid/<mbid>/sources")
def api_mbid_sources(mbid):
    if not _UUID_RE.fullmatch(mbid):
        return jsonify({"error": "Invalid MBID format"}), 400
    try:
        release = _rate_limited_mb(fetch_release_metadata, mbid)
    except FetchError:
        return jsonify({"error": "Release not found in MusicBrainz."}), 404
    synthetic_album = {
        "id": mbid,
        "name": f"{release['artist']} - {release['album']}",
        "mbid": mbid,
        "cover_size_kb": 0,
        "cover_width": 0,
        "cover_height": 0,
    }
    sources = fetch_sources(synthetic_album, artist=release["artist"], album_name=release["album"])
    return jsonify({"sources": sources, "release": release})


@app.route("/api/albums/<album_id>/replace", methods=["POST"])
def api_album_replace(album_id):
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400

    url = data["url"]
    album_dir = album["path"]

    try:
        img_data = fetch_bytes(url)
    except Exception as e:
        return jsonify({"error": f"Failed to download: {e}"}), 502

    try:
        img = Image.open(io.BytesIO(img_data))
        img.verify()
        img = Image.open(io.BytesIO(img_data))
        w, h = img.size
    except Exception:
        return jsonify({"error": "Downloaded data is not a valid image"}), 400

    ext = ext_from_url(url)
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"

    for old_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        old = album_dir / f"cover{old_ext}"
        if old.exists():
            old.unlink()
        old_thumb = album_dir / f"thumbnail{old_ext}"
        if old_thumb.exists():
            old_thumb.unlink()

    cover_path = album_dir / f"cover{ext}"
    cover_path.write_bytes(img_data)

    try:
        thumb = img.copy()
        thumb.thumbnail((250, 250), Image.LANCZOS)
        thumb_path = album_dir / f"thumbnail{ext}"
        if ext in (".jpg", ".jpeg"):
            thumb.save(thumb_path, "JPEG", quality=85)
        else:
            thumb.save(thumb_path)
    except Exception:
        pass

    size_kb = round(len(img_data) / 1024, 1)
    with _albums_lock:
        albums[album_id]["cover_path"] = cover_path
        albums[album_id]["has_cover"] = True
        albums[album_id]["cover_size_kb"] = size_kb
        albums[album_id]["cover_width"] = w
        albums[album_id]["cover_height"] = h

    return jsonify({
        "ok": True,
        "cover_size_kb": size_kb,
        "cover_width": w,
        "cover_height": h,
    })


@app.route("/api/albums/<album_id>/media")
def api_album_media(album_id):
    """List existing files in the album's .media/ directory."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    media_dir = album["path"] / ".media"
    files = []
    if media_dir.is_dir():
        for f in sorted(media_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                try:
                    size_kb = round(f.stat().st_size / 1024, 1)
                    with Image.open(f) as img:
                        w, h = img.size
                except Exception:
                    size_kb, w, h = 0, 0, 0
                files.append({
                    "filename": f.name,
                    "size_kb": size_kb,
                    "width": w,
                    "height": h,
                })
    return jsonify({"files": files})


@app.route("/api/albums/<album_id>/media/<path:filename>")
def api_album_media_file(album_id, filename):
    """Serve an image from the album's .media/ directory."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    media_path = album["path"] / ".media" / filename
    if not media_path.exists() or not media_path.is_file():
        abort(404)
    try:
        media_path.resolve().relative_to((album["path"] / ".media").resolve())
    except ValueError:
        abort(403)
    return send_file(media_path)


@app.route("/api/albums/<album_id>/save-media", methods=["POST"])
def api_album_save_media(album_id):
    """Download an image and save it to the album's .media/ directory."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400

    url = data["url"]
    img_type = data.get("type", "Art").strip()
    safe_type = re.sub(r'[<>:"/\\|?*]', '', img_type.replace("/", "-").replace(" ", "_"))
    if not safe_type:
        safe_type = "Art"

    album_dir = album["path"]
    media_dir = album_dir / ".media"
    media_dir.mkdir(exist_ok=True)

    try:
        img_data = fetch_bytes(url)
    except Exception as e:
        return jsonify({"error": f"Failed to download: {e}"}), 502

    try:
        img = Image.open(io.BytesIO(img_data))
        img.verify()
        img = Image.open(io.BytesIO(img_data))
        w, h = img.size
    except Exception:
        return jsonify({"error": "Downloaded data is not a valid image"}), 400

    ext = ext_from_url(url)
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"

    counter = 1
    while True:
        filename = f"{safe_type}-{counter:03d}{ext}"
        dest = media_dir / filename
        if not dest.exists():
            break
        counter += 1

    dest.write_bytes(img_data)
    size_kb = round(len(img_data) / 1024, 1)

    return jsonify({
        "ok": True,
        "filename": filename,
        "size_kb": size_kb,
        "width": w,
        "height": h,
    })


@app.route("/api/albums/<album_id>/media/<path:filename>", methods=["DELETE"])
def api_album_delete_media(album_id, filename):
    """Delete a file from the album's .media/ directory."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    media_dir = (album["path"] / ".media").resolve()
    media_path = (album["path"] / ".media" / filename).resolve()
    try:
        media_path.relative_to(media_dir)
    except ValueError:
        abort(403)
    if not media_path.exists() or not media_path.is_file():
        abort(404)
    media_path.unlink()
    return jsonify({"ok": True})


@app.route("/api/albums/<album_id>/use-media", methods=["POST"])
def api_album_use_media(album_id):
    """Swap a .media/ file in as the cover, archiving the current cover to .media/."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)
    data = request.get_json()
    if not data or "filename" not in data:
        return jsonify({"error": "Missing 'filename' in request body"}), 400

    album_dir = album["path"]
    media_dir = album_dir / ".media"
    media_path = (media_dir / data["filename"]).resolve()

    try:
        media_path.relative_to(media_dir.resolve())
    except ValueError:
        abort(403)
    if not media_path.exists() or not media_path.is_file():
        abort(404)

    try:
        img = Image.open(media_path)
        img.verify()
        img = Image.open(media_path)
        w, h = img.size
    except Exception:
        return jsonify({"error": "File is not a valid image"}), 400

    current_cover = _find_cover(album_dir)
    if current_cover:
        media_dir.mkdir(exist_ok=True)
        cover_ext = current_cover.suffix
        counter = 1
        while True:
            backup_name = f"Cover-{counter:03d}{cover_ext}"
            if not (media_dir / backup_name).exists():
                break
            counter += 1
        current_cover.rename(media_dir / backup_name)
        for old_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            thumb = album_dir / f"thumbnail{old_ext}"
            if thumb.exists():
                thumb.unlink()

    new_ext = media_path.suffix
    new_cover = album_dir / f"cover{new_ext}"
    img_data = media_path.read_bytes()
    media_path.rename(new_cover)

    size_kb = round(len(img_data) / 1024, 1)

    try:
        thumb_img = Image.open(new_cover)
        thumb_img.thumbnail((250, 250), Image.LANCZOS)
        thumb_path = album_dir / f"thumbnail{new_ext}"
        if new_ext in (".jpg", ".jpeg"):
            thumb_img.save(thumb_path, "JPEG", quality=85)
        else:
            thumb_img.save(thumb_path)
    except Exception:
        pass

    with _albums_lock:
        albums[album_id]["cover_path"] = new_cover
        albums[album_id]["has_cover"] = True
        albums[album_id]["cover_size_kb"] = size_kb
        albums[album_id]["cover_width"] = w
        albums[album_id]["cover_height"] = h

    return jsonify({
        "ok": True,
        "cover_size_kb": size_kb,
        "cover_width": w,
        "cover_height": h,
    })


@app.route("/api/albums/<album_id>/mb-releases")
def api_mb_releases(album_id):
    """Search MusicBrainz for releases matching this album's artist and title."""
    with _albums_lock:
        album = albums.get(album_id)
    if not album:
        abort(404)

    artist = album.get("artist", "")
    album_name = album.get("album_name", "") or album["name"]

    if not artist and not album_name:
        return jsonify({"releases": []})

    parts = []
    if album_name:
        parts.append(f'release:"{album_name}"')
    if artist:
        parts.append(f'artistname:"{artist}"')
    query = " AND ".join(parts)

    url = (
        f"https://musicbrainz.org/ws/2/release"
        f"?query={urllib.parse.quote(query)}&fmt=json&limit=12"
    )

    try:
        data = _rate_limited_mb(caa_get, url)
    except Exception:
        return jsonify({"releases": []})

    releases = []
    for r in data.get("releases", []):
        artist_credit = "".join(
            (ac.get("name") or ac.get("artist", {}).get("name", "")) + ac.get("joinphrase", "")
            for ac in r.get("artist-credit", [])
            if isinstance(ac, dict)
        ).strip()
        label_infos = r.get("label-info", [])
        label = ""
        if label_infos and isinstance(label_infos[0], dict):
            lbl = label_infos[0].get("label")
            if lbl:
                label = lbl.get("name", "")
        release_group = r.get("release-group") or {}
        releases.append({
            "id": r["id"],
            "title": r.get("title", ""),
            "artist": artist_credit,
            "date": (r.get("date") or "")[:4],
            "country": r.get("country", ""),
            "label": label,
            "type": release_group.get("primary-type", ""),
            "score": r.get("score", 0),
        })

    return jsonify({"releases": releases})


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    if MUSIC_DIR is None:
        return jsonify({"error": "No music directory configured"}), 500
    _scan_done.clear()
    result = scan_library(MUSIC_DIR)
    with _albums_lock:
        albums.clear()
        albums.update(result)
    _scan_done.set()
    return jsonify({"ok": True, "count": len(albums)})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _background_scan():
    global albums
    result = scan_library(MUSIC_DIR)
    with _albums_lock:
        albums.update(result)
    _scan_done.set()
    print(f"Scan complete: {len(result)} album(s) found.")


def main():
    global MUSIC_DIR

    parser = argparse.ArgumentParser(description="Cover Art Browser")
    parser.add_argument("-d", "--dir", metavar="PATH", default=os.environ.get("MUSIC_DIR", "."),
                        help="path to music library (default: MUSIC_DIR env or current dir)")
    parser.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", "8080")),
                        help="server port (default: 8080)")
    args = parser.parse_args()

    MUSIC_DIR = Path(args.dir).resolve()
    if not MUSIC_DIR.is_dir():
        print(f"Error: '{args.dir}' is not a directory.")
        raise SystemExit(1)

    print(f"Music directory: {MUSIC_DIR}")
    if DISCOGS_TOKEN:
        print("Discogs integration: enabled")
    else:
        print("Discogs integration: disabled (set DISCOGS_TOKEN to enable)")

    print("Scanning library in background...")
    scan_thread = threading.Thread(target=_background_scan, daemon=True)
    scan_thread.start()

    print(f"Starting server on http://localhost:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
