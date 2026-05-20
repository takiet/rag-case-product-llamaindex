"""PDF-based product loader: download PDF from URL, LlamaCloud parse, return Document."""

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from llama_cloud import LlamaCloud
from llama_cloud.types.parsing_get_response import (
    MarkdownPageFailedMarkdownPage,
    MarkdownPageMarkdownResultPage,
)
from llama_index.core.schema import Document

from rag_case_products.config import PRODUCTS_PARSED_DIR, PRODUCTS_RAW_DIR
from rag_case_products.models import DocType

log = logging.getLogger(__name__)

_NOISE_RES = [
    re.compile(r"©.*?Axis Communications.*?\n"),
    re.compile(r"www\.axis\.com\s*"),
]


def _url_slug(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def download_pdf(pdf_url: str, cache_dir: Path = PRODUCTS_RAW_DIR) -> Path:
    """Download PDF to cache_dir. Returns local path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_url_slug(pdf_url)}.pdf"
    if path.exists():
        return path
    resp = httpx.get(pdf_url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def parse_pdf_to_markdown(pdf_path: Path, cache_dir: Path = PRODUCTS_PARSED_DIR) -> str:
    """Parse PDF via LlamaCloud agentic tier. Returns clean markdown."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{pdf_path.stem}.md"
    if cache_file.exists():
        return cache_file.read_text()

    client = LlamaCloud()
    with pdf_path.open("rb") as f:
        uploaded = client.files.create(file=f, purpose="parse")
    result = client.parsing.parse(
        file_id=uploaded.id,
        tier="agentic",
        version="latest",
        expand=["markdown"],
    )

    if not result.markdown:
        raise RuntimeError(f"LlamaCloud returned no markdown result for {pdf_path.name}")

    pages = result.markdown.pages or []
    for p in pages:
        if isinstance(p, MarkdownPageFailedMarkdownPage):
            log.warning("Parse failed for page in %s: %s", pdf_path.name, p.error)

    text = "\n\n".join(
        p.markdown
        for p in pages
        if isinstance(p, MarkdownPageMarkdownResultPage) and p.markdown
    )
    for pattern in _NOISE_RES:
        text = pattern.sub("", text)

    if not text.strip():
        raise RuntimeError(
            f"LlamaCloud produced empty markdown for {pdf_path.name}; "
            "check page-level errors above"
        )

    cache_file.write_text(text)
    return text


class PdfProductLoader:
    """Load a PDF URL into a Document via LlamaCloud parse."""

    def load(self, pdf_url: str) -> Document:
        pdf_path = download_pdf(pdf_url)
        markdown = parse_pdf_to_markdown(pdf_path)
        content_hash = hashlib.sha256(markdown.encode()).hexdigest()

        return Document(
            text=markdown,
            id_=_url_slug(pdf_url),
            metadata={
                "doc_type": DocType.PRODUCT.value,
                "source": pdf_url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_hash": content_hash,
            },
        )
