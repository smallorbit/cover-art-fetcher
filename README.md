# cover-art-fetcher

Fetches cover art from the [Cover Art Archive](https://coverartarchive.org) for MusicBrainz releases. Two modes available:

- **CLI tool** — batch-process a music library or download art for a single release
- **Web app** — interactive browser UI for browsing, searching, and managing cover art

## Requirements

Python 3.10+. Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick start

### Web app (easiest)

```bash
python server.py
```

Open `http://localhost:5000` in your browser. Navigate to a music folder or paste a MusicBrainz release ID to browse and download cover art.

### CLI: single release

```bash
python fetch_cover_art.py 76df3287-6cda-33eb-8e9a-044b5e15ffdd
```

Creates a folder `Artist - Album [mbid]/` in the current directory with downloaded cover art.

### CLI: batch scan a directory

```bash
python fetch_cover_art.py --dir ~/Music
```

Walks the directory tree, reads the MusicBrainz release ID from each audio file's tags, and downloads cover art. Folders that already have a `.media/` directory are skipped.

### CLI: identify untagged music

```bash
python fetch_cover_art.py --dir ~/Music --identify
```

Uses AcoustID fingerprinting to identify untagged music files, looks up their release on MusicBrainz, and downloads cover art.

## Output structure

```
Artist - Album/
  cover.jpg        ← front cover (1200px)
  thumbnail.jpg    ← front cover (250px)
  .media/
    Front-<id>.jpg
    Back-<id>.jpg
    ...            ← all other images from the release
```

## Supported audio formats

`.mp3` `.flac` `.ogg` `.opus` `.m4a` `.aac` `.wma` `.wav` `.aiff` `.ape` `.alac`

For CLI `--dir` mode, MusicBrainz release IDs must be present in file tags. Most music tagged with [MusicBrainz Picard](https://picard.musicbrainz.org) will have these.
