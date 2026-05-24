"""Tests for retrieval/tools.py — build_tools() with mocked indices."""

from unittest.mock import MagicMock, patch

import pytest
from llama_index.core.vector_stores.types import VectorStoreQueryMode

from rag_case_products.config import CASES_MMR_THRESHOLD, CASES_MMR_TOP_K, SIMILARITY_TOP_K
from rag_case_products.retrieval import tools as tools_module
from rag_case_products.retrieval.tools import build_tools


@pytest.fixture()
def mocked_indices(monkeypatch):
    """Patch index loading and the reranker so build_tools() needs no storage or network."""
    products_index = MagicMock(name="products_index")
    products_retriever = MagicMock(name="products_retriever")
    products_index.as_retriever.return_value = products_retriever

    cases_index = MagicMock(name="cases_index")
    cases_retriever = MagicMock(name="cases_retriever")
    cases_index.as_retriever.return_value = cases_retriever

    reranker = MagicMock(name="reranker")

    monkeypatch.setattr(tools_module, "build_reranker", lambda: reranker)
    monkeypatch.setattr(tools_module, "load_products_index", lambda: products_index)
    monkeypatch.setattr(tools_module, "load_cases_index", lambda: cases_index)

    return {
        "products_index": products_index,
        "products_retriever": products_retriever,
        "cases_index": cases_index,
        "cases_retriever": cases_retriever,
        "reranker": reranker,
    }


def test_build_tools_returns_two_named_tools(mocked_indices):
    with patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        tools = build_tools()
    assert [t.metadata.name for t in tools] == ["search_products", "search_cases"]


def test_build_tools_descriptions_are_steering(mocked_indices):
    with patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        by_name = {t.metadata.name: t.metadata.description.lower() for t in build_tools()}
    assert "product" in by_name["search_products"]
    assert "case" in by_name["search_cases"]


def test_products_uses_plain_retriever(mocked_indices):
    with patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        build_tools()
    mocked_indices["products_index"].as_retriever.assert_called_once_with(
        similarity_top_k=SIMILARITY_TOP_K,
    )


def test_cases_uses_mmr_retriever(mocked_indices):
    with patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        build_tools()

    mocked_indices["cases_index"].as_query_engine.assert_not_called()
    mocked_indices["cases_index"].as_retriever.assert_called_once_with(
        similarity_top_k=CASES_MMR_TOP_K,
        vector_store_query_mode=VectorStoreQueryMode.MMR,
        vector_store_kwargs={"mmr_threshold": CASES_MMR_THRESHOLD},
    )
