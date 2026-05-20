# SPEC — RAG Prototype (LlamaIndex Workflow + Agent Hybrid)

> Status: **Living document**. Updated to reflect the implemented product ingest redesign (PDF + HierarchicalNodeParser + AutoMergingRetriever).

## 1. Context

`rag_case_products_llamaindex/` starts from zero. Based on `CLAUDE.md`, the prototype
is an AI assistant that helps customers and partners search Axis Communications
**products** (network cameras, network speakers, radar, access control) and
**deployment case studies**.

Three query patterns are in scope:

- **Pattern A — Case Study search**: e.g. "Show retail case studies related to checkout monitoring."
- **Pattern B — Product search**: e.g. "List cameras with high waterproof resistance."
- **Pattern C — Hybrid**: e.g. "What operating temperatures are common for cameras used in factories?"

### Agreed design decisions

| Topic | Decision |
|---|---|
| Orchestration | **Workflow + tool-using Agent hybrid** |
| Index layout | **Separate `VectorStoreIndex`** for products and case studies |
| Product data source | **PDF datasheets** downloaded from `urls.txt`, parsed via **LlamaCloud agentic tier** |
| Case data source | **HTML (URL)**, converted to markdown via **markitdown** |
| Product chunking | **HierarchicalNodeParser** (2048/512/128 tokens) + **AutoMergingRetriever** |
| Case chunking | Semantic node builder (case_card / case_section / case_products / case_partners) + contextual prefix |
| Incremental ingest | Already-ingested URLs are **skipped by default**; `--force` re-ingests (reuses PDF/markdown cache) |
| Completion line | End-to-end runnable from **Chainlit**, with citations and an ingest CLI; the evaluation pipeline is deferred |

The goal of this prototype is a working end-to-end slice, not a feature-complete product.

## 2. Architecture Overview

```
                      ┌─────────────────────────────────────────┐
                      │           Chainlit (UI layer)           │
                      │   on_chat_start / on_message / cl.Step   │
                      └────────────────┬────────────────────────┘
                                       │ run(query) + stream events
                      ┌────────────────▼────────────────────────┐
                      │              RAG Workflow                │
                      │  StartEvent                              │
                      │    └─▶ analyze_query  (LLM, Pydantic)    │
                      │    └─▶ run_agent      (FunctionAgent)    │
                      │           ├─ tool: search_products       │
                      │           └─ tool: search_cases          │
                      │    └─▶ synthesize     (final answer)     │
                      │  StopEvent (AnswerBundle)                │
                      └────────────────┬────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                      ▼
   ┌──────────────────────┐                              ┌──────────────────┐
   │ Products Index       │                              │ Cases Index      │
   │ VectorStore (leaves) │                              │ VectorStore +    │
   │ DocStore (all nodes) │                              │ DocStore +       │
   │ AutoMergingRetriever │                              │ contextual nodes │
   └────────▲─────────────┘                              └────────▲─────────┘
            │                                                      │
   ┌────────┴───────────────────────────────────────────────────────┐
   │                  Ingest Pipeline (CLI, offline)                 │
   │  Products: PDF download → LlamaCloud parse → HierarchicalNode   │
   │    Parser (2048/512/128) → text-embedding-3-small (leaf nodes)  │
   │  Cases: markitdown (URL) → Entity extract → Node build →        │
   │    Contextual prefix → text-embedding-3-small                   │
   └──────────────────────────────────────────────────────────────────┘
```

### 2.1 Why a Workflow + Agent hybrid

- The **Workflow** gives explicit, observable steps (`analyze → run_agent → synthesize`).
  This makes progress easy to surface in Chainlit via `cl.Step`, and keeps the pipeline
  debuggable and testable.
- Retrieval is delegated to a LlamaIndex **`FunctionAgent`** that owns the
  `search_products` / `search_cases` tools and combines them itself. Pattern C
  ("retrieve from both and reconcile") is naturally handled by the agent's
  tool-use loop.
