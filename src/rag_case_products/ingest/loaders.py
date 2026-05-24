"""URL loader seam wrapping markitdown (SPEC §5.1, §5.2 step 2)."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from llama_index.core.schema import Document
from markitdown import MarkItDown

from rag_case_products.config import CASES_PARSED_DIR, CASES_RAW_DIR
from rag_case_products.models import DocType

_converter = MarkItDown()


def _url_to_doc_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _url_to_slug(url: str) -> str:
    return Path(urlparse(url).path).name


class UrlLoader:
    def load(self, url: str, doc_type: DocType) -> Document:
        slug = _url_to_slug(url)
        html_cache = CASES_RAW_DIR / f"{slug}.html"
        md_cache = CASES_PARSED_DIR / f"{slug}.md"

        if md_cache.exists():
            content = md_cache.read_text()
            title = slug
        elif html_cache.exists():
            result = _converter.convert(str(html_cache))
            content = result.text_content or ""
            title = result.title or slug
            CASES_PARSED_DIR.mkdir(parents=True, exist_ok=True)
            md_cache.write_text(content)
        else:
            resp = httpx.get(url, follow_redirects=True, timeout=60)
            resp.raise_for_status()
            CASES_RAW_DIR.mkdir(parents=True, exist_ok=True)
            html_cache.write_bytes(resp.content)
            result = _converter.convert(str(html_cache))
            content = result.text_content or ""
            title = result.title or slug
            CASES_PARSED_DIR.mkdir(parents=True, exist_ok=True)
            md_cache.write_text(content)

        return Document(
            text=content,
            id_=_url_to_doc_id(url),
            metadata={
                "doc_type": doc_type.value,
                "source": url,
                "title": title,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            },
        )
