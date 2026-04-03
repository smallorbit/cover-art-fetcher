from pathlib import Path

from fetch_cover_art import ext_from_url, safe_dirname, build_output_dir
from server import _parse_artist_album, _album_id, _detect_duplicates


# --- ext_from_url ---

def test_ext_from_url_simple_jpg():
    assert ext_from_url("https://example.com/image.jpg") == ".jpg"


def test_ext_from_url_with_query_string():
    assert ext_from_url("https://example.com/image.png?size=large") == ".png"


def test_ext_from_url_no_extension_defaults_to_jpg():
    assert ext_from_url("https://example.com/image") == ".jpg"


def test_ext_from_url_trailing_slash():
    assert ext_from_url("https://example.com/image.png/") == ".png"


def test_ext_from_url_real_caa_url():
    url = "https://coverartarchive.org/release/76df3287-6cda-33eb-8e9a-044b5e15ffdd/12345678901-500.jpg"
    assert ext_from_url(url) == ".jpg"


# --- safe_dirname ---

def test_safe_dirname_passthrough():
    assert safe_dirname("Pink Floyd - DSOTM") == "Pink Floyd - DSOTM"


def test_safe_dirname_replaces_slashes():
    assert safe_dirname("AC/DC") == "AC_DC"


def test_safe_dirname_replaces_colons():
    assert safe_dirname("Disc 1: Remastered") == "Disc 1_ Remastered"


def test_safe_dirname_replaces_question_marks():
    assert safe_dirname("Who?") == "Who_"


def test_safe_dirname_replaces_control_chars():
    assert safe_dirname("bad\x00name\x1f") == "bad_name"


def test_safe_dirname_strips_leading_trailing_dots():
    assert safe_dirname("...hidden...") == "hidden"


def test_safe_dirname_strips_whitespace():
    assert safe_dirname("  spaced  ") == "spaced"


def test_safe_dirname_empty_returns_underscore():
    assert safe_dirname("") == "_"


# --- build_output_dir ---

def test_build_output_dir_artist_and_album():
    result = build_output_dir("abc-123", "Pink Floyd", "DSOTM")
    assert result == Path("Pink Floyd - DSOTM [abc-123]")


def test_build_output_dir_album_only():
    result = build_output_dir("abc-123", "", "DSOTM")
    assert result == Path("DSOTM [abc-123]")


def test_build_output_dir_mbid_only():
    result = build_output_dir("abc-123", "", "")
    assert result == Path("abc-123")


def test_build_output_dir_special_chars_sanitized():
    result = build_output_dir("abc-123", "AC/DC", "Who Made Who?")
    assert result == Path("AC_DC - Who Made Who_ [abc-123]")


# --- _parse_artist_album ---

def test_parse_artist_album_standard():
    assert _parse_artist_album("Pink Floyd - DSOTM") == ("Pink Floyd", "DSOTM")


def test_parse_artist_album_strips_mbid_suffix():
    assert _parse_artist_album(
        "Pink Floyd - DSOTM [76df3287-6cda-33eb-8e9a-044b5e15ffdd]"
    ) == ("Pink Floyd", "DSOTM")


def test_parse_artist_album_no_separator():
    assert _parse_artist_album("JustAnAlbum") == ("", "JustAnAlbum")


def test_parse_artist_album_multiple_dashes_splits_on_first():
    assert _parse_artist_album("A - B - C") == ("A", "B - C")


def test_parse_artist_album_whitespace_handling():
    assert _parse_artist_album("  Artist  -  Album  ") == ("Artist", "Album")


def test_parse_artist_album_non_uuid_brackets_preserved():
    assert _parse_artist_album("Artist - Album [Deluxe]") == ("Artist", "Album [Deluxe]")


# --- _album_id ---

def test_album_id_deterministic():
    p = Path("/music/Pink Floyd - DSOTM")
    assert _album_id(p) == _album_id(p)


def test_album_id_different_paths_differ():
    assert _album_id(Path("/a")) != _album_id(Path("/b"))


# --- _detect_duplicates ---

def test_detect_duplicates_exact_size_match():
    images = [{"size_kb": 100.5, "width": 800, "height": 800}]
    result = _detect_duplicates(images, current_size_kb=101.0, current_w=600, current_h=600)
    assert result[0]["match"] == "current"


def test_detect_duplicates_resolution_and_size_match():
    images = [{"size_kb": 110.0, "width": 600, "height": 600}]
    result = _detect_duplicates(images, current_size_kb=100.0, current_w=600, current_h=600)
    assert result[0]["match"] == "current"


def test_detect_duplicates_no_match_when_too_different():
    images = [{"size_kb": 200.0, "width": 800, "height": 800}]
    result = _detect_duplicates(images, current_size_kb=100.0, current_w=600, current_h=600)
    assert result[0]["match"] is None


def test_detect_duplicates_skips_all_when_no_current_cover():
    images = [
        {"size_kb": 100.0, "width": 600, "height": 600},
        {"size_kb": 200.0, "width": 800, "height": 800},
    ]
    result = _detect_duplicates(images, current_size_kb=0, current_w=0, current_h=0)
    assert all(img["match"] is None for img in result)


def test_detect_duplicates_size_match_takes_priority():
    images = [{"size_kb": 100.0, "width": 600, "height": 600}]
    result = _detect_duplicates(images, current_size_kb=100.5, current_w=600, current_h=600)
    assert result[0]["match"] == "current"
