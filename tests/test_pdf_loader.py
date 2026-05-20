"""Tests for ingest/pdf_loader.py and the hierarchical product ingest path."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from llama_cloud.types.parsing_get_response import (
    MarkdownPageFailedMarkdownPage,
    MarkdownPageMarkdownResultPage,
)
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
from llama_index.core.schema import Document

from rag_case_products.ingest.pdf_loader import (
    PdfProductLoader,
    download_pdf,
    parse_pdf_to_markdown,
)
from rag_case_products.models import DocType

# ── Helpers ──────────────────────────────────────────────────────────────────

def _ok_page(text: str, page_number: int = 1) -> MarkdownPageMarkdownResultPage:
    return MarkdownPageMarkdownResultPage(markdown=text, page_number=page_number, success=True)


def _fail_page(
    error: str = "render failed", page_number: int = 1
) -> MarkdownPageFailedMarkdownPage:
    return MarkdownPageFailedMarkdownPage(error=error, page_number=page_number, success=False)


def _make_parse_result(pages: list) -> MagicMock:
    """Build a mock LlamaCloud parse result with the given pages."""
    result = MagicMock()
    result.markdown = SimpleNamespace(pages=pages)
    return result


def _make_client(parse_result: MagicMock) -> MagicMock:
    client = MagicMock()
    client.files.create.return_value = MagicMock(id="file-123")
    client.parsing.parse.return_value = parse_result
    return client


# ── download_pdf ─────────────────────────────────────────────────────────────

def test_download_pdf_cache_hit(tmp_path: Path) -> None:
    pdf_url = "https://example.com/datasheet.pdf"
    slug = hashlib.sha256(pdf_url.encode()).hexdigest()[:16]
    cached = tmp_path / f"{slug}.pdf"
    cached.write_bytes(b"%PDF-1.4 cached")

    with patch("rag_case_products.ingest.pdf_loader.httpx.get") as mock_get:
        result = download_pdf(pdf_url, cache_dir=tmp_path)

    mock_get.assert_not_called()
    assert result == cached


def test_download_pdf_cache_miss(tmp_path: Path) -> None:
    pdf_url = "https://example.com/new.pdf"
    mock_resp = MagicMock()
    mock_resp.content = b"%PDF-1.4 fresh"

    with patch("rag_case_products.ingest.pdf_loader.httpx.get", return_value=mock_resp):
        result = download_pdf(pdf_url, cache_dir=tmp_path)

    assert result.exists()
    assert result.read_bytes() == b"%PDF-1.4 fresh"


# ── parse_pdf_to_markdown ─────────────────────────────────────────────────────

def test_parse_normal_two_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF")
    pages = [_ok_page("# Page one\n\nContent A.", 1), _ok_page("## Page two\n\nContent B.", 2)]
    result = _make_parse_result(pages)

    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=_make_client(result)):
        md = parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    assert "Content A." in md
    assert "Content B." in md
    # Verify cache file was written
    cache_file = tmp_path / "test.md"
    assert cache_file.exists()
    assert cache_file.read_text() == md


def test_parse_cache_hit_skips_llama_cloud(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cached.pdf"
    pdf_path.write_bytes(b"%PDF")
    cache_file = tmp_path / "cached.md"
    cache_file.write_text("# Cached content")

    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud") as mock_llama:
        md = parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    mock_llama.assert_not_called()
    assert md == "# Cached content"


def test_parse_raises_when_markdown_is_none(tmp_path: Path) -> None:
    pdf_path = tmp_path / "bad.pdf"
    pdf_path.write_bytes(b"%PDF")
    result = MagicMock()
    result.markdown = None  # API returned no markdown block

    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=_make_client(result)):
        with pytest.raises(RuntimeError, match="no markdown result"):
            parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    # Cache must NOT be written on failure
    assert not (tmp_path / "bad.md").exists()


def test_parse_raises_when_all_pages_failed(tmp_path: Path) -> None:
    pdf_path = tmp_path / "allfail.pdf"
    pdf_path.write_bytes(b"%PDF")
    pages = [_fail_page("corrupt page", 1), _fail_page("another error", 2)]
    result = _make_parse_result(pages)

    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=_make_client(result)):
        with pytest.raises(RuntimeError, match="empty markdown"):
            parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    assert not (tmp_path / "allfail.md").exists()


def test_parse_logs_warning_for_failed_pages(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pdf_path = tmp_path / "mixed.pdf"
    pdf_path.write_bytes(b"%PDF")
    pages = [_ok_page("# Good page\n\nContent.", 1), _fail_page("page 2 error", 2)]
    result = _make_parse_result(pages)

    import logging
    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=_make_client(result)):
        with caplog.at_level(logging.WARNING, logger="rag_case_products.ingest.pdf_loader"):
            md = parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    assert "Content." in md
    assert any("page 2 error" in r.message for r in caplog.records)


def test_parse_removes_noise(tmp_path: Path) -> None:
    pdf_path = tmp_path / "noisy.pdf"
    pdf_path.write_bytes(b"%PDF")
    noisy = "# Product\n\n© 2024 Axis Communications AB\nwww.axis.com\nReal content here."
    pages = [_ok_page(noisy, 1)]
    result = _make_parse_result(pages)

    with patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=_make_client(result)):
        md = parse_pdf_to_markdown(pdf_path, cache_dir=tmp_path)

    assert "Real content here." in md
    assert "Axis Communications" not in md
    assert "www.axis.com" not in md


# ── PdfProductLoader ─────────────────────────────────────────────────────────

def _load_with_tmp(pdf_url: str, pages: list, tmp_path: Path) -> Document:
    """Helper: run PdfProductLoader.load() with tmp_path caches."""
    result = _make_parse_result(pages)
    client = _make_client(result)
    with patch("rag_case_products.ingest.pdf_loader.httpx.get") as mock_get, \
         patch("rag_case_products.ingest.pdf_loader.LlamaCloud", return_value=client), \
         patch("rag_case_products.ingest.pdf_loader.PRODUCTS_RAW_DIR", tmp_path), \
         patch("rag_case_products.ingest.pdf_loader.PRODUCTS_PARSED_DIR", tmp_path):
        mock_get.return_value = MagicMock(content=b"%PDF")
        return PdfProductLoader().load(pdf_url)


def test_pdf_product_loader_returns_document(tmp_path: Path) -> None:
    pdf_url = "https://example.com/cam.pdf"
    pages = [_ok_page("# AXIS Q1234\n\nSpec content.", 1)]
    doc = _load_with_tmp(pdf_url, pages, tmp_path)

    assert isinstance(doc, Document)
    assert doc.metadata["doc_type"] == DocType.PRODUCT.value
    assert doc.metadata["source"] == pdf_url
    assert "content_hash" in doc.metadata
    assert "fetched_at" in doc.metadata
    assert doc.id_ == hashlib.sha256(pdf_url.encode()).hexdigest()[:16]
    assert "AXIS Q1234" in doc.text


# ── Hierarchical product path: metadata propagation + LLM-exclude invariant ──

_PRODUCT_MARKDOWN = """\
# AXIS Q3558-LVE Dome Camera

