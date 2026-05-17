from llama_index.postprocessor.sbert_rerank import SentenceTransformerRerank
from llama_index.postprocessor.sbert_rerank.base import infer_torch_device

from rag_case_products.config import RERANK_MODEL, RERANK_TOP_N


def build_reranker() -> SentenceTransformerRerank:
    # device must be an explicit string; infer_torch_device() picks cpu/mps/cuda
    return SentenceTransformerRerank(
        model=RERANK_MODEL,
        top_n=RERANK_TOP_N,
        device=infer_torch_device(),
    )
