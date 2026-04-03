"""Cover art source aggregation."""

import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from fetch_cover_art import (
    USER_AGENT,
    FetchError,
    fetch_cover_art_listing,
    fetch_release_info,
)
from library import _parse_artist_album
from probing import _detect_duplicates, _probe_images_batch

DISCOGS_TOKEN: str | None = os.environ.get("DISCOGS_TOKEN")

_rate_limited_mb = None


def init(rate_limited_mb_fn):
    global _rate_limited_mb
    _rate_limited_mb = rate_limited_mb_fn


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
        if mbid:
            try:
                artist, album_name = _rate_limited_mb(fetch_release_info, mbid)
            except FetchError:
                pass

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

    all_images = [img for src in sources for img in src["images"]]
    if all_images:
        _probe_images_batch(all_images)

        _detect_duplicates(
            all_images,
            album.get("cover_size_kb", 0),
            album.get("cover_width", 0),
            album.get("cover_height", 0),
        )

        for src in sources:
            src["images"].sort(
                key=lambda img: (img.get("width", 0) * img.get("height", 0), img.get("size_kb", 0)),
                reverse=True,
            )

    return sources
