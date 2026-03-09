#!/usr/bin/env python3
"""Web app for browsing album cover art and finding higher-resolution replacements."""

import argparse
import hashlib
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort
from PIL import Image

from fetch_cover_art import (
    read_mbid_from_file,
    first_music_file,
    fetch_release_info,
    fetch_cover_art_listing,
    fetch_bytes,
    ext_from_url,
    get as caa_get,
    MUSIC_EXTENSIONS,
    USER_AGENT,
    FetchError,
)

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


def _album_id(path: Path) -> str:
    return hashlib.md5(str(path).encode()).hexdigest()[:12]


def _find_cover(album_dir: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        p = album_dir / f"cover{ext}"
        if p.exists():
            return p
    return None


def _cover_info(cover_path: Path | None) -> dict:
    if cover_path is None or not cover_path.exists():
        return {"has_cover": False, "cover_size_kb": 0, "cover_width": 0, "cover_height": 0}
    size_kb = round(cover_path.stat().st_size / 1024, 1)
    try:
        with Image.open(cover_path) as img:
            w, h = img.size
    except Exception:
        w, h = 0, 0
    return {"has_cover": True, "cover_size_kb": size_kb, "cover_width": w, "cover_height": h}


def _parse_artist_album(dirname: str) -> tuple[str, str]:
    """Best-effort parse 'Artist - Album' or 'Artist - Album [mbid]' from folder name."""
    import re
    name = re.sub(r"\s*\[[\da-f-]{36}\]\s*$", "", dirname)
    if " - " in name:
        parts = name.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return "", name.strip()


def scan_library(root: Path) -> dict[str, dict]:
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        path = Path(dirpath)
        if not any(Path(f).suffix.lower() in MUSIC_EXTENSIONS for f in filenames):
            continue
        aid = _album_id(path)
        music_file = first_music_file(path)
        mbid = None
        if music_file:
            try:
                mbid = read_mbid_from_file(music_file)
            except Exception:
                pass
        cover_path = _find_cover(path)
        info = _cover_info(cover_path)
        result[aid] = {
            "id": aid,
            "path": path,
            "name": path.name,
            "mbid": mbid,
            "cover_path": cover_path,
            **info,
        }
    return result


# ---------------------------------------------------------------------------
# Cover art sources
# ---------------------------------------------------------------------------

def _search_caa(mbid: str) -> dict:
    """Search Cover Art Archive for all images."""
    source = {"source": "Cover Art Archive", "images": []}
    if not mbid:
        return source
    try:
        listing = _rate_limited_mb(fetch_cover_art_listing, mbid)
    except FetchError:
        return source
    for img in listing.get("images", []):
        img_id = img["id"]
        types = ", ".join(img.get("types", [])) or "Unknown"
        base_url = img["image"].rsplit(".", 1)[0]
        # Offer multiple size tiers
        for label, url in [
            ("Original", img["image"]),
            ("1200px", f"{base_url}-1200.jpg"),
            ("500px", f"{base_url}-500.jpg"),
        ]:
            source["images"].append({
                "id": f"caa-{img_id}-{label.lower().replace('px','')}",
                "url": url,
                "thumbnail_url": f"{base_url}-250.jpg",
                "type": types,
                "label": label,
                "source_detail": f"{types} ({label})",
            })
    return source


def _search_itunes(artist: str, album: str) -> dict:
    """Search iTunes for album artwork."""
    source = {"source": "iTunes", "images": []}
    if not artist and not album:
        return source
    query = f"{artist} {album}".strip()
    encoded = urllib.parse.quote(query)
    url = f"https://itunes.apple.com/search?term={encoded}&entity=album&limit=5"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return source
    for i, result in enumerate(data.get("results", [])):
        artwork_url = result.get("artworkUrl100", "")
        if not artwork_url:
            continue
        collection = result.get("collectionName", "Unknown Album")
        result_artist = result.get("artistName", "Unknown Artist")
        for size_label, size_str in [("3000px", "3000x3000bb"), ("1200px", "1200x1200bb"), ("600px", "600x600bb")]:
            big_url = artwork_url.replace("100x100bb", size_str)
            source["images"].append({
                "id": f"itunes-{i}-{size_label.replace('px','')}",
                "url": big_url,
                "thumbnail_url": artwork_url,
                "type": "Front",
                "label": size_label,
                "source_detail": f"{result_artist} - {collection} ({size_label})",
            })
    return source


def _search_discogs(artist: str, album: str) -> dict:
    """Search Discogs for cover images. Requires DISCOGS_TOKEN."""
    source = {"source": "Discogs", "images": []}
    if not DISCOGS_TOKEN:
        return source
    if not artist and not album:
        return source
    params = {}
    if album:
        params["release_title"] = album
    if artist:
        params["artist"] = artist
    params["type"] = "release"
    params["per_page"] = "5"
    params["token"] = DISCOGS_TOKEN
    qs = urllib.parse.urlencode(params)
    url = f"https://api.discogs.com/database/search?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return source
    for i, result in enumerate(data.get("results", [])):
        cover = result.get("cover_image", "")
        thumb = result.get("thumb", "")
        title = result.get("title", "Unknown")
        if not cover:
            continue
        source["images"].append({
            "id": f"discogs-{i}",
            "url": cover,
            "thumbnail_url": thumb or cover,
            "type": "Front",
            "label": "Original",
            "source_detail": title,
        })
    return source


def fetch_sources(album: dict) -> list[dict]:
    """Query all sources in parallel and return consolidated results."""
    mbid = album.get("mbid")
    artist, album_name = "", ""

    # Try to get artist/album from MusicBrainz first
    if mbid:
        try:
            artist, album_name = _rate_limited_mb(fetch_release_info, mbid)
        except FetchError:
            pass

    # Fallback: parse from directory name
    if not artist and not album_name:
        artist, album_name = _parse_artist_album(album["name"])

    sources = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_search_caa, mbid): "caa",
            pool.submit(_search_itunes, artist, album_name): "itunes",
            pool.submit(_search_discogs, artist, album_name): "discogs",
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                if result["images"]:
                    sources.append(result)
            except Exception:
                pass
    return sources


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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

    # Validate image
    try:
        img = Image.open(io.BytesIO(img_data))
        img.verify()
        img = Image.open(io.BytesIO(img_data))  # re-open after verify
        w, h = img.size
    except Exception:
        return jsonify({"error": "Downloaded data is not a valid image"}), 400

    ext = ext_from_url(url)
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"

    # Remove old cover files
    for old_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        old = album_dir / f"cover{old_ext}"
        if old.exists():
            old.unlink()
        old_thumb = album_dir / f"thumbnail{old_ext}"
        if old_thumb.exists():
            old_thumb.unlink()

    # Write new cover
    cover_path = album_dir / f"cover{ext}"
    cover_path.write_bytes(img_data)

    # Generate thumbnail
    try:
        thumb = img.copy()
        thumb.thumbnail((250, 250), Image.LANCZOS)
        thumb_path = album_dir / f"thumbnail{ext}"
        if ext in (".jpg", ".jpeg"):
            thumb.save(thumb_path, "JPEG", quality=85)
        else:
            thumb.save(thumb_path)
    except Exception:
        pass  # thumbnail generation failure is non-critical

    # Update cache
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