- Net effect: **deterministic frame, autonomous retrieval** — the bounded structure
  needed for the UI, with the flexibility needed for hybrid queries.

## 3. Directory Layout (to be created)

```
rag_case_products_llamaindex/
├── SPEC.md                             # this document
├── pyproject.toml                      # dependencies (see §9)
├── app.py                              # Chainlit entrypoint
├── data/
│   ├── products/
│   │   ├── urls.txt                    # product datasheet PDF URLs (1 per line)
│   │   ├── raw/                        # downloaded PDFs (gitignored)
│   │   └── parsed/                     # LlamaCloud-parsed markdown cache (gitignored)
│   └── cases/urls.txt                  # case study page URLs (1 URL per line)
├── storage/                            # persisted indices + manifest (gitignored)
│   ├── products/
│   │   ├── (docstore / vector_store / index_store …)
│   │   └── manifest.json               # record of ingested URLs
│   └── cases/
│       ├── (docstore / vector_store / index_store …)
│       └── manifest.json
├── src/rag_case_products/
│   ├── __init__.py
│   ├── config.py                       # Settings (model names, paths, top_k, chunk sizes)
│   ├── models.py                       # Pydantic models (§4)
│   ├── ingest/
│   │   ├── pdf_loader.py               # PDF download + LlamaCloud parse → Document
│   │   ├── loaders.py                  # markitdown URL wrapper (cases only)
│   │   ├── entities.py                 # Product / CaseStudy structured extraction
│   │   ├── node_builder.py             # case node generation (case_card / case_section / …)
│   │   ├── contextual.py               # contextual retrieval for case nodes (chunk + summary)
│   │   ├── manifest.py                 # ingest manifest read/write + skip decision
│   │   └── pipeline.py                 # build_and_persist_indices()
│   ├── retrieval/
│   │   ├── indices.py                  # load_products_index / load_cases_index
│   │   ├── reranker.py                 # cross-encoder rerank postprocessor
│   │   └── tools.py                    # QueryEngineTool (products / cases)
│   ├── workflow/
│   │   ├── events.py                   # workflow events
│   │   └── rag_workflow.py             # RagWorkflow(Workflow)
│   └── cli.py                          # `python -m rag_case_products.cli ingest`
└── tests/                              # pytest smoke tests
```

**UI separation invariant**: only `app.py` may import Chainlit. Nothing under
`src/rag_case_products/` depends on Chainlit (per CLAUDE.md "Minimal UI framework
dependencies"). The Workflow emits framework-agnostic events; the UI listens.

## 4. Data Model (`src/rag_case_products/models.py`)

All models are Pydantic v2.

### 4.1 Common

```python
class DocType(str, Enum):
    PRODUCT = "product"
    CASE    = "case"

class QueryPattern(str, Enum):
    CASE_SEARCH    = "A"
    PRODUCT_SEARCH = "B"
    HYBRID         = "C"

class QueryAnalysis(BaseModel):
    pattern: QueryPattern
    product_hints: list[str]      # e.g. ["P3268-LVE"]
    industry_hints: list[str]     # e.g. ["retail", "factory"]
    spec_hints: list[str]         # e.g. ["IP66", "FOV>120"]
    rewritten_query: str          # query-rewriting result

class Citation(BaseModel):
    doc_type: DocType
    title: str
    source: str                   # URL
    snippet: str

class RetrievedChunk(BaseModel):
    text: str
    score: float
    citation: Citation
```

`QueryAnalysis` is produced via the LLM's structured output (`structured_predict()` /
`as_structured_llm()`); it drives both hallucination control and pattern routing.

### 4.2 Domain types (extracted by the LLM at ingest time)

Design principle: **only identity / filter keys are typed fields**; the remaining
specs go into a `dict[str, Any]` bag. Spec items vary widely per product category
(camera FOV, speaker SPL, radar detection range), so a fixed schema is brittle —
while identifiers (`model_name`, `category`, `subcategory`) are stable.

