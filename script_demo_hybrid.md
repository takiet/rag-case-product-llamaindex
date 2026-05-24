# Interview Presentation Script

> **How to use this document**
> - The **Main Script** is what you say out loud — simple, conversational English.
> - The **Technical Supplement** is your backup reference if the interviewer asks deeper questions.
> - Sections marked `[SHOW SCREEN]` are moments to point at the live demo.

---

## Main Script

### Opening (30 seconds)

> "Thank you for your time.
> Today I would like to show you a prototype AI assistant I built.
> It helps users search product specifications and real-world deployment case studies.
> The domain I used is network cameras and security devices from Axis Communications —
> but the architecture is general enough to apply to other domains."

---

### What Problem Does It Solve? (1 minute)

> "Imagine a sales engineer or a partner who needs to answer a customer question like:
> 'What cameras are being used in factory environments, and what operating temperature
> do they support?'
>
> This is actually a hard question.
> It requires finding relevant case studies first, then looking up product specifications
> for the cameras mentioned in those cases.
>
> Normally, a person would search two or three different documents manually.
> This assistant does that in one step, automatically."

---

### How It Works — Big Picture (1 minute)

> "The system has two main parts: an **ingest pipeline** and a **query pipeline**.
>
> The ingest pipeline runs offline. It downloads product datasheets — as PDFs —
> and case study web pages, converts them to text, extracts key information,
> and stores them as searchable vector embeddings.
>
> The query pipeline runs in real time when a user asks a question.
> It has three steps: analyze the question, retrieve relevant documents, and write the answer.
>
> Let me show you the interface."

---

### Demo Walkthrough (3–4 minutes)

`[SHOW SCREEN — Chainlit chat UI]`

> "This is the chat interface. The user just types a question in natural language."

---

**Demo Query 1 — Product search**

Type: *"Which cameras support outdoor installation and have an IP67 rating?"*

> "I typed a product question. Watch the left side — you can see the system's progress in real time.
> First it analyzes the question. Then it calls the product search tool.
> Finally it writes the answer with citations — you can see the source URL for each fact."

`[Point to the step indicators and citations in the UI]`

---

**Demo Query 2 — Case study search**

Type: *"Show me retail deployments related to checkout monitoring."*

> "This time I asked about case studies. The system searched a different index —
> one that stores real customer stories. It summarized the relevant cases and showed me
> the source pages."

---

**Demo Query 3 — Hybrid query**

Type: *"What operating temperatures are common for cameras used in factory deployments?"*

> "This is the interesting one. The question connects two things: case studies and product specs.
>
> Watch what happens — the system first searches case studies to find cameras used in factories.
> Then it automatically searches the product index to look up operating temperatures for those models.
> It does this in two steps, by itself, without me telling it to.
>
> The final answer combines information from both sources, with citations."

`[Point to the multi-step tool calls in the UI]`

---

### Key Design Points (1–2 minutes)

> "Let me highlight three things I am particularly proud of.

> **First: Structure-aware chunking for product specs.**
> Product datasheets are parsed from PDF into clean markdown by LlamaCloud,
> and then split at heading boundaries — one chunk per specification section,
> like 'Lens', 'Operating conditions', or 'Video compression'.
> This means a query about field of view hits exactly the Lens section,
> not a fragment that starts mid-table.

> **Second: Contextual retrieval for case studies.**
> Each chunk from a case study gets a short summary prepended before embedding.
> This improves search accuracy because the embedding captures 'what this chunk is about'
> rather than just the raw text.

> **Third: An autonomous retrieval agent inside a structured workflow.**
> I used a ReAct agent for retrieval, which means the system can plan and execute
> multi-step searches on its own — like the factory camera example you just saw.
> But the agent runs inside a fixed three-step workflow, so it is still observable
> and debuggable. Progress is always visible in the UI."

---

### Closing (30 seconds)

> "This prototype shows an end-to-end RAG system — from data ingest to a usable chat interface.
> I chose LlamaIndex because it provides clean abstractions for workflows and retrieval strategies,
> and Chainlit because it makes streaming progress easy to show to users.
>
> I am happy to go deeper into any part of the system.
> Thank you."

---
---

## Technical Supplement

> Use this section to answer follow-up questions. Do not read it as a script.

---

### Architecture

The system is a **LlamaIndex Workflow + ReAct Agent hybrid**.

