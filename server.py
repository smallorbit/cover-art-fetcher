#!/usr/bin/env python3
"""Web app for browsing album cover art and finding higher-resolution replacements."""

import argparse
import hashlib
import io
import json
import os
import re
import struct
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
    fetch_release_metadata,
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
        artist, _ = _parse_artist_album(path.name)
        if not artist and path.parent != root:
            artist = path.parent.name
        result[aid] = {
            "id": aid,
            "path": path,
            "name": path.name,
            "artist": artist,
            "mbid": mbid,
            "cover_path": cover_path,
            **info,
        }
    return result


# ---------------------------------------------------------------------------
# Image probing — get actual file size and resolution from remote images
# ---------------------------------------------------------------------------

def _head_size(url: str) -> int | None:
    """Do a HEAD request and return Content-Length in bytes, or None."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Parse JPEG SOF markers to extract width x height from partial data."""
    i = 0
    if len(data) < 2 or data[0:2] != b'\xff\xd8':
        return None
    i = 2
    while i < len(data) - 8:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xD9:  # EOI
            break
        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01, 0xFF):
            i += 2
            continue
        if i + 4 > len(data):
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF markers: C0-C3, C5-C7, C9-CB, CD-CF
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 <= len(data):
                h = struct.unpack(">H", data[i + 5:i + 7])[0]
                w = struct.unpack(">H", data[i + 7:i + 9])[0]
                return (w, h)
        i += 2 + seg_len
    return None


def _read_png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Parse PNG IHDR to extract width x height."""
    if len(data) < 24 or data[0:8] != b'\x89PNG\r\n\x1a\n':
        return None
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    return (w, h)


def _probe_image(url: str) -> dict:
    """Probe a remote image for file size and dimensions.

    Returns {"size_kb": float, "width": int, "height": int}.
    Values are 0 if unknown.
    """
    result = {"size_kb": 0, "width": 0, "height": 0}

    try:
        # Fetch first 64KB — enough to read JPEG/PNG headers and get Content-Length
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-65535",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Content-Length from range response or full response
            cl = resp.headers.get("Content-Range")
            if cl and "/" in cl:
                total = cl.split("/")[-1]
                if total.isdigit():
                    result["size_kb"] = round(int(total) / 1024, 1)
            else:
                cl_header = resp.headers.get("Content-Length")
                if cl_header:
                    result["size_kb"] = round(int(cl_header) / 1024, 1)

            data = resp.read()

            # Try JPEG
            dims = _read_jpeg_dimensions(data)
            if dims:
                result["width"], result["height"] = dims
            else:
                # Try PNG
                dims = _read_png_dimensions(data)
                if dims:
                    result["width"], result["height"] = dims
    except Exception:
        # Fall back to HEAD for size only
        size = _head_size(url)
        if size:
            result["size_kb"] = round(size / 1024, 1)

    return result


def _probe_images_batch(images: list[dict]) -> list[dict]:
    """Probe a batch of images in parallel, adding size_kb/width/height to each."""
    def probe_one(img):
        # For iTunes, dimensions are known from the URL pattern
        if "itunes.apple.com" in img.get("url", ""):
            url = img["url"]
            for sz in ("3000x3000", "1200x1200", "600x600"):
                if sz in url:
                    dim = int(sz.split("x")[0])
                    img["width"] = dim
                    img["height"] = dim
                    break
            # Still need file size
            size = _head_size(url)
            if size:
                img["size_kb"] = round(size / 1024, 1)
            return img

        # For everything else, do a partial download probe
        info = _probe_image(img["url"])
        img["size_kb"] = info["size_kb"]
        img["width"] = info["width"]
        img["height"] = info["height"]
        return img

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe_one, img): img for img in images}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass
    return images


def _detect_duplicates(images: list[dict], current_size_kb: float,
                       current_w: int, current_h: int) -> list[dict]:
    """Mark images that are likely the same as the current cover.

    Adds a "match" field: "current" if likely the same image, else None.
    """
    for img in images:
        img["match"] = None
        if current_size_kb <= 0:
            continue

        sz = img.get("size_kb", 0)
        w = img.get("width", 0)
        h = img.get("height", 0)

        # Exact or near-exact file size match (within 2%)
        if sz > 0 and abs(sz - current_size_kb) / current_size_kb < 0.02:
            img["match"] = "current"
            continue

        # Same resolution AND similar file size (within 15%)
        if (w > 0 and h > 0 and w == current_w and h == current_h
                and sz > 0 and abs(sz - current_size_kb) / current_size_kb < 0.15):
            img["match"] = "current"

    return images


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


def fetch_sources(album: dict, *, artist: str = "", album_name: str = "") -> list[dict]:
    """Query all sources in parallel, probe images, and detect duplicates."""
    mbid = album.get("mbid")

    if not artist and not album_name:
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

    # Probe all images for actual size/resolution
    all_images = [img for src in sources for img in src["images"]]
    if all_images:
        _probe_images_batch(all_images)

        # Detect duplicates of the current cover
        _detect_duplicates(
            all_images,
            album.get("cover_size_kb", 0),
            album.get("cover_width", 0),
            album.get("cover_height", 0),
        )

        # Sort each source's images: largest first (by pixel count, then file size)
        for src in sources:
            src["images"].sort(
                key=lambda img: (img.get("width", 0) * img.get("height", 0), img.get("size_kb", 0)),
                reverse=True,
            )

    return sources


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
    # Prevent path traversal
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
    # Sanitize type for filename: replace slashes, remove unsafe chars
    import re
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

    # Validate image
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

    # Generate a unique filename: Type-001.ext, Type-002.ext, ...
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

    # Archive current cover into .media/ before replacing it
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