## Advanced 8 MP AI-powered dome camera

Built on ARTPEC-9 with outstanding image quality.

## Camera

Sensor: 1/1.2 progressive scan RGB CMOS
Resolution: Up to 3840x2160
Lens: Varifocal, F1.6

## Video

Compression: H.264, H.265, AV1
Frame rate: Up to 30 fps
"""


def _make_product_doc() -> Document:
    return Document(
        text=_PRODUCT_MARKDOWN,
        id_="prod-test-001",
        metadata={
            "doc_type": DocType.PRODUCT.value,
            "source": "https://example.com/q3558.pdf",
            "model_name": "AXIS Q3558-LVE",
            "category": "network_camera",
            "subcategory": "dome",
            "entity": '{"model_name": "AXIS Q3558-LVE", "specs": {}}',
            "content_hash": "abc123",
            "fetched_at": "2026-01-01T00:00:00+00:00",
        },
    )


def test_hierarchical_nodes_inherit_product_metadata() -> None:
    doc = _make_product_doc()
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])
    all_nodes = parser.get_nodes_from_documents([doc])
    leaf_nodes = get_leaf_nodes(all_nodes)

    assert len(leaf_nodes) > 0
    for node in leaf_nodes:
        assert node.metadata.get("model_name") == "AXIS Q3558-LVE"
        assert node.metadata.get("category") == "network_camera"
        assert node.metadata.get("source") == "https://example.com/q3558.pdf"


def test_hierarchical_nodes_llm_exclude_hides_entity() -> None:
    from llama_index.core.schema import MetadataMode

    doc = _make_product_doc()
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])
    all_nodes = parser.get_nodes_from_documents([doc])
    leaf_nodes = get_leaf_nodes(all_nodes)

    meta_excluded = ["entity", "content_hash", "fetched_at"]
    for node in all_nodes:
        node.excluded_embed_metadata_keys = meta_excluded
        node.excluded_llm_metadata_keys = meta_excluded

    for node in leaf_nodes:
        llm_content = node.get_content(metadata_mode=MetadataMode.LLM)
        assert "entity" not in llm_content
        assert "content_hash" not in llm_content
        # model_name and source are still visible to the LLM
        assert "model_name" in llm_content or "AXIS Q3558-LVE" in llm_content


def test_hierarchical_nodes_embed_exclude_hides_heavy_fields() -> None:
    from llama_index.core.schema import MetadataMode

    doc = _make_product_doc()
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])
    all_nodes = parser.get_nodes_from_documents([doc])

    meta_excluded = ["entity", "content_hash", "fetched_at"]
    for node in all_nodes:
        node.excluded_embed_metadata_keys = meta_excluded

    for node in all_nodes:
        embed_content = node.get_content(metadata_mode=MetadataMode.EMBED)
        assert "content_hash" not in embed_content
        assert "fetched_at" not in embed_content