```
User query
    ↓
[Step 1] analyze_query
    → gpt-4o-mini generates a QueryAnalysis (Pydantic)
    → classifies the query as Pattern A (case), B (product), or C (hybrid) — for analytics only
    → rewrites the query into retrieval instructions (e.g. multi-step for hybrid)
    → maintains ChatMemoryBuffer so follow-up questions resolve pronouns ("those cameras")

[Step 2] run_agent
    → ReActAgent with two tools: search_products and search_cases
    → Thought → Action → Observation loop (up to 10 iterations)
    → decides autonomously which tool(s) to call and in what order

[Step 3] synthesize
    → assembles citations and markdown answer into AnswerBundle

Chainlit UI
    → streams ProgressEvent from each step as cl.Step
    → renders the final AnswerBundle with citation links
```

The pattern classification (A/B/C) is **analytics metadata only**. The agent decides tool selection on its own based on the rewritten query and the tool descriptions. This keeps the routing flexible for hybrid queries without hard-coded branching logic.

---

### Ingest Pipeline

#### Products (PDF datasheets)

1. Download PDF → cache to `data/products/raw/`
2. Parse via **LlamaCloud agentic tier** → clean markdown → cache to `data/products/parsed/`
3. Entity extraction with `gpt-4o-mini` structured output → `Product` (Pydantic model)
   - Filter keys (`model_name`, `category`, `subcategory`) written to `Document.metadata`
   - Full specs stored as `metadata["entity"]` (not expanded — avoids metadata pollution)
4. **MarkdownNodeParser** — splits at H1/H2 heading boundaries
   - One node per specification section (e.g. Lens, Video, Operating conditions)
   - `doc.metadata` (including `model_name`, `category`) propagated to every node
   - Heavy fields (`entity`, `content_hash`, `fetched_at`, `source`) excluded from embedding/LLM rendering
5. Embed all nodes with `text-embedding-3-small`

#### Cases (HTML pages)

1. Fetch HTML → **markitdown** → markdown
2. Entity extraction → `CaseStudy` (Pydantic model)
3. Semantic node builder → typed nodes:
   - `case_card`: structured summary of the whole case (identity, challenge, outcome)
   - `case_section`: narrative chapters (one per H2)
   - `case_products`: "Products & solutions" section body
   - `case_partners`: partner list (if significant)
4. **Contextual prefix** injected per node before embedding:
   ```
   [Case: {title} | Customer: {customer} | Industry: {industry} | ...]
   {1-sentence chunk-level summary}

   {original section body}
   ```
   - Embedding computed over the prefixed text; LLM receives the original body only
5. Embed all nodes with `text-embedding-3-small`

#### Manifest

`storage/<source>/manifest.json` tracks ingested URLs with `{url, content_hash, doc_id, node_ids, ingested_at}`.
- Default run: skip already-ingested URLs
- `--force`: re-ingest all, delete old nodes first
- `--limit N`: test with a small subset

---

### Retrieval Strategy

| Tool | Index | Retriever | Post-processing |
|------|-------|-----------|-----------------|
| `search_products` | Products VectorStoreIndex | Plain vector retriever (top 10) | Cross-encoder rerank, top 5 |
| `search_cases` | Cases VectorStoreIndex | MMR (top 30, threshold 0.5) for diversity | Cross-encoder rerank, top 5 |

**Plain vector retriever (products)**: each node already corresponds to one specification section, so no merging is needed. A heading-aligned chunk is the right granularity for both embedding and LLM rendering.

**MMR (Maximal Marginal Relevance)**: balances relevance and diversity. With 30 candidates and threshold 0.5, it avoids returning five chunks from the same case study.

**Cross-encoder rerank**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (CPU, local). Chosen over an LLM reranker to minimize cost and latency.

---

### Data Model (Key Pydantic Types)

```python
class QueryAnalysis(BaseModel):
    pattern: QueryPattern          # A / B / C — analytics only
    rewritten_query: str           # drives the agent
    product_hints: list[str]       # e.g. ["P3268-LVE"]
    industry_hints: list[str]      # e.g. ["retail", "factory"]
    spec_hints: list[str]          # e.g. ["IP66", "FOV>120"]

class Product(BaseModel):
    model_name: str
    category: ProductCategory      # network_camera / speaker / radar / access_control
    subcategory: str | None        # fixed / dome / ptz / bullet / modular …
    specs: dict[str, Any]          # open bag — avoids brittle fixed schema
    source_url: str

class CaseStudy(BaseModel):
    title: str
    industry: str
    customer: str | None
    region: str | None
    deployment_year: int | None
    referenced_models: list[str]   # key for Pattern C linking
    details: dict[str, Any]        # challenges / decision_factors / outcomes

class AnswerBundle(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    used_pattern: QueryPattern
    source_items: list[SourceItem]
```