```python
class ProductCategory(str, Enum):
    NETWORK_CAMERA  = "network_camera"
    NETWORK_SPEAKER = "network_speaker"
    RADAR           = "radar"
    ACCESS_CONTROL  = "access_control"

class Product(BaseModel):
    model_name: str               # "AXIS P3268-LVE"  — identifier
    category: ProductCategory     # coarse class (for filtering)
    subcategory: str | None       # "fixed" / "dome" / "ptz" / "bullet" / "modular" …
                                  #   free string, no fixed enum (extensible)
    specs: dict[str, Any]         # free-form bag: ip_rating, fov_horizontal_deg,
                                  #   operating_temp_c, resolution, features, …
    source_url: str
    raw_summary: str              # 2–3 line LLM description (fallback)

class CaseStudy(BaseModel):
    title: str
    industry: str                 # "retail" / "factory" / "parking" / … (for filtering)
    customer: str | None
    region: str | None
    deployment_year: int | None
    referenced_models: list[str]  # model names appearing in the story (Pattern C link)
    details: dict[str, Any]       # free-form bag: challenges / decision_factors / outcomes
    source_url: str
    raw_summary: str
```

### 4.3 Final output

```python
class AnswerBundle(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    used_pattern: QueryPattern
    # optional, filled when structured comparison is needed (UI renders as a table)
    products_compared: list[Product] | None = None
    cases_compared: list[CaseStudy] | None = None
```

### 4.4 Storage of extracted entities

- For each URL, the ingest pipeline extracts a `Product` or `CaseStudy` via the
  `gpt-4o-mini` structured output.
- The result is stored two ways:
  - full JSON dump in `Document.metadata["entity"]`
  - **filter keys only** copied to the top level of `Document.metadata`:
    - Product: `{doc_type, model_name, category, subcategory, source_url}`
    - CaseStudy: `{doc_type, industry, customer, deployment_year, source_url}`
- Nodes inherit these top-level metadata keys, providing the foundation for future
  `MetadataFilters`. The `specs` / `details` bags are **not** expanded to metadata —
  they stay inside the entity JSON, so a growing key set never pollutes search.

## 5. Ingest Pipeline (`src/rag_case_products/ingest/`)

### 5.1 Input

- `data/products/urls.txt` — direct PDF datasheet URLs (1 per line; `#` lines are comments).
  Example: `https://www.axis.com/dam/public/.../datasheet-axis-q3558-lve-…-en-US.pdf`
- `data/cases/urls.txt` — case study page URLs.

Downloaded PDFs are cached in `data/products/raw/`; parsed markdown in
`data/products/parsed/`. Both directories are gitignored.

### 5.2 Steps (CLI: `rag_case_products.cli ingest`)

Steps 1 and 6–7 are shared between products and cases.
Steps 2–5 differ by source.

1. **Manifest load** (`manifest.py`, `pipeline.py`)
   - Read `storage/<name>/manifest.json` into a map `url → {ingested_at, content_hash, doc_id, node_ids}`.
   - Without `--force`: diff `urls.txt` against the manifest and process **new URLs only**.
   - With `--force`: ignore the manifest, re-ingest every URL, and delete the existing
     nodes via `index.delete_ref_doc(doc_id)` before re-inserting. PDF and markdown
     caches are **reused** under `--force` (LlamaCloud costs; re-parse only by manually
     deleting `data/products/parsed/`).
   - With `--limit N`: each source's URL list is truncated to its first N entries.

2. **Load**
   - *Products* (`pdf_loader.py`): download PDF → LlamaCloud agentic parse → clean
     markdown → `Document`. Both the PDF and the parsed markdown are cached on disk;
     subsequent runs return the cached files without re-downloading or re-parsing.
     `doc.id_` is `sha256(pdf_url)[:16]`; `doc.metadata` gets `{doc_type, source, fetched_at, content_hash}`.
   - *Cases* (`loaders.py`): `markitdown` fetches the HTML page and converts it to
     markdown. `doc.id_` is `sha256(url)[:16]`; `doc.metadata` gets
     `{doc_type, source, title, fetched_at, content_hash}`.

