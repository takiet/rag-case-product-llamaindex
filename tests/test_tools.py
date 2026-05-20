"""Tests for retrieval/tools.py — build_tools() with mocked indices."""

from unittest.mock import MagicMock, patch

import pytest

from rag_case_products.retrieval import tools as tools_module
from rag_case_products.retrieval.tools import build_tools


@pytest.fixture()
def mocked_indices(monkeypatch):
    """Patch index loading and the reranker so build_tools() needs no storage or network."""
    products_index = MagicMock(name="products_index")
    products_sc = MagicMock(name="products_sc")
    # products uses AutoMergingRetriever; as_retriever() provides the base retriever
    base_retriever = MagicMock(name="base_retriever")
    products_index.as_retriever.return_value = base_retriever

    cases_index = MagicMock(name="cases_index")
    cases_engine = MagicMock(name="cases_engine")
    cases_index.as_query_engine.return_value = cases_engine

    reranker = MagicMock(name="reranker")

    monkeypatch.setattr(tools_module, "build_reranker", lambda: reranker)
    monkeypatch.setattr(
        tools_module, "load_products_index", lambda: (products_index, products_sc)
    )
    monkeypatch.setattr(tools_module, "load_cases_index", lambda: cases_index)

    return {
        "products_index": products_index,
        "products_sc": products_sc,
        "base_retriever": base_retriever,
        "cases_index": cases_index,
        "cases_engine": cases_engine,
        "reranker": reranker,
    }


def test_build_tools_returns_two_named_tools(mocked_indices):
    with patch("rag_case_products.retrieval.tools.AutoMergingRetriever"), \
         patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        tools = build_tools()
    assert [t.metadata.name for t in tools] == ["search_products", "search_cases"]


def test_build_tools_descriptions_are_steering(mocked_indices):
    with patch("rag_case_products.retrieval.tools.AutoMergingRetriever"), \
         patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        by_name = {t.metadata.name: t.metadata.description.lower() for t in build_tools()}
    assert "product" in by_name["search_products"]
    assert "case" in by_name["search_cases"]


def test_products_uses_auto_merging_retriever(mocked_indices):
    with patch("rag_case_products.retrieval.tools.AutoMergingRetriever") as mock_amr, \
         patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        build_tools()
    mock_amr.assert_called_once_with(
        mocked_indices["base_retriever"],
        mocked_indices["products_sc"],
        verbose=False,
    )


def test_cases_uses_as_query_engine(mocked_indices):
    with patch("rag_case_products.retrieval.tools.AutoMergingRetriever"), \
         patch("rag_case_products.retrieval.tools.RetrieverQueryEngine") as mock_qe:
        mock_qe.from_args.return_value = MagicMock()
        build_tools()
    mocked_indices["cases_index"].as_query_engine.assert_called_once()