The `specs` / `details` bags are intentionally schema-free. Product specs vary widely across categories (camera FOV, speaker SPL, radar range). Typing only the stable identity keys keeps the model extensible without migrations.

---

### Technology Choices and Rationale

| Choice | Rationale |
|--------|-----------|
| **LlamaIndex Workflow** | Explicit, observable steps; easy to surface progress in UI via `ProgressEvent` |
| **ReAct Agent for retrieval** | Multi-step tool use without hard-coded routing; handles hybrid queries naturally |
| **MarkdownNodeParser** | Product datasheets have clean heading structure from LlamaCloud; section-aligned chunks match query granularity without merging complexity |
| **Contextual prefix (cases)** | Case section headings are marketing-style and weak semantically; prefix reinforces retrieval signal |
| **LlamaCloud PDF parse** | Handles complex PDF layouts (tables, multi-column) that plain text extractors miss |
| **markitdown (cases)** | Lightweight HTML-to-markdown; no browser dependency |
| **ChatMemoryBuffer** | Enables follow-up questions within a session; re-instantiated per `on_chat_start` for session isolation |
| **Chainlit** | First-class streaming + `cl.Step` for per-step progress; minimal glue code |
| **gpt-4o-mini** | Sufficient quality for structured extraction and agent reasoning at low cost |
| **text-embedding-3-small** | Best cost/performance for retrieval at prototype scale |

---

### Design Constraints and Trade-offs

**UI separation invariant**: only `app.py` imports Chainlit. The workflow and retrieval modules are framework-agnostic. This makes the core logic testable without a running UI.

**No metadata filtering yet**: filter keys (`category`, `industry`, `deployment_year`) are stored in node metadata but not yet applied as hard filters during retrieval. Adding `MetadataFilters` is the next iteration.

**Evaluation deferred**: no retrieval benchmark exists yet. The evaluation pipeline (`RetrieverEvaluator` + benchmark dataset) is listed as a future enhancement.

**`--force` reuses PDF/markdown cache**: re-ingesting does not re-download or re-parse. To force a full re-parse, delete `data/products/parsed/` manually before running `--force`. This preserves LlamaCloud API cost at prototype scale.

---

### Running the Project

```bash
# Install
uv sync

# Set up keys
cp .env.example .env   # fill in OPENAI_API_KEY and LLAMA_CLOUD_API_KEY

# Ingest (incremental)
uv run python -m rag_case_products.cli ingest

# Start UI
uv run chainlit run app.py

# Tests
uv run pytest
```

---
---

## Anticipated Interview Questions

> Use this section as a reference for follow-up questions. Answer conversationally — do not read verbatim.

---

### On Chunking Strategy

**Q: Why did you use MarkdownNodeParser instead of a fixed-size splitter?**

> Product datasheets have a consistent heading structure — one section per specification category.
> A fixed-size splitter like 512 tokens would cut across section boundaries: the end of the 'Lens' section might end up in the same chunk as the beginning of 'Video compression'.
> When a user asks about field of view, that chunk is noise around the real answer.
> With MarkdownNodeParser, each chunk is exactly one spec category, so the retrieved context is clean and unambiguous.

---

**Q: Did you consider SemanticSplitterNodeParser?**

> Yes. Semantic splitting groups sentences by embedding similarity rather than headings.
> It works well for long narrative text without explicit structure.
> But product datasheets are already structured — the headings are the semantic boundaries.
> Semantic splitting would call the embedding API on every sentence during ingest, which adds cost and latency for no gain here.
> MarkdownNodeParser is faster, deterministic, and better suited to this document type.

---

**Q: What happened to HierarchicalNodeParser and AutoMergingRetriever?**

> That was my initial approach. The idea was to retrieve small leaf nodes for precision,
> then merge sibling leaves into a larger parent if multiple matched the same section.
> In practice, the leaf nodes were 128 tokens each — small enough to cut mid-table in a datasheet.
> And AutoMerging only triggers when multiple sibling leaves match simultaneously,
> which is rare for narrow spec queries.
> MarkdownNodeParser gives a more predictable chunk size that naturally aligns with the document structure,
> so I dropped the merging step entirely.

---