3. **Entity extraction** (`entities.py`)
   - Per `doc_type`, extract a `Product` or `CaseStudy` with `gpt-4o-mini` structured output.
   - Store as `doc.metadata["entity"]` (JSON) and copy filter keys to top-level metadata (§4.4).
   - For products this step runs **before** chunking so that `model_name`, `category`, and
     `subcategory` are inherited by every hierarchical node automatically.

4. **Node build / chunking**
   - *Products* (`pipeline.py`): `HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])`
     creates a parent → child → leaf hierarchy. All nodes inherit `doc.metadata`
     (including entity filter keys). Heavy metadata fields (`entity`, `content_hash`,
     `fetched_at`) are excluded from embedding and LLM rendering via
     `excluded_embed_metadata_keys` / `excluded_llm_metadata_keys`.
   - *Cases* (`node_builder.py`): 7–9 typed nodes per §5.4. Every node carries
     `node_kind` ∈ `{case_card, case_section, case_products, case_partners}`.
     Cascading split: nodes exceeding ~1500 tokens are split with
     `SentenceSplitter(chunk_size=1024, chunk_overlap=100)`.

5. **Contextual prefix** (`contextual.py`) — **cases only**
   - For each case node, `gpt-4o-mini` generates a 1–2 sentence context; it is prepended
     to `node.text`. The original body is kept in `node.metadata["original_text"]`.
   - Product nodes do not get a contextual prefix; the hierarchical parent context serves
     the same role during AutoMerging.

6. **Embed & persist**
   - `Settings.embed_model = OpenAIEmbedding("text-embedding-3-small")`.
   - *Products*: **all** nodes (parent + child + leaf) are added to the docstore;
     **only leaf nodes** are inserted into the `VectorStoreIndex` (embedded). This is
     required for `AutoMergingRetriever` to look up parent nodes during merge.
   - *Cases*: all nodes inserted into the index as before.
   - `storage_context.persist("storage/products")` / `"storage/cases"`.

7. **Manifest update**
   - Append `{url, content_hash, doc_id, node_ids, entity_kind, ingested_at}` per URL.
   - `node_ids` records only **leaf node** IDs for products (those present in the vector index).
   - `content_hash` is the SHA256 of the parsed markdown — reserved for future content-change detection.

### 5.3 Product chunking

Products use `HierarchicalNodeParser` with three levels:

| Level | Token budget | Role |
|---|---|---|
| Parent | 2048 | Chapter-level context; returned by AutoMergingRetriever when multiple children match |
| Child | 512 | Section-level |
| Leaf | 128 | Sentence-level; the unit that is embedded and searched |

The PDF datasheet markdown (from LlamaCloud agentic parse) feeds the parser directly.
All nodes at every level carry the filter keys (`model_name`, `category`, `subcategory`,
`source`) inherited from `doc.metadata` via entity extraction.

`AutoMergingRetriever` merges retrieved leaf nodes up to their parent when enough
sibling leaves match, returning richer context to the LLM.

Note: this replaces the earlier semantic node taxonomy (`card`, `prose`, `spec`,
`analytics`, `procurement`) and the per-node contextual prefix. The hierarchical
structure provides equivalent recall coverage without category-specific parsing logic,
making it straightforward to extend to all product categories (cameras, speakers,
radar, access control) from a single PDF source.

### 5.4 Case Study chunking

Grounded in a study of `https://www.axis.com/customer-story/coned-drone-ptz`. One case URL → nodes:

| `node_kind` | count/case | content | purpose |
|---|---|---|---|
| `case_card` | 1 | extracted `CaseStudy` entity expanded into a template: `customer / industry / region / year / customer_need / challenge / solution / outcome / referenced_models / partners` | Pattern A identification; aggregation unit for cross-case trend analysis |
| `case_section` | 4–6 | narrative chapters split per H2 (incl. intro); pull quotes are kept inline, not split out | episode-detail queries |
| `case_products` | 1 | the "Products & solutions" section body | Pattern C, e.g. "case studies using AXIS Q62" matched on body text |
| `case_partners` | 0–1 | "Our partner organizations" list (standalone if ≥ 2 partners; folded into `case_card` otherwise) | ecosystem queries |
| related stories / "Get in touch" / footer / breadcrumb | **0 (skip)** | — | excludes navigational noise |

