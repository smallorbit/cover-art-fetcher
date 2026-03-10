#!/usr/bin/env python3
"""Fetch and download all cover art for a MusicBrainz release given its MBID."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


CAA_BASE = "https://coverartarchive.org/release"
MB_BASE  = "https://musicbrainz.org/ws/2/release"
USER_AGENT = "cover-art-fetcher/1.0 ( https://github.com/smallorbit/cover-art-fetcher )"

MUSIC_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
    ".wma", ".wav", ".aiff", ".ape", ".alac",
})


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class FetchError(Exception):
    pass


def get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise FetchError(f"invalid UUID in request to {url}")
        elif e.code == 404:
            raise FetchError(f"not found — {url}")
        else:
            raise FetchError(f"HTTP {e.code} from {url}")
    except urllib.error.URLError as e:
        raise FetchError(f"could not connect — {e.reason}")


def fetch_bytes(url: str) -> bytes:
    """Fetch url, preserving User-Agent across redirects, and return raw bytes."""
    class UARedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new_req:
                new_req.add_unredirected_header("User-Agent", USER_AGENT)
            return new_req

    opener = urllib.request.build_opener(UARedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req) as response:
        return response.read()


def download_image(url: str, dest: Path) -> int:
    data = fetch_bytes(url)
    dest.write_bytes(data)
    return len(data)


def ext_from_url(url: str) -> str:
    path = url.split("?")[0].rstrip("/")
    suffix = Path(path).suffix
    return suffix if suffix else ".jpg"


# ---------------------------------------------------------------------------
# MusicBrainz / Cover Art Archive API
# ---------------------------------------------------------------------------

def fetch_release_info(mbid: str) -> tuple[str, str]:
    """Return (artist, album) from the MusicBrainz API."""
    data = get(f"{MB_BASE}/{mbid}?fmt=json&inc=artist-credits")
    album = data.get("title", "")
    credits = data.get("artist-credit", [])
    artist_parts = []
    for credit in credits:
        if isinstance(credit, dict) and "artist" in credit:
            artist_parts.append(credit["artist"]["name"])
            if credit.get("joinphrase"):
                artist_parts.append(credit["joinphrase"])
    return "".join(artist_parts), album


def fetch_release_metadata(mbid: str) -> dict:
    """Return extended release metadata from MusicBrainz.

    Returns a dict with keys: artist, album, year, label.
    Raises FetchError on failure.
    """
    data = get(f"{MB_BASE}/{mbid}?fmt=json&inc=artist-credits+labels")
    album = data.get("title", "")
    credits = data.get("artist-credit", [])
    artist_parts = []
    for credit in credits:
        if isinstance(credit, dict) and "artist" in credit:
            artist_parts.append(credit["artist"]["name"])
            if credit.get("joinphrase"):
                artist_parts.append(credit["joinphrase"])
    artist = "".join(artist_parts)

    date = data.get("date", "")
    year = date[:4] if date else None

    label = None
    label_infos = data.get("label-info", [])
    if label_infos:
        li = label_infos[0]
        label = (li.get("label") or {}).get("name")

    return {"artist": artist, "album": album, "year": year, "label": label}


def fetch_cover_art_listing(mbid: str) -> dict:
    return get(f"{CAA_BASE}/{mbid}")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def safe_dirname(value: str) -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(". ")
    return value or "_"


def build_output_dir(mbid: str, artist: str, album: str) -> Path:
    if artist and album:
        dirname = safe_dirname(f"{artist} - {album} [{mbid}]")
    elif album:
        dirname = safe_dirname(f"{album} [{mbid}]")
    else:
        dirname = mbid
    return Path(dirname)


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def _require_mutagen():
    try:
        import mutagen  # noqa: F401
    except ImportError:
        sys.exit(
            "mutagen is required for directory mode.\n"
            "Install it with: pip install mutagen"
        )


def read_mbid_from_file(path: Path) -> str | None:
    """Extract MusicBrainz release ID from audio file tags."""
    _require_mutagen()
    from mutagen import File  # type: ignore

    try:
        audio = File(path, easy=False)
    except Exception:
        return None

    if audio is None or audio.tags is None:
        return None

    tags = audio.tags

    # ID3 (MP3, AIFF, WAV): TXXX frames accessed via getall()
    if hasattr(tags, "getall"):
        for frame in tags.getall("TXXX"):
            if frame.desc.lower() in ("musicbrainz release id", "musicbrainz album id"):
                return frame.text[0] if frame.text else None
        return None

    # VorbisComment (FLAC, OGG, Opus): dict-like with list values
    for key in ("musicbrainz_albumid", "MUSICBRAINZ_ALBUMID"):
        if key in tags:
            vals = tags[key]
            return vals[0] if vals else None

    # MP4 / M4A
    mp4_key = "----:com.apple.iTunes:MusicBrainz Album Id"
    if mp4_key in tags:
        vals = tags[mp4_key]
        if vals:
            v = vals[0]
            return v.decode("utf-8") if isinstance(v, bytes) else str(v)

    return None


def first_music_file(directory: Path) -> Path | None:
    files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in MUSIC_EXTENSIONS
    )
    return files[0] if files else None


# ---------------------------------------------------------------------------
# Core download logic
# ---------------------------------------------------------------------------

def download_cover_art(mbid: str, out_dir: Path) -> None:
    """Download all cover art for mbid into out_dir."""
    listing = fetch_cover_art_listing(mbid)
    images = listing.get("images", [])
    print(f"  Found {len(images)} image(s)")

    if not images:
        print("  Nothing to download.")
        return

    media_dir = out_dir / ".media"
    media_dir.mkdir(exist_ok=True)

    total = 0
    for img in images:
        img_id  = img["id"]
        types   = "_".join(t.replace("/", "-") for t in img.get("types", [])) or "unknown"
        ext     = ext_from_url(img["image"])
        is_front = bool(img.get("front"))

        if is_front:
            base = img["image"].rsplit(".", 1)[0]
            try:
                data_orig = fetch_bytes(img["image"])
                data_1200 = fetch_bytes(f"{base}-1200.jpg")
                media_data = data_1200 if len(data_1200) > len(data_orig) else data_orig
                media_path = media_dir / f"{types}-{img_id}{ext}"
                media_path.write_bytes(media_data)
                print(f"  .media/{media_path.name} ({len(media_data) // 1024} KB)")
                total += 1
                cover_path = out_dir / f"cover{ext}"
                cover_path.write_bytes(data_1200)
                print(f"  cover{ext} ({len(data_1200) // 1024} KB)")
                total += 1
            except Exception as e:
                print(f"  .media/{types}-{img_id}{ext} or cover{ext} — FAILED: {e}")

            try:
                data_250 = fetch_bytes(f"{base}-250.jpg")
                thumb_path = out_dir / f"thumbnail{ext}"
                thumb_path.write_bytes(data_250)
                print(f"  thumbnail{ext} ({len(data_250) // 1024} KB)")
                total += 1
            except Exception as e:
                print(f"  thumbnail{ext} — FAILED: {e}")
        else:
            dest = media_dir / f"{types}-{img_id}{ext}"
            try:
                size_bytes = download_image(img["image"], dest)
                print(f"  .media/{dest.name} ({size_bytes // 1024} KB)")
                total += 1
            except Exception as e:
                print(f"  .media/{dest.name} — FAILED: {e}")

    print(f"  Done. {total} file(s) saved.")


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def run_single(mbid: str) -> None:
    print(f"Fetching release info for {mbid}...")
    try:
        artist, album = fetch_release_info(mbid)
    except FetchError as e:
        sys.exit(f"Error: {e}")

    if artist or album:
        print(f"  Artist : {artist or '(unknown)'}")
        print(f"  Album  : {album or '(unknown)'}")

    out_dir = build_output_dir(mbid, artist, album)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Fetching cover art listing...\n  Saving to: {out_dir}/\n")

    try:
        download_cover_art(mbid, out_dir)
    except FetchError as e:
        sys.exit(f"Error: {e}")


def run_directory(root: Path) -> None:
    music_dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        path = Path(dirpath)
        if any(Path(f).suffix.lower() in MUSIC_EXTENSIONS for f in filenames):
            music_dirs.append(path)

    print(f"Found {len(music_dirs)} music director(ies) under {root}\n")

    for i, music_dir in enumerate(music_dirs, 1):
        print(f"[{i}/{len(music_dirs)}] {music_dir}")

        media_dir = music_dir / ".media"
        if media_dir.is_dir() and any(media_dir.iterdir()):
            print("  Skipping — .media/ already populated.\n")
            continue

        music_file = first_music_file(music_dir)
        if not music_file:
            print("  Warning: no music files found.\n")
            continue

        mbid = read_mbid_from_file(music_file)
        if not mbid:
            print(f"  Warning: no MusicBrainz ID in tags ({music_file.name}).\n")
            continue

        print(f"  MBID: {mbid}")
        try:
            download_cover_art(mbid, music_dir)
        except FetchError as e:
            print(f"  Error: {e}")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch cover art from the Cover Art Archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s 76df3287-6cda-33eb-8e9a-044b5e15ffdd\n"
            "  %(prog)s --dir ~/Music"
        ),
    )
    parser.add_argument("mbid", nargs="?", help="MusicBrainz release ID")
    parser.add_argument(
        "-d", "--dir", metavar="PATH",
        help="scan a directory tree and fetch cover art for each music folder found",
    )
    args = parser.parse_args()

    if args.dir:
        root = Path(args.dir)
        if not root.is_dir():
            sys.exit(f"Error: '{args.dir}' is not a directory.")
        run_directory(root)
        return

    if args.mbid:
        mbid = args.mbid.strip()
    else:
        try:
            mbid = input("MusicBrainz release ID: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

    if not mbid:
        sys.exit("Error: no MusicBrainz ID provided.")

    run_single(mbid)


if __name__ == "__main__":
    main()
