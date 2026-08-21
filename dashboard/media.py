# dashboard/media.py
import aiohttp

MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def fetch_image(url: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes | None:
    """Download an image with a hard size cap; None on any failure."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                if not (r.content_type or "").startswith("image/"):
                    return None
                buf = b""
                async for chunk in r.content.iter_chunked(65536):
                    buf += chunk
                    if len(buf) > max_bytes:
                        return None
        return buf or None
    except Exception:
        return None