### 5.5 Contextual prefix templates — cases only

Embedding is computed once over the **prefixed** text with `text-embedding-3-small`.
`metadata["original_text"]` holds the prefix-free body; synthesis feeds the LLM that body.

Product nodes do **not** use a contextual prefix; `model_name` and `category` metadata
embedded alongside the text provide identity context, and the hierarchical merge gives
the LLM the surrounding section.

Case study (H2 headings are marketing-style and semantically weak, so the prefix
reinforces them with extracted-entity metadata):
```
[Case: {title} | Customer: {customer} | Industry: {industry} | Region: {region} | Year: {year} | Section: {section_heading} | Kind: {node_kind}]
{1-sentence chunk-level summary}

{original section body}
```

### 5.6 Cost estimate

`text-embedding-3-small` is $0.02 / 1M tokens. ~12 nodes/product × ~200 tokens ≈
2,400 tokens ≈ $0.00005/product; ~8 nodes/case × ~250 tokens ≈ 2,000 tokens ≈
$0.00004/case. Negligible at prototype scale.

### 5.7 CLI

```
python -m rag_case_products.cli ingest
    [--source products|cases|all]   # default: all
    [--limit N]                      # process at most the first N URLs per source
    [--force]                        # ignore manifest, re-ingest everything
    [--dry-run]                      # print the target URLs without ingesting
```

- Default: ingest new URLs only; already-ingested URLs are skipped (logged as
  "skip (already ingested)").
- `--force`: re-fetch all URLs, delete the old nodes from the docstore, re-insert.
- `--limit N`: truncate each source's URL list to its first N entries before the
  manifest diff — for small-scale verification (e.g. Gate 2). Default: no limit.
- `--dry-run`: no URL fetches, no writes — print the plan only.

## 6. Retrieval & Tools (`src/rag_case_products/retrieval/`)

### 6.1 Indices → QueryEngineTools (`tools.py`)

Each index is exposed as a `QueryEngineTool` for the agent:

- `search_products` — products index with `AutoMergingRetriever`:
  1. `index.as_retriever(similarity_top_k=8)` searches leaf nodes (128-token chunks).
  2. `AutoMergingRetriever` promotes to parent (512 or 2048 tokens) when enough siblings match.
  3. Cross-encoder rerank to `top_n=4`.
  Description steers the agent to use it for model specs, IP rating, field of view, operating temperature, etc.
- `search_cases` — cases index, `similarity_top_k=8`, cross-encoder rerank to `top_n=4`.
  Description steers the agent toward industries, customer challenges, selected products, and outcomes.

### 6.2 Reranker (`reranker.py`)

`SentenceTransformerRerank(model="cross-encoder/ms-marco-MiniLM-L-6-v2", top_n=4)`.
CPU is sufficient; the model downloads once. An LLM reranker is rejected for cost
and latency.

## 7. Workflow (`src/rag_case_products/workflow/`)

`RagWorkflow` is a 3-step LlamaIndex Workflow. Each step writes a `ProgressEvent`
to the stream via `ctx.write_event_to_stream()` for the UI.

```python
class RagWorkflow(Workflow):
    @step
    async def analyze_query(self, ctx, ev: StartEvent) -> QueryAnalyzedEvent:
        # gpt-4o-mini → structured QueryAnalysis (pattern + hints + rewritten query)
        ...

    @step
    async def run_agent(self, ctx, ev: QueryAnalyzedEvent) -> AgentDoneEvent:
        agent = FunctionAgent(
            tools=build_tools(),
            llm=OpenAI("gpt-4o-mini"),
            system_prompt=AGENT_SYSTEM_PROMPT,
        )
        # hints from QueryAnalysis are injected into the prompt;
        # tool choice is left to the model (Pattern C → both tools)
        ...

    @step
    async def synthesize(self, ctx, ev: AgentDoneEvent) -> StopEvent:
        # reconstruct citations, assemble AnswerBundle
        ...
```

