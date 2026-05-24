"""3-step RAG workflow: analyze_query → run_agent → synthesize."""

from llama_index.core import PromptTemplate, Settings
from llama_index.core.agent.workflow import ReActAgent
from llama_index.core.agent.workflow.workflow_events import ToolCall, ToolCallResult
from llama_index.core.base.response.schema import Response
from llama_index.core.memory import ChatMemoryBuffer
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
    SourceItem,
    SourceReasons,
)
from rag_case_products.retrieval.tools import build_tools
from rag_case_products.workflow.events import (
    AgentDoneEvent,
    ProgressEvent,
    QueryAnalyzedEvent,
)

_ANALYZE_PROMPT = PromptTemplate(
    "You are a query analyser for an Axis Communications product and case study RAG system.\n"
    "Analyse the user query and return:\n"
    "  - pattern: 'A' (case study focus), 'B' (product/spec focus), or 'C' (both)\n"
    "    This is for analytics only — the agent will decide its own retrieval strategy.\n"
    "  - product_hints: list of product model names mentioned\n"
    "  - industry_hints: list of industries mentioned (retail, factory, parking, etc.)\n"
    "  - spec_hints: list of specification constraints (IP66, FOV>120, etc.)\n"
    "  - rewritten_query: a self-contained, search-optimised version of the query.\n"
    "    Resolve any pronouns or references (e.g. 'it', 'those', 'that model') using\n"
    "    the conversation history below.\n"
    "    IMPORTANT — for hybrid queries that combine a deployment context\n"
    "    (factory, retail, parking, outdoor, etc.) WITH a product specification\n"
    "    (operating temperature, IP rating, FOV, resolution, etc.),\n"
    "    rewrite as explicit multi-step retrieval instructions, for example:\n"
    "      'Step 1: Search case studies for [deployment context] deployments to identify\n"
    "       which camera models are actually used there.\n"
    "       Step 2: Look up [specification] for those specific camera models.'\n"
    "    For simple single-domain queries (product-only or case-only), write a single\n"
    "    search-optimised query as usual.\n\n"
    "Conversation history (most recent turns):\n"
    "{history}\n\n"
    "Current query: {query}\n"
)

AGENT_SYSTEM_PROMPT = (
    "You are an expert assistant for Axis Communications products and deployment case studies.\n\n"
    "TOOLS:\n"
    "  search_cases    — searches deployment case studies:\n"
    "                    industries, customer challenges, selected products, outcomes\n"
    "  search_products — Axis product data catalogue (datasheet-level detail):\n"
    "                    * Identification: model name, product category, subcategory\n"
    "                    * Optics & image: resolution, sensor size, FOV, focal length,\n"
    "                      min illumination, IR range, WDR range\n"
    "                    * Environmental ratings: IP66/IP67/IP69, IK (vandal resistance),\n"
    "                      NEMA rating, operating temperature range, humidity\n"
    "                    * Analytics: AXIS Object Analytics (line crossing, tailgating,\n"
    "                      counting, PPE), AXIS Audio Analytics (glass break, scream,\n"
    "                      shout), third-party ACAP app support\n"
    "                    * Deployment traits: indoor/outdoor suitability, power (PoE/DC),\n"
    "                      mounting options, IR illumination reach\n"
    "                    * Integration: ONVIF profiles, VMS compatibility, VAPIX API\n"
    "                    Use for spec lookup, capability comparison, or finding cameras\n"
    "                    that match specific technical or environmental requirements.\n\n"
    "RETRIEVAL STRATEGY:\n"
    "  Think before each tool call. Use the results of earlier calls to refine later ones.\n"
    "  Examples of multi-step reasoning:\n"
    "  - 'Which cameras are used in factories?' → search_cases('factory deployment') →\n"
    "    extract product names → search_products('<model names> specifications')\n"
    "  - 'IP66 cameras in outdoor retail' → search_products('IP66 outdoor camera') AND\n"
    "    search_cases('outdoor retail deployment') → cross-reference\n"
    "  You may call each tool multiple times with different queries if needed.\n\n"
    "CONTEXT:\n"
    "  Hints — products: {product_hints} | industries: {industry_hints} | specs: {spec_hints}\n\n"
    "RESPONSE RULES:\n"
    "  1. Include source URL for every factual claim. Format: 'Source: <url>'\n"
    "  2. Distinguish explicit specifications from inferences.\n"
    "  3. Be concise unless the user asks for detailed analysis.\n\n"
    "RESPONSE FORMAT (follow strictly — do not write specs as prose):\n"
    "  - Open with ONE direct sentence answering the query.\n"
    "  - Specifications for a single model → markdown table:\n"
    "      | Specification | Value |\n"
    "      |---|---|\n"
    "      | Focal length | … |\n"
    "  - Specifications across 2+ models → table with model names as columns:\n"
    "      | Specification | Model A | Model B |\n"
    "      |---|---|---|\n"
    "  - Case study results → numbered list, one case per item.\n"
    "  - Use ### headings only when the answer has 2+ distinct sections\n"
    "    (e.g. ### Recommended Models, ### Deployment Examples).\n"
    "  - Close with a brief ### Recommendation only when the query asks for one.\n"
)