### On Retrieval Strategy

**Q: Why MMR for cases but plain vector search for products?**

> For case studies, diversity matters. If a user asks about retail deployments,
> you want examples from different customers and environments — not five chunks from the same story.
> MMR penalizes redundancy in the candidate set, which helps here.
> For products, the user is typically asking about one spec at a time.
> Diversity is less important, and each product's spec sections are already distinct nodes,
> so plain similarity search is sufficient.

---

**Q: Why a cross-encoder for reranking instead of an LLM or a larger bi-encoder?**

> A cross-encoder looks at the query and each candidate chunk together, which gives more accurate relevance scores than embedding similarity alone.
> Compared to an LLM reranker, it is much faster and cheaper — it runs locally on CPU in under a second.
> For a prototype, that trade-off made sense. In production, I would evaluate whether the quality gap justifies switching to an LLM reranker.

---

**Q: How does contextual retrieval work for case studies?**

> Each case study chunk gets a short prefix injected before embedding — something like
> "Case: Factory safety upgrade | Customer: Acme Manufacturing | Industry: factory".
> That prefix is baked into the embedding vector, so similarity search can find the chunk
> even if the user's query uses different wording than what appears in the raw text.
> The LLM receives only the original body, not the prefix, to avoid repetition in the answer.

---

### On the Agent Design

**Q: Why use a ReAct agent for retrieval rather than hard-coded routing?**

> Hard-coded routing works for clear-cut cases: if the query is about a product, call the product tool.
> But hybrid queries are ambiguous — "What operating temperatures are common in factory cameras?" starts in cases and ends in products.
> A ReAct agent can plan multi-step sequences autonomously: search cases first, extract model names, then search products.
> It also handles edge cases naturally, like queries that turn out to need only one tool despite looking hybrid.

---

**Q: Isn't an autonomous agent harder to debug and control?**

> That is a fair concern, and why the agent runs inside a fixed three-step LlamaIndex Workflow.
> The workflow enforces the analyze → retrieve → synthesize structure.
> The agent has autonomy only within the retrieval step.
> Every tool call and observation is surfaced as a streaming event in the UI, so the user can see exactly what the agent did.
> If the agent fails, the workflow catches the exception cleanly.

---

### On Quality and Evaluation

**Q: How do you know the retrieval quality is good?**

> Honestly, I don't have a quantitative benchmark yet — that is the most significant gap in this prototype.
> I validated manually: I ran a set of representative queries across each pattern (A, B, C),
> checked that the retrieved chunks were relevant, and verified that citations pointed to the right sources.
> The next step would be building a benchmark dataset — a set of queries with ground-truth relevant documents —
> and running LlamaIndex's `RetrieverEvaluator` to get hit-rate and MRR metrics.

---

**Q: What would you improve if you had more time?**

> Three things in priority order.
> First, metadata filtering: the filter keys are stored but not applied. Adding hard filters on `category` or `industry` would cut noise significantly.
> Second, an evaluation pipeline with a real benchmark so I can measure retrieval quality objectively and catch regressions.
> Third, multi-vendor support — right now the data is Axis-only. Extending to other vendors would require normalizing product categories across brands, which is a non-trivial schema problem.

---

### On Technology Choices

**Q: Why LlamaIndex over LangChain?**

> Both are capable. I chose LlamaIndex because its `Workflow` abstraction maps cleanly to the three-step pipeline I had in mind.
> Each step is an explicit class with typed inputs and outputs, which makes the flow easy to trace and test.
> LlamaIndex also has first-class support for the node-level metadata and retriever patterns I needed —
> things like `excluded_embed_metadata_keys` and `AutoMergingRetriever` are built in rather than custom.

---

**Q: Why Chainlit for the UI?**

> Chainlit has built-in support for streaming and `cl.Step`, which maps directly to my workflow steps.
> I can surface each agent thought, tool call, and observation as a collapsible step in the UI with almost no glue code.
> For a demo where showing the process is as important as showing the answer, that was the right fit.

---

**Q: Why LlamaCloud for PDF parsing instead of a local parser?**

> Product datasheets have complex layouts — multi-column text, tables with merged cells, footnotes.
> Local extractors like PyMuPDF produce reasonable plain text but lose table structure.
> LlamaCloud's agentic tier preserves tables as markdown, which means spec values stay next to their labels after chunking.
> For a prototype the API cost is acceptable; in production I would evaluate whether the quality difference justifies the ongoing cost.
