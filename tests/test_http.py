import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

import fetch_cover_art
from fetch_cover_art import (
    FetchError,
    fetch_release_info,
    fetch_release_metadata,
    get,
    lookup_acoustid,
    post,
)
from server import _search_itunes


def _mock_urlopen_response(data: bytes, status=200):
    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code, url="http://example.com"):
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, None)


# ── get() / post() ──────────────────────────────────────────────


class TestGet:
    @patch("urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response(b'{"key": "value"}')
        assert get("http://example.com") == {"key": "value"}

    @patch("urllib.request.urlopen")
    def test_404_raises_fetch_error(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(404)
        with pytest.raises(FetchError, match="not found"):
            get("http://example.com")

    @patch("urllib.request.urlopen")
    def test_400_raises_fetch_error(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(400)
        with pytest.raises(FetchError, match="invalid UUID"):
            get("http://example.com")

    @patch("urllib.request.urlopen")
    def test_500_raises_fetch_error(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(500)
        with pytest.raises(FetchError, match="HTTP 500"):
            get("http://example.com")

    @patch("urllib.request.urlopen")
    def test_url_error_raises_fetch_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with pytest.raises(FetchError, match="could not connect"):
            get("http://example.com")

    @patch("urllib.request.urlopen")
    def test_sends_user_agent(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response(b'{}')
        get("http://example.com")
        req = mock_urlopen.call_args[0][0]
        assert "cover-art-fetcher" in req.get_header("User-agent")


class TestPost:
    @patch("urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response(b'{"ok": true}')
        assert post("http://example.com", {"k": "v"}) == {"ok": True}

    @patch("urllib.request.urlopen")
    def test_http_error_raises_fetch_error(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(502)
        with pytest.raises(FetchError, match="HTTP 502"):
            post("http://example.com", {})


# ── fetch_release_info() ────────────────────────────────────────


class TestFetchReleaseInfo:
    @patch("fetch_cover_art.get")
    def test_basic(self, mock_get):
        mock_get.return_value = {
            "title": "DSOTM",
            "artist-credit": [{"artist": {"name": "Pink Floyd"}}],
        }
        assert fetch_release_info("abc") == ("Pink Floyd", "DSOTM")

    @patch("fetch_cover_art.get")
    def test_multiple_artists_with_joinphrase(self, mock_get):
        mock_get.return_value = {
            "title": "Abbey Road",
            "artist-credit": [
                {"artist": {"name": "John"}, "joinphrase": " & "},
                {"artist": {"name": "Paul"}},
            ],
        }
        artist, _ = fetch_release_info("abc")
        assert artist == "John & Paul"

    @patch("fetch_cover_art.get")
    def test_missing_fields(self, mock_get):
        mock_get.return_value = {"title": "", "artist-credit": []}
        assert fetch_release_info("abc") == ("", "")

    @patch("fetch_cover_art.get")
    def test_fetch_error_propagates(self, mock_get):
        mock_get.side_effect = FetchError("not found")
        with pytest.raises(FetchError):
            fetch_release_info("abc")


# ── fetch_release_metadata() ────────────────────────────────────


class TestFetchReleaseMetadata:
    @patch("fetch_cover_art.get")
    def test_full_response(self, mock_get):
        mock_get.return_value = {
            "title": "DSOTM",
            "artist-credit": [{"artist": {"name": "Pink Floyd"}}],
            "date": "1973-03-01",
            "label-info": [{"label": {"name": "Harvest"}}],
        }
        result = fetch_release_metadata("abc")
        assert result == {
            "artist": "Pink Floyd",
            "album": "DSOTM",
            "year": "1973",
            "label": "Harvest",
        }

    @patch("fetch_cover_art.get")
    def test_no_label(self, mock_get):
        mock_get.return_value = {
            "title": "X",
            "artist-credit": [{"artist": {"name": "A"}}],
            "date": "2020",
            "label-info": [],
        }
        assert fetch_release_metadata("abc")["label"] is None

    @patch("fetch_cover_art.get")
    def test_no_date(self, mock_get):
        mock_get.return_value = {
            "title": "X",
            "artist-credit": [{"artist": {"name": "A"}}],
            "date": "",
            "label-info": [],
        }
        assert fetch_release_metadata("abc")["year"] is None


# ── lookup_acoustid() ───────────────────────────────────────────


def _acoustid_response(results):
    return {"status": "ok", "results": results}


def _acoustid_result(score, releases):
    return {
        "score": score,
        "recordings": [{"releases": releases}],
    }


def _acoustid_release(rid, title="Album", artist="Artist", year=None):
    r = {
        "id": rid,
        "title": title,
        "artists": [{"name": artist}],
        "date": {"year": year} if year else {},
    }
    return r


class TestLookupAcoustid:
    @patch("fetch_cover_art.post")
    def test_success(self, mock_post):
        mock_post.return_value = _acoustid_response([
            _acoustid_result(0.95, [_acoustid_release("r1", "Album1")]),
        ])
        results = lookup_acoustid("key", 180, "fp")
        assert len(results) == 1
        assert results[0]["mbid"] == "r1"
        assert results[0]["score"] == 0.95

    @patch("fetch_cover_art.post")
    def test_dedup_keeps_higher_score(self, mock_post):
        mock_post.return_value = _acoustid_response([
            _acoustid_result(0.5, [_acoustid_release("r1")]),
            _acoustid_result(0.9, [_acoustid_release("r1")]),
        ])
        results = lookup_acoustid("key", 180, "fp")
        assert len(results) == 1
        assert results[0]["score"] == 0.9

    @patch("fetch_cover_art.post")
    def test_dedup_ignores_lower_score(self, mock_post):
        mock_post.return_value = _acoustid_response([
            _acoustid_result(0.9, [_acoustid_release("r1")]),
            _acoustid_result(0.5, [_acoustid_release("r1")]),
        ])
        results = lookup_acoustid("key", 180, "fp")
        assert len(results) == 1
        assert results[0]["score"] == 0.9

    @patch("fetch_cover_art.post")
    def test_sorted_by_score_descending(self, mock_post):
        mock_post.return_value = _acoustid_response([
            _acoustid_result(0.5, [_acoustid_release("r1")]),
            _acoustid_result(0.9, [_acoustid_release("r2")]),
            _acoustid_result(0.7, [_acoustid_release("r3")]),
        ])
        results = lookup_acoustid("key", 180, "fp")
        scores = [r["score"] for r in results]
        assert scores == [0.9, 0.7, 0.5]

    @patch("fetch_cover_art.post")
    def test_status_not_ok(self, mock_post):
        mock_post.return_value = {"status": "error"}
        assert lookup_acoustid("key", 180, "fp") == []

    @patch("fetch_cover_art.post")
    def test_no_results(self, mock_post):
        mock_post.return_value = {"status": "ok", "results": []}
        assert lookup_acoustid("key", 180, "fp") == []


# ── _search_itunes() ────────────────────────────────────────────


class TestSearchItunes:
    @patch("urllib.request.urlopen")
    def test_success_with_size_variants(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response(json.dumps({
            "results": [{
                "artworkUrl100": "https://is1-ssl.mzstatic.com/art/100x100bb.jpg",
                "collectionName": "DSOTM",
                "artistName": "Pink Floyd",
            }]
        }).encode())
        result = _search_itunes("Pink Floyd", "DSOTM")
        assert result["source"] == "iTunes"
        assert len(result["images"]) == 3
        urls = [img["url"] for img in result["images"]]
        assert any("3000x3000bb" in u for u in urls)
        assert any("1200x1200bb" in u for u in urls)
        assert any("600x600bb" in u for u in urls)

    @patch("urllib.request.urlopen")
    def test_no_artwork_skipped(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response(json.dumps({
            "results": [{"collectionName": "X", "artistName": "Y"}]
        }).encode())
        result = _search_itunes("Y", "X")
        assert result["images"] == []

    @patch("urllib.request.urlopen")
    def test_network_error_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("timeout")
        result = _search_itunes("Pink Floyd", "DSOTM")
        assert result == {"source": "iTunes", "images": []}

    def test_empty_query_returns_empty(self):
        result = _search_itunes("", "")
        assert result == {"source": "iTunes", "images": []}