_FORMAT_HINTS: dict[QueryPattern, str] = {
    QueryPattern.CASE_SEARCH: (
        "Structure your answer as follows:\n"
        "1. One sentence summarising the overall finding.\n"
        "2. A numbered list of matching case studies. For each case include:\n"
        "   - **Title** — customer / region / industry\n"
        "   - Challenge: what problem the customer faced\n"
        "   - Solution: how Axis products addressed it\n"
        "   - Outcome: measurable or qualitative result\n"
        "Do not use specification tables for case study answers."
    ),
    QueryPattern.PRODUCT_SEARCH: (
        "Structure your answer as follows:\n"
        "1. A short paragraph (2-4 sentences) introducing the product(s) "
        "and summarising key highlights in plain language.\n"
        "2. A markdown table for the detailed specifications:\n"
        "   - Single model → | Specification | Value |\n"
        "   - Multiple models → | Specification | Model A | Model B |\n"
        "Do not bury spec values inside prose sentences."
    ),
    QueryPattern.HYBRID: (
        "Structure your answer with clearly labelled sections:\n"
        "### Deployment Examples\n"
        "Numbered list of relevant case studies (title, challenge, solution, outcome).\n"
        "### Product Match\n"
        "Short paragraph on matching products, followed by a spec table if specs are relevant.\n"
        "### Recommendation (optional)\n"
        "Only include if the query asks for a recommendation."
    ),
}


