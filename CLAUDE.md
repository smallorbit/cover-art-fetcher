# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A dual-mode Python project:
- **CLI tool** (`fetch_cover_art.py`) — fetches cover art from the [Cover Art Archive](https://coverartarchive.org) for MusicBrainz releases, with optional AcoustID fingerprint-based identification for untagged music
- **Web app** (`server.py`) — interactive browser UI for browsing album art, searching MusicBrainz, and previewing higher-resolution replacements

## Running the CLI

```bash
# Single release by MBID
python fetch_cover_art.py <mbid>

# Scan a directory tree
python fetch_cover_art.py --dir ~/Music

# Identify untagged music using AcoustID fingerprints
python fetch_cover_art.py --dir ~/Music --identify
```

## Running the web app

```bash
python server.py
```

Opens an interactive UI at `http://localhost:5000` for browsing and managing cover art.

## Dependencies

Install via:

```bash
pip install -r requirements.txt
```

- `mutagen` — read MusicBrainz IDs and audio metadata from files
- `flask` — web framework for the UI
- `Pillow` — image resizing and metadata handling

## Architecture

### `fetch_cover_art.py`

Core library used by both CLI and web app. Key functions:

- **`get(url)`**, **`post(url, data)`** — HTTP helpers for MusicBrainz and AcoustID APIs
- **`first_music_file(path)`** — find the first audio file in a directory
- **`read_mbid_from_file(path)`** — extract MusicBrainz release ID from file tags
- **`fetch_release_info(mbid)`** — lookup release metadata from MusicBrainz
- **`identify_file(path)`** — identify untagged music using AcoustID fingerprinting
- **`download_cover_art(folder, mbid)`** — fetch and save all cover images for a release
- **`run_single(mbid)`** — download art for a single release
- **`run_directory(directory, identify=False)`** — batch download art for a music library

Key constants: `CAA_BASE`, `MB_BASE`, `ACOUSTID_BASE`, `USER_AGENT`, `MUSIC_EXTENSIONS`.

### `server.py`

Flask web app that exposes `fetch_cover_art.py` functions via HTTP/JSON routes. Handles:

- Album lookup and preview
- MusicBrainz release searches and alternates
- Full-size lightbox preview
- Media asset management (delete, reorder)
- AcoustID-based identification for untagged files

## Output structure

For `--dir` mode, images are organized in the music folder:
```
Artist - Album/
  cover.jpg        ← front art (1200px)
  thumbnail.jpg    ← front art (250px)
  .media/
    Front-<id>.jpg
    Back-<id>.jpg
    ...            ← other images from the release
```

For single-MBID CLI mode, a new folder `Artist - Album [mbid]/` is created in the current directory.

## sample-library

`sample-library/` is a test music library used for manual testing of `--dir` mode. Gitignored; some folders have pre-populated `.media/` directories (those are skipped on re-run).
