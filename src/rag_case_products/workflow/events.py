from llama_index.core.workflow import Event

from rag_case_products.models import QueryAnalysis, RetrievedChunk


class QueryAnalyzedEvent(Event):
    analysis: QueryAnalysis


class AgentDoneEvent(Event):
    raw_answer: str
    chunks: list[RetrievedChunk]


class ProgressEvent(Event):
    step: str
    detail: str
