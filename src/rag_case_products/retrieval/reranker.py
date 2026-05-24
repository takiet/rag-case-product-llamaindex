from llama_index.core.utils import infer_torch_device
from llama_index.postprocessor.sbert_rerank import SentenceTransformerRerank

from rag_case_products.config import RERANK_MODEL, RERANK_TOP_N


def build_reranker() -> SentenceTransformerRerank:
    # SentenceTransformerRerank passes device=None to super().__init__() when not
    # specified, which fails Pydantic's str validation. Resolve the device here
    # so it's always a concrete string.
    return SentenceTransformerRerank(
        model=RERANK_MODEL,
        top_n=RERANK_TOP_N,
        device=infer_torch_device(),
    )
