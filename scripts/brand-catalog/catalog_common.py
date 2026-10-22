"""Utilitas bersama scraper katalog brand (stdlib saja, tanpa dependency baru).

Prinsip (lihat README.md):
- User-Agent jujur ``EFN-CatalogSync/0.1 (Erajaya internal)``.
- Hormati robots.txt per host (RFC 9309): 2xx -> aturan dipakai; 4xx selain 429 -> dianggap tidak ada aturan
  (boleh); 429/5xx/gagal jaringan -> host dianggap TERLARANG (tidak di-crawl). ``Crawl-delay`` dipatuhi dan
  tidak pernah lebih kecil dari ``min_delay`` yang diminta skrip.
- Timeout per request, retry ringan (backoff eksponensial) hanya untuk error jaringan / 429 / 5xx.
- Idempoten: gambar yang sudah ada di disk tidak diunduh ulang; ``products.json`` ditulis atomik.
- Tidak ada secret, cookie, atau data pribadi yang diproses.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "EFN-CatalogSync/0.1 (Erajaya internal)"
DEFAULT_TIMEOUT = 30
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data" / "brand-catalog"
IMAGE_EXTS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RobotsDenied(Exception):
    """URL dilarang robots.txt (atau robots.txt host tidak dapat dipastikan)."""


@dataclass
class _HostPolicy:
    parser: urllib.robotparser.RobotFileParser | None  # None = tanpa aturan (boleh semua)
    allow_all: bool
    deny_all: bool
    delay: float
    last_request: float = 0.0


@dataclass
class Fetcher:
    """Klien HTTP sopan: robots.txt, crawl-delay per host, timeout, retry ringan."""

    min_delay: float = 1.0
    timeout: int = DEFAULT_TIMEOUT
    retries: int = 3
    _hosts: dict = field(default_factory=dict)
    requests_made: int = 0

    # ------------------------------------------------------------------ robots
    def _policy(self, url: str) -> _HostPolicy:
        parts = urllib.parse.urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key in self._hosts:
            return self._hosts[key]
        robots_url = f"{key}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        allow_all = deny_all = False
        try:
            body = self._raw_get(robots_url, polite=False).decode("utf-8", errors="replace")
            parser.parse(body.splitlines())
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code != 429:
                allow_all = True  # RFC 9309 §2.3.1.3: robots "unavailable" -> boleh
            else:
                deny_all = True  # RFC 9309 §2.3.1.4: "unreachable" -> anggap terlarang
        except (urllib.error.URLError, TimeoutError, OSError):
            deny_all = True
        delay = self.min_delay
        if not (allow_all or deny_all):
            crawl_delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
            if crawl_delay:
                delay = max(delay, float(crawl_delay))
        policy = _HostPolicy(None if (allow_all or deny_all) else parser, allow_all, deny_all, delay)
        self._hosts[key] = policy
        state = "deny-all" if deny_all else ("no-robots(allow)" if allow_all else "robots.txt")
        log(f"robots {key}: {state}, jeda {delay:.1f}s")
        return policy

    def check_allowed(self, url: str) -> _HostPolicy:
        policy = self._policy(url)
        if policy.deny_all:
            raise RobotsDenied(f"robots.txt tidak dapat dipastikan untuk {url}")
        if policy.parser is not None and not policy.parser.can_fetch(USER_AGENT, url):
            raise RobotsDenied(f"dilarang robots.txt: {url}")
        return policy

    # -------------------------------------------------------------------- http
    def _raw_get(self, url: str, data: bytes | None = None, headers: dict | None = None, polite: bool = True) -> bytes:
        req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept-Language", "en,id;q=0.8")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (URL dari konfigurasi skrip)
            self.requests_made += 1
            return resp.read()

    def request(self, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        policy = self.check_allowed(url)
        attempt = 0
        while True:
            wait = policy.last_request + policy.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            policy.last_request = time.monotonic()
            try:
                return self._raw_get(url, data=data, headers=headers)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries:
                    raise
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                backoff = float(retry_after) if (retry_after or "").isdigit() else 2 ** (attempt + 1)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt >= self.retries:
                    raise
                backoff = 2 ** (attempt + 1)
                log(f"  jaringan: {exc} -> ulang dalam {backoff:.0f}s")
            attempt += 1
            time.sleep(backoff)

    def get_json(self, url: str):
        return json.loads(self.request(url, headers={"Accept": "application/json"}).decode("utf-8"))

    def post_json(self, url: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        raw = self.request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
        return json.loads(raw.decode("utf-8"))

    def get_text(self, url: str) -> str:
        return self.request(url).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- teks
_BLOCK_TAGS = re.compile(r"(?i)</?(p|br|div|li|ul|ol|h[1-6]|tr|table|section)\b[^>]*>")
_TAGS = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1>")


def html_to_text(value: str | None, max_len: int = 2000) -> str:
    """HTML -> teks polos satu spasi per paragraf (aman untuk ``efn_online_description``: tanpa ``<``)."""
    if not value:
        return ""
    text = _SCRIPT.sub(" ", value)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = text.replace("<", "‹").replace(">", "›")  # validator Odoo menolak pola tag HTML
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text[:max_len].rstrip()


def clean_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", value or ""))).strip()


def nice_category(value: str) -> str:
    """'ALL-DAY BRUNCH' -> 'All-Day Brunch'; nama campuran ('COFFEE by Café Adagio') dirapikan per kata kapital."""
    words = []
    for w in clean_title(value).split(" "):
        if w.isupper() and len(w) > 1:
            w = "-".join(part.capitalize() for part in w.split("-"))
        words.append(w)
    return " ".join(words)


def slugify(value: str, max_len: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:max_len].strip("-") or "item"


# -------------------------------------------------------------------- output
def brand_dir(brand: str) -> Path:
    path = DATA_ROOT / brand
    (path / "images").mkdir(parents=True, exist_ok=True)
    return path


def download_image(fetcher: Fetcher, url: str, images_dir: Path, slug: str) -> str | None:
    """Unduh gambar ke ``images/<slug>.<ext>``; lewati bila file sudah ada (idempoten). Return nama file relatif."""
    existing = sorted(images_dir.glob(f"{slug}.*"))
    if existing:
        return f"images/{existing[0].name}"
    try:
        raw = fetcher.request(url, headers={"Accept": "image/webp,image/png,image/jpeg,*/*;q=0.5"})
    except RobotsDenied as exc:
        log(f"  lewati gambar ({exc})")
        return None
    except urllib.error.HTTPError as exc:
        log(f"  gambar gagal HTTP {exc.code}: {url}")
        return None
    ext = _sniff_ext(raw) or os.path.splitext(urllib.parse.urlsplit(url).path)[1].lstrip(".").lower() or "bin"
    if ext not in IMAGE_EXTS.values():
        log(f"  bukan gambar ({ext}): {url}")
        return None
    target = images_dir / f"{slug}.{ext}"
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(raw)
    tmp.replace(target)
    return f"images/{target.name}"


def _sniff_ext(raw: bytes) -> str | None:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def write_catalog(brand: str, source_url: str, products: list[dict], extra: dict | None = None) -> Path:
    """Tulis ``products.json`` (skema seragam, README §Skema) secara atomik."""
    out = brand_dir(brand) / "products.json"
    doc = {"brand": brand, "source_url": source_url, "scraped_at": now_iso(), "products": products}
    if extra:
        doc.update(extra)
    tmp = out.with_suffix(".json.part")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    return out


def product_record(**kw) -> dict:
    """Record produk skema seragam; field tambahan brand masuk ``extra``."""
    base = {
        "source_id": str(kw.pop("source_id")),
        "slug": kw.pop("slug"),
        "name": kw.pop("name"),
        "description_text": kw.pop("description_text", ""),
        "category": kw.pop("category", None),
        "image_file": kw.pop("image_file", None),
        "source_url": kw.pop("source_url", None),
        "price_idr": None,
        "company_code": kw.pop("company_code"),
    }
    base["extra"] = kw.pop("extra", {}) or {}
    if kw:
        raise TypeError(f"field tidak dikenal: {sorted(kw)}")
    return base


def configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # konsol Windows cp1252
    except (AttributeError, ValueError):
        pass