_SOURCE_SUMMARY_PROMPT = PromptTemplate(
    "You are summarising why each retrieved source was relevant to the user's query.\n"
    "For each source below, write exactly one reason string (a single sentence under "
    "25 words) explaining its relevance.\n"
    "Return a JSON object with a 'reasons' array, one string per source, in the same "
    "order as the sources listed.\n\n"
    "User query: {query}\n\n"
    "Sources:\n{sources_text}\n"
)


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
            source=meta.get("source", ""),
            snippet=meta.get("original_text", content)[:300],
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
        # Shared memory persists across turns for multi-turn conversation context.
        self._memory = ChatMemoryBuffer.from_defaults(token_limit=4096)

    @step
    async def analyze_query(self, ctx: Context, ev: StartEvent) -> QueryAnalyzedEvent:
        query: str = ev.get("query", "")
        ctx.write_event_to_stream(
            ProgressEvent(step="analyze_query", detail=f"Analysing query: {query!r}")
        )

        # Format recent conversation history so the analyser can resolve references.
        history_messages = self._memory.get()
        if history_messages:
            history = "\n".join(
                f"{m.role.value}: {m.content[:300]}" for m in history_messages[-6:]
            )
        else:
            history = "No previous conversation."

        analysis: QueryAnalysis = await Settings.llm.astructured_predict(
            QueryAnalysis,
            _ANALYZE_PROMPT,
            query=query,
            history=history,
        )
        ctx.write_event_to_stream(
            ProgressEvent(
                step="analyze_query",
                detail=f"Pattern: {analysis.pattern.value} | rewrite: {analysis.rewritten_query!r}",
            )
        )
        await ctx.store.set("used_pattern", analysis.pattern.value)
        await ctx.store.set("original_query", query)
        return QueryAnalyzedEvent(analysis=analysis)

    @step
    async def run_agent(self, ctx: Context, ev: QueryAnalyzedEvent) -> AgentDoneEvent:
        analysis = ev.analysis
        ctx.write_event_to_stream(
            ProgressEvent(step="run_agent", detail="Planning retrieval strategy...")
        )

        system_prompt = AGENT_SYSTEM_PROMPT.format(
            product_hints=", ".join(analysis.product_hints) or "none",
            industry_hints=", ".join(analysis.industry_hints) or "none",
            spec_hints=", ".join(analysis.spec_hints) or "none",
        )

        agent = ReActAgent(
            tools=self._tools,
            llm=Settings.llm,
            system_prompt=system_prompt,
            max_iterations=10,
            memory=self._memory,
        )

        format_hint = _FORMAT_HINTS[analysis.pattern]
        user_msg = analysis.rewritten_query + f"\n\n[Format]\n{format_hint}"
        handler = agent.run(user_msg=user_msg)

        source_nodes: list[NodeWithScore] = []
        async for agent_ev in handler.stream_events():
            if isinstance(agent_ev, ToolCall):
                query_input = agent_ev.tool_kwargs.get("input", "")
                ctx.write_event_to_stream(ProgressEvent(
                    step="run_agent",
                    detail=f"[{agent_ev.tool_name}] query: {query_input!r}",
                ))
            elif isinstance(agent_ev, ToolCallResult):
                tool_out = agent_ev.tool_output
                raw = getattr(tool_out, "raw_output", None)
                if isinstance(raw, Response) and raw.source_nodes:
                    source_nodes.extend(raw.source_nodes)
                    titles = [
                        n.node.metadata.get("title", n.node.metadata.get("model_name", "?"))
                        for n in raw.source_nodes[:5]
                    ]
                    ctx.write_event_to_stream(ProgressEvent(
                        step="run_agent",
                        detail=(
                            f"[{agent_ev.tool_name}] {len(raw.source_nodes)} chunks"
                            f" — {', '.join(titles)}"
                        ),
                    ))

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

        pattern_raw: str = await ctx.store.get("used_pattern", default=QueryPattern.HYBRID.value)
        try:
            used_pattern = QueryPattern(pattern_raw)
        except ValueError:
            used_pattern = QueryPattern.HYBRID

        citations = _deduplicate_citations([c.citation for c in ev.chunks])

        source_items: list[SourceItem] = []
        if citations:
            original_query: str = await ctx.store.get("original_query", default="")
            sources_text = "\n".join(
                f"{i + 1}. [{c.doc_type.value}] {c.title}: {c.snippet[:200]}"
                for i, c in enumerate(citations)
            )
            summary: SourceReasons = await Settings.llm.astructured_predict(
                SourceReasons,
                _SOURCE_SUMMARY_PROMPT,
                query=original_query,
                sources_text=sources_text,
            )
            reasons = summary.reasons
            if len(reasons) < len(citations):
                reasons = reasons + [""] * (len(citations) - len(reasons))
            elif len(reasons) > len(citations):
                reasons = reasons[: len(citations)]
            source_items = [
                SourceItem(
                    title=c.title,
                    doc_type=c.doc_type,
                    source=c.source,
                    reason=r,
                )
                for c, r in zip(citations, reasons)
            ]

        bundle = AnswerBundle(
            answer_markdown=ev.raw_answer,
            citations=citations,
            used_pattern=used_pattern,
            source_items=source_items,
        )
        ctx.write_event_to_stream(
            ProgressEvent(
                step="synthesize",
                detail=f"Done. Citations: {len(citations)}",
            )
        )
        return StopEvent(result=bundle)
