# cover-art-fetcher

Fetches cover art from the [Cover Art Archive](https://coverartarchive.org) for MusicBrainz releases. Works in two modes: single release by MBID, or batch scan of a music library directory.

## Requirements

Python 3.10+. For directory mode, `mutagen` is required:

```bash
pip install -r requirements.txt
```

## Usage

**Single release** — provide a MusicBrainz release ID:

```bash
python fetch_cover_art.py 76df3287-6cda-33eb-8e9a-044b5e15ffdd
```

Creates a folder named `Artist - Album [mbid]/` in the current directory containing the downloaded art.

**Directory scan** — point it at your music library:

```bash
python fetch_cover_art.py --dir ~/Music
```

Walks the directory tree, finds folders containing audio files, reads the MusicBrainz release ID from their tags, and downloads cover art into each folder. Folders that already have a `.media/` directory are skipped.

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

## Supported formats

`.mp3` `.flac` `.ogg` `.opus` `.m4a` `.aac` `.wma` `.wav` `.aiff` `.ape` `.alac`

MusicBrainz release IDs must be present in the file tags for directory mode to work. Most music tagged with [MusicBrainz Picard](https://picard.musicbrainz.org) will have these.
