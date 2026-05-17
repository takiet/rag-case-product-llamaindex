"""3-step RAG workflow: analyze_query → run_agent → synthesize."""

from llama_index.core import PromptTemplate, Settings
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.agent.workflow.workflow_events import ToolCallResult
from llama_index.core.base.response.schema import Response
from llama_index.core.schema import NodeWithScore
from llama_index.core.workflow import Context, StartEvent, StopEvent, Workflow, step
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from rag_case_products.config import EMBED_MODEL, LLM_MODEL
from rag_case_products.models import (
    AnswerBundle,
    Citation,
    DocType,
    QueryAnalysis,
    QueryPattern,
    RetrievedChunk,
)
from rag_case_products.retrieval.tools import build_tools
from rag_case_products.workflow.events import (
    AgentDoneEvent,
    ProgressEvent,
    QueryAnalyzedEvent,
)

_ANALYZE_PROMPT = PromptTemplate(
    "You are a query router for an Axis Communications product and case study RAG system.\n"
    "Classify the user query into one of three patterns:\n"
    "  A = Case Study search (industries, deployments, customer challenges, outcomes)\n"
    "  B = Product search (camera specs, model names, IP rating, FOV, operating temperature)\n"
    "  C = Hybrid (spans both products and deployment cases)\n\n"
    "Return a JSON-structured analysis with:\n"
    "  - pattern: 'A', 'B', or 'C'\n"
    "  - product_hints: list of product model names mentioned\n"
    "  - industry_hints: list of industries mentioned (retail, factory, parking, etc.)\n"
    "  - spec_hints: list of specification constraints (IP66, FOV>120, etc.)\n"
    "  - rewritten_query: a cleaner, search-optimised version of the query\n\n"
    "User query: {query}\n"
)

AGENT_SYSTEM_PROMPT = (
    "You are an expert assistant for Axis Communications products and deployment case studies.\n\n"
    "TOOL SELECTION RULES:\n"
    "  Pattern A (case study question): call search_cases only.\n"
    "  Pattern B (product question):    call search_products only.\n"
    "  Pattern C (hybrid question):     call BOTH tools and reconcile the results.\n\n"
    "Current query pattern: {pattern}\n"
    "Pattern description: {pattern_detail}\n"
    "Hints — products: {product_hints} | industries: {industry_hints} | specs: {spec_hints}\n\n"
    "RESPONSE RULES:\n"
    "  1. Preserve every citation: include the source URL for every fact you state.\n"
    "     Format: 'Source: <url>'\n"
    "  2. When comparing specs across models, format as a markdown table.\n"
    "  3. Clearly distinguish explicit specifications from inferences.\n"
    "  4. Be concise unless the user asks for detailed analysis.\n"
)

_PATTERN_DETAIL: dict[QueryPattern, str] = {
    QueryPattern.CASE_SEARCH: "Case study search — use search_cases only",
    QueryPattern.PRODUCT_SEARCH: "Product search — use search_products only",
    QueryPattern.HYBRID: "Hybrid — call both search_cases AND search_products",
}


def _configure_settings() -> None:
    """Pin embed model and LLM in LlamaIndex Settings to avoid lazy OpenAI defaults."""
    Settings.embed_model = OpenAIEmbedding(model=EMBED_MODEL)
    Settings.llm = OpenAI(model=LLM_MODEL)


def _nodes_to_chunks(nodes: list[NodeWithScore]) -> list[RetrievedChunk]:
    chunks = []
    for n in nodes:
        meta = n.node.metadata or {}
        content = n.node.get_content()
        doc_type_raw = meta.get("doc_type", "product")
        try:
            doc_type = DocType(doc_type_raw)
        except ValueError:
            doc_type = DocType.PRODUCT
        citation = Citation(
            doc_type=doc_type,
            title=meta.get("title", meta.get("model_name", "Unknown")),
            source=meta.get("source", meta.get("source_url", "")),
            snippet=content[:300],
        )
        chunks.append(
            RetrievedChunk(
                text=content,
                score=n.score or 0.0,
                citation=citation,
            )
        )
    return chunks


def _deduplicate_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    unique = []
    for c in citations:
        if c.source not in seen:
            seen.add(c.source)
            unique.append(c)
    return unique


class RagWorkflow(Workflow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _configure_settings()
        # Build tools once at construction; indices + reranker load is expensive.
        self._tools = build_tools()

    @step
    async def analyze_query(self, ctx: Context, ev: StartEvent) -> QueryAnalyzedEvent:
        query: str = ev.get("query", "")
        ctx.write_event_to_stream(
            ProgressEvent(step="analyze_query", detail=f"Analysing query: {query!r}")
        )
        analysis: QueryAnalysis = await Settings.llm.astructured_predict(
            QueryAnalysis,
            _ANALYZE_PROMPT,
            query=query,
        )
        ctx.write_event_to_stream(
            ProgressEvent(
                step="analyze_query",
                detail=f"Pattern: {analysis.pattern.value} | rewrite: {analysis.rewritten_query!r}",
            )
        )
        await ctx.set("used_pattern", analysis.pattern.value)
        return QueryAnalyzedEvent(analysis=analysis)

    @step
    async def run_agent(self, ctx: Context, ev: QueryAnalyzedEvent) -> AgentDoneEvent:
        analysis = ev.analysis
        ctx.write_event_to_stream(
            ProgressEvent(
                step="run_agent",
                detail=f"Running agent (pattern {analysis.pattern.value})",
            )
        )

        system_prompt = AGENT_SYSTEM_PROMPT.format(
            pattern=analysis.pattern.value,
            pattern_detail=_PATTERN_DETAIL[analysis.pattern],
            product_hints=", ".join(analysis.product_hints) or "none",
            industry_hints=", ".join(analysis.industry_hints) or "none",
            spec_hints=", ".join(analysis.spec_hints) or "none",
        )

        agent = FunctionAgent(
            tools=self._tools,
            llm=Settings.llm,
            system_prompt=system_prompt,
        )

        handler = agent.run(user_msg=analysis.rewritten_query)

        source_nodes: list[NodeWithScore] = []
        async for agent_ev in handler.stream_events():
            if isinstance(agent_ev, ToolCallResult):
                tool_out = agent_ev.tool_output
                raw = getattr(tool_out, "raw_output", None)
                if isinstance(raw, Response) and raw.source_nodes:
                    source_nodes.extend(raw.source_nodes)

        agent_output = await handler
        raw_answer: str = agent_output.response.content or ""

        chunks = _nodes_to_chunks(source_nodes)

        ctx.write_event_to_stream(
            ProgressEvent(
                step="run_agent",
                detail=f"Agent done. Retrieved {len(chunks)} chunks.",
            )
        )
        return AgentDoneEvent(raw_answer=raw_answer, chunks=chunks)

    @step
    async def synthesize(self, ctx: Context, ev: AgentDoneEvent) -> StopEvent:
        ctx.write_event_to_stream(
            ProgressEvent(step="synthesize", detail="Assembling answer bundle")
        )

        pattern_raw: str = await ctx.get("used_pattern", default=QueryPattern.HYBRID.value)
        try:
            used_pattern = QueryPattern(pattern_raw)
        except ValueError:
            used_pattern = QueryPattern.HYBRID

        citations = _deduplicate_citations([c.citation for c in ev.chunks])

        bundle = AnswerBundle(
            answer_markdown=ev.raw_answer,
            citations=citations,
            used_pattern=used_pattern,
        )
        ctx.write_event_to_stream(
            ProgressEvent(
                step="synthesize",
                detail=f"Done. Citations: {len(citations)}",
            )
        )
        return StopEvent(result=bundle)
