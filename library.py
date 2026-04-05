"""Library scanning and album helpers."""

import hashlib
import os
import re
from pathlib import Path

from PIL import Image

from fetch_cover_art import MUSIC_EXTENSIONS, first_music_file, read_mbid_from_file


def album_id(path: Path) -> str:
    return hashlib.md5(str(path).encode()).hexdigest()[:12]


def find_cover(album_dir: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        p = album_dir / f"cover{ext}"
        if p.exists():
            return p
    return None


def cover_info(cover_path: Path | None) -> dict:
    if cover_path is None or not cover_path.exists():
        return {"has_cover": False, "cover_size_kb": 0, "cover_width": 0, "cover_height": 0}
    size_kb = round(cover_path.stat().st_size / 1024, 1)
    try:
        with Image.open(cover_path) as img:
            w, h = img.size
    except Exception:
        w, h = 0, 0
    return {"has_cover": True, "cover_size_kb": size_kb, "cover_width": w, "cover_height": h}


def parse_artist_album(dirname: str) -> tuple[str, str]:
    """Best-effort parse 'Artist - Album' or 'Artist - Album [mbid]' from folder name."""
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
        aid = album_id(path)
        music_file = first_music_file(path)
        mbid = None
        if music_file:
            try:
                mbid = read_mbid_from_file(music_file)
            except Exception:
                pass
        cover_path = find_cover(path)
        info = cover_info(cover_path)
        artist, album_name = parse_artist_album(path.name)
        if not artist and path.parent != root:
            artist = path.parent.name
        if not album_name:
            album_name = path.name
        result[aid] = {
            "id": aid,
            "path": path,
            "name": path.name,
            "artist": artist,
            "album_name": album_name,
            "mbid": mbid,
            "cover_path": cover_path,
            **info,
        }
    return result
