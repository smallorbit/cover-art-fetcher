# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Python script (`fetch_cover_art.py`) that fetches cover art from the [Cover Art Archive](https://coverartarchive.org) for MusicBrainz releases. No framework, no build step — pure stdlib plus one optional third-party dependency.

## Running the script

```bash
# Single release by MBID
python fetch_cover_art.py <mbid>

# Scan a directory tree
python fetch_cover_art.py --dir ~/Music
```

## Dependencies

`mutagen` is the only third-party dependency, required only for `--dir` mode (reads MusicBrainz IDs from audio file tags). Install via:

```bash
pip install -r requirements.txt
```

## Architecture

Everything lives in `fetch_cover_art.py`. The flow is:

1. **Entry point** (`main`) — parses args, dispatches to `run_single` or `run_directory`
2. **`run_single`** — looks up release metadata from MusicBrainz API, builds an output directory named `Artist - Album [mbid]/`, calls `download_cover_art`
3. **`run_directory`** — walks a directory tree, finds music folders, reads the MBID from the first audio file's tags via `mutagen`, calls `download_cover_art` for each
4. **`download_cover_art`** — fetches the CAA image listing, downloads front art in three sizes (`cover.*`, `thumbnail.*`, `.media/<type>-<id>.*`), downloads all other images into `.media/`

Key constants at the top of the file: `CAA_BASE`, `MB_BASE`, `USER_AGENT`.

## Output structure

For `--dir` mode, images are written into the music folder itself:
```
Artist - Album/
  cover.jpg        ← front art (1200px)
  thumbnail.jpg    ← front art (250px)
  .media/
    Front-<id>.jpg
    Back-<id>.jpg
    ...
```

For single-MBID mode, a new folder `Artist - Album [mbid]/` is created in the current directory with the same structure.

## sample-library

`sample-library/` contains a sample music library used for manual testing of `--dir` mode. It is gitignored. Some folders already have `.media/` populated (those are skipped on re-run).
