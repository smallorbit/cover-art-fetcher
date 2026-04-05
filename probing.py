"""Image probing and dimension parsing."""

import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from fetch_cover_art import USER_AGENT


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
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-65535",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
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

            dims = _read_jpeg_dimensions(data)
            if dims:
                result["width"], result["height"] = dims
            else:
                dims = _read_png_dimensions(data)
                if dims:
                    result["width"], result["height"] = dims
    except Exception:
        size = _head_size(url)
        if size:
            result["size_kb"] = round(size / 1024, 1)

    return result


def probe_images_batch(images: list[dict]) -> list[dict]:
    """Probe a batch of images in parallel, adding size_kb/width/height to each."""
    def probe_one(img):
        if "itunes.apple.com" in img.get("url", ""):
            url = img["url"]
            for sz in ("3000x3000", "1200x1200", "600x600"):
                if sz in url:
                    dim = int(sz.split("x")[0])
                    img["width"] = dim
                    img["height"] = dim
                    break
            size = _head_size(url)
            if size:
                img["size_kb"] = round(size / 1024, 1)
            return img

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


def detect_duplicates(images: list[dict], current_size_kb: float,
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

        if sz > 0 and abs(sz - current_size_kb) / current_size_kb < 0.02:
            img["match"] = "current"
            continue

        if (w > 0 and h > 0 and w == current_w and h == current_h
                and sz > 0 and abs(sz - current_size_kb) / current_size_kb < 0.15):
            img["match"] = "current"

    return images
