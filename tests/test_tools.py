"""Tests for retrieval/tools.py — build_tools() with a mocked index and query engine."""

from unittest.mock import MagicMock

import pytest

from rag_case_products.config import SIMILARITY_TOP_K
from rag_case_products.retrieval import tools as tools_module
from rag_case_products.retrieval.tools import build_tools


@pytest.fixture()
def mocked_indices(monkeypatch):
    """Patch index loading and the reranker so build_tools() needs no storage or network."""
    products_engine = MagicMock(name="products_engine")
    cases_engine = MagicMock(name="cases_engine")

    products_index = MagicMock(name="products_index")
    products_index.as_query_engine.return_value = products_engine
    cases_index = MagicMock(name="cases_index")
    cases_index.as_query_engine.return_value = cases_engine

    reranker = MagicMock(name="reranker")

    monkeypatch.setattr(tools_module, "build_reranker", lambda: reranker)
    monkeypatch.setattr(tools_module, "load_products_index", lambda: products_index)
    monkeypatch.setattr(tools_module, "load_cases_index", lambda: cases_index)

    return {
        "products_engine": products_engine,
        "cases_engine": cases_engine,
        "products_index": products_index,
        "cases_index": cases_index,
        "reranker": reranker,
    }


def test_build_tools_returns_two_named_tools(mocked_indices):
    tools = build_tools()
    assert [t.metadata.name for t in tools] == ["search_products", "search_cases"]


def test_build_tools_descriptions_are_steering(mocked_indices):
    by_name = {t.metadata.name: t.metadata.description.lower() for t in build_tools()}
    assert "product" in by_name["search_products"]
    assert "case" in by_name["search_cases"]


def test_build_tools_configures_query_engine(mocked_indices):
    build_tools()
    expected = {
        "similarity_top_k": SIMILARITY_TOP_K,
        "node_postprocessors": [mocked_indices["reranker"]],
    }
    mocked_indices["products_index"].as_query_engine.assert_called_once_with(**expected)
    mocked_indices["cases_index"].as_query_engine.assert_called_once_with(**expected)


def test_tool_invocation_routes_to_its_query_engine(mocked_indices):
    tools = {t.metadata.name: t for t in build_tools()}
    tools["search_products"]("cameras with IP66 rating")
    mocked_indices["products_engine"].query.assert_called_once_with("cameras with IP66 rating")
    mocked_indices["cases_engine"].query.assert_not_called()
