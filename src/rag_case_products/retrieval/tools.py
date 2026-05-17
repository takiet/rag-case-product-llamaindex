from llama_index.core.tools import QueryEngineTool

from rag_case_products.config import SIMILARITY_TOP_K
from rag_case_products.retrieval.indices import load_cases_index, load_products_index
from rag_case_products.retrieval.reranker import build_reranker

_PRODUCTS_DESCRIPTION = (
    "Search the Axis Communications product catalogue. "
    "Use this tool for queries about camera models, specifications (resolution, "
    "field of view, IP rating, operating temperature, compression formats), "
    "analytics support, or product comparisons. "
    "Input should be a natural-language question about products."
)

_CASES_DESCRIPTION = (
    "Search Axis Communications deployment case studies. "
    "Use this tool for queries about industries, customer challenges, deployment "
    "environments, selected products within a case, partner organisations, and "
    "outcomes or benefits. "
    "Input should be a natural-language question about real-world deployments."
)


def build_tools() -> list[QueryEngineTool]:
    reranker = build_reranker()

    products_engine = load_products_index().as_query_engine(
        similarity_top_k=SIMILARITY_TOP_K,
        node_postprocessors=[reranker],
    )
    cases_engine = load_cases_index().as_query_engine(
        similarity_top_k=SIMILARITY_TOP_K,
        node_postprocessors=[reranker],
    )

    return [
        QueryEngineTool.from_defaults(
            query_engine=products_engine,
            name="search_products",
            description=_PRODUCTS_DESCRIPTION,
        ),
        QueryEngineTool.from_defaults(
            query_engine=cases_engine,
            name="search_cases",
            description=_CASES_DESCRIPTION,
        ),
    ]