### 7.1 Events (`events.py`)

```python
class QueryAnalyzedEvent(Event):
    analysis: QueryAnalysis

class AgentDoneEvent(Event):
    raw_answer: str
    chunks: list[RetrievedChunk]

class ProgressEvent(Event):       # UI-only, off the main workflow path
    step: str
    detail: str
```

### 7.2 Agent system prompt — key points

- Pattern A → prefer `search_cases`; Pattern B → prefer `search_products`; Pattern C → use both.
- Always preserve the citation source (URL) in the answer.
- When specs span multiple models, organize them as a table.
- Clearly distinguish explicit specifications from inferred information (CLAUDE.md).

## 8. Chainlit Integration (`app.py`)

- `on_chat_start`: construct `RagWorkflow` and store it in the user session.
- `on_message`: run the workflow, consume `stream_events()`; render each
  `ProgressEvent` as a `cl.Step`; await the final `AnswerBundle` and send the
  answer with citation elements.

The UI only listens to workflow events — it holds no business logic.

## 9. Dependencies (`pyproject.toml`)

```toml
dependencies = [
    "llama-index-core>=0.12",
    "llama-index-llms-openai",
    "llama-index-embeddings-openai",
    "llama-index-agent-openai",            # FunctionAgent
    "llama-index-postprocessor-sbert-rerank",
    "llama-cloud>=2.5.0",                  # LlamaCloud agentic PDF parse
    "markitdown",                           # case study HTML → markdown
    "httpx",                               # PDF download
    "chainlit",
    "pydantic>=2",
    "python-dotenv",
]

[tool.uv]
dev-dependencies = ["ruff", "pytest"]
```

Environment variables (`.env`):
- `OPENAI_API_KEY` — entity extraction, contextual summaries, query analysis, agent.
- `LLAMA_CLOUD_API_KEY` — PDF agentic parsing (products only).

## 10. End-to-End Verification

1. `uv sync` — resolve dependencies.
2. Add 2–3 URLs each to `data/products/urls.txt` and `data/cases/urls.txt`.
3. **First ingest**: `uv run python -m rag_case_products.cli ingest` — confirm
   `storage/products/` and `storage/cases/` contain index files plus `manifest.json`.
4. **Incremental behavior**: run the same command again — confirm "skip (already
   ingested)" is logged and no URL is re-fetched.
5. **Force re-ingest**: `uv run python -m rag_case_products.cli ingest --force` —
   confirm all URLs are re-fetched and old nodes are deleted then re-inserted.
6. `uv run chainlit run app.py -w` — in the browser:
   - Pattern A: "Show retail case studies on checkout monitoring." → only `search_cases` is called.
   - Pattern B: "Which outdoor cameras meet IP66 and IP67?" → only `search_products` is called.
   - Pattern C: "What operating temperature range do factory-deployed models have?" → both are called.
7. Confirm the answer shows citations (title + source URL).
8. Confirm `cl.Step` surfaces analyze / agent / synthesize progress in the UI.
9. `pytest tests/` — smoke tests pass (manifest skip decision, index load, mocked tool invocation).

## 11. Out of Scope (next phases)

- Full metadata filtering (structured extraction of industry, year, IP rating into filters).
- Evaluation pipeline (LlamaIndex `RetrieverEvaluator` + benchmark dataset).
- Multi-vendor support.
- Hybrid search (BM25 + vector).
- Content-change-aware incremental ingest (using `content_hash` in `manifest.json`).
- `--force` re-parse from scratch: current `--force` reuses the PDF/markdown cache.
  Full re-parse requires manual deletion of `data/products/parsed/` before `--force`.
