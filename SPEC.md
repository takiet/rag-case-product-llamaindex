# SPEC — RAG Prototype (LlamaIndex Workflow + Agent Hybrid)

> Status: **Design draft**. This document is the agreed design for the first prototype.
> It precedes implementation; source layout and signatures below are the build target.

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
| Data source | **HTML (URL) only**, converted to markdown via **markitdown** (PDF out of scope) |
| Incremental ingest | Already-ingested URLs are **skipped by default**; `--force` re-ingests |
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
   ┌──────────────────┐                                  ┌──────────────────┐
   │ Products Index   │                                  │ Cases Index      │
   │ VectorStore +    │                                  │ VectorStore +    │
   │ DocStore +       │                                  │ DocStore +       │
   │ contextual nodes │                                  │ contextual nodes │
   └────────▲─────────┘                                  └────────▲─────────┘
            │                                                      │
   ┌────────┴───────────────────────────────────────────────────────┐
   │                  Ingest Pipeline (CLI, offline)                 │
   │  markitdown (URL) → Entity extract → Node build → Contextual     │
   │  prefix → text-embedding-3-small → SimpleVectorStore persist()   │
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
│   ├── products/urls.txt               # product page URLs (1 URL per line)
│   └── cases/urls.txt                  # case study URLs (1 URL per line)
├── storage/                            # persisted indices + manifest (gitignored)
│   ├── products/
│   │   ├── (docstore / vector_store / index_store …)
│   │   └── manifest.json               # record of ingested URLs
│   └── cases/
│       ├── (docstore / vector_store / index_store …)
│       └── manifest.json
├── src/rag_case_products/
│   ├── __init__.py
│   ├── config.py                       # Settings (model names, paths, top_k)
│   ├── models.py                       # Pydantic models (§4)
│   ├── ingest/
│   │   ├── loaders.py                  # markitdown URL wrapper
│   │   ├── entities.py                 # Product / CaseStudy structured extraction
│   │   ├── node_builder.py             # node generation (card / prose / spec / …)
│   │   ├── contextual.py               # contextual retrieval (chunk + summary)
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

- `data/products/urls.txt` (1 URL per line; lines starting with `#` are comments)
- `data/cases/urls.txt`

PDF ingestion is out of scope for this prototype. `loaders.py` exposes a `UrlLoader`
seam so a different loader can be swapped in later.

### 5.2 Steps (CLI: `rag_case_products.cli ingest`)

1. **Manifest load** (`manifest.py`, `pipeline.py`)
   - Read `storage/<name>/manifest.json` into a map `url → {ingested_at, content_hash, doc_id, node_ids}`.
   - Without `--force`: diff `urls.txt` against the manifest and process **new URLs only**.
   - With `--force`: ignore the manifest, re-ingest every URL, and delete the existing
     nodes for each URL via `docstore.delete_document(doc_id)` before re-inserting
     (prevents duplicates).
   - With `--limit N`: each source's `urls.txt` is truncated to its first N URLs
     before the manifest diff (caps products and cases independently).
2. **Load** (`loaders.py`)
   - `markitdown` (`MarkItDown().convert(url)`) fetches each URL and converts the HTML
     page to markdown; the result is wrapped in a LlamaIndex `Document`.
   - `doc.id_` is a stable URL-derived id (`sha256(url)[:16]`); `doc.metadata` gets
     `{doc_type, source, title, fetched_at}`.
3. **Entity extraction** (`entities.py`)
   - Per `doc_type`, extract a `Product` or `CaseStudy` with the `gpt-4o-mini`
     structured output.
   - Store as `doc.metadata["entity"]` (JSON) and copy filter keys to top-level metadata (§4.4).
4. **Node build** (`node_builder.py`)
   - Product → 10–14 nodes per §5.3; CaseStudy → 7–9 nodes per §5.4.
   - Every node inherits `doc_id`, `source`, the filter keys, and a `node_kind` ∈
     `{card, prose, spec, analytics, procurement, case_card, case_section, case_products, case_partners}`.
   - Cascading split: only if a node exceeds ~1500 tokens, apply
     `SentenceSplitter(chunk_size=1024, chunk_overlap=100)`.
5. **Contextual prefix** (`contextual.py`)
   - For each node, `gpt-4o-mini` generates a 1–2 sentence context; it is prepended
     to `node.text`. The original body (without prefix) is kept in
     `node.metadata["original_text"]`.
   - The prompt only sees document excerpts (per CLAUDE.md "avoid hallucinations").
6. **Embed & persist**
   - `Settings.embed_model = OpenAIEmbedding("text-embedding-3-small")`.
   - If an index already exists, `load_index_from_storage()` then `index.insert_nodes(new_nodes)`;
     otherwise build fresh.
   - `storage_context.persist("storage/products")` / `"storage/cases"`.
7. **Manifest update**
   - Append `{url, content_hash, doc_id, node_ids, entity_kind, ingested_at}` per URL and write back.
   - `content_hash` is the SHA256 of the markitdown markdown — kept for a future
     "URL unchanged but content changed" check (currently used only for new-URL detection).

### 5.3 Product chunking

Grounded in a study of `https://www.axis.com/products/axis-q3558-lve`. One product URL → nodes:

| `node_kind` | count/product | content | purpose |
|---|---|---|---|
| `card` | 1 | extracted `Product` entity expanded into a template: `model_name / category / subcategory / key specs (resolution, FOV, IP rating, operating temp) / 1–2 line summary` | product identification / overview for Pattern B & C |
| `prose` | 3–5 | upper marketing sections split per H2 ("Outstanding image quality", "ARTPEC-9 …", "Powerful video and audio analytics", "Robust with strong security") | context not in the spec table — chipset, codec (AV1), encryption (FIPS 140-3) |
| `spec` | ~10 | "Technical specifications" split per H3 subgroup (Camera / Video / Lens / Pan-Tilt-Zoom / Compression / Audio / Network / Security / General / Sustainability); each node is ~3–5 key/value rows | pinpoint spec queries ("operating temperature?", "FOV?") |
| `analytics` | 1 | included + supported analytics apps enumerated | Pattern C, e.g. "outdoor 4K cameras supporting Object Analytics" |
| `procurement` | 1 | part-number table (`03206-001`, regions) | procurement queries |
| Accessories | **0 (skip)** | 80+ navigational links — excluded from embedding | avoids inflating the corpus with noise |

### 5.4 Case Study chunking

Grounded in a study of `https://www.axis.com/customer-story/coned-drone-ptz`. One case URL → nodes:

| `node_kind` | count/case | content | purpose |
|---|---|---|---|
| `case_card` | 1 | extracted `CaseStudy` entity expanded into a template: `customer / industry / region / year / customer_need / challenge / solution / outcome / referenced_models / partners` | Pattern A identification; aggregation unit for cross-case trend analysis |
| `case_section` | 4–6 | narrative chapters split per H2 (incl. intro); pull quotes are kept inline, not split out | episode-detail queries |
| `case_products` | 1 | the "Products & solutions" section body | Pattern C, e.g. "case studies using AXIS Q62" matched on body text |
| `case_partners` | 0–1 | "Our partner organizations" list (standalone if ≥ 2 partners; folded into `case_card` otherwise) | ecosystem queries |
| related stories / "Get in touch" / footer / breadcrumb | **0 (skip)** | — | excludes navigational noise |

### 5.5 Contextual prefix templates

Embedding is computed once over the **prefixed** text with `text-embedding-3-small`.
`metadata["original_text"]` holds the prefix-free body; synthesis feeds the LLM that body.

Product:
```
[Product: {model_name} | Category: {category}/{subcategory} | Section: {section_path} | Kind: {node_kind}]
{1-sentence chunk-level summary}

{original section body}
```

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

- `search_products` — products index, `similarity_top_k=8`, cross-encoder rerank to
  `top_n=4`. Description steers the agent to use it for model specs, IP rating,
  field of view, operating temperature, etc.
- `search_cases` — cases index, same retrieval shape. Description steers the agent
  toward industries, customer challenges, selected products, and outcomes.

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
    "markitdown",
    "chainlit",
    "pydantic>=2",
    "python-dotenv",
]

[tool.uv]
dev-dependencies = ["ruff", "pytest"]
```

Environment variable: `OPENAI_API_KEY`, managed via `.env`. markitdown needs no API
key — HTML-to-markdown conversion runs locally.

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
- Non-camera product ingest (radar / network speaker / access control). The §5.3
  chunking design is grounded in camera product pages; other categories render
  their specs as datasheet-only links or unlabelled tables, which the camera-shaped
  node builder cannot segment. The prototype product corpus is therefore scoped to
  network cameras (`data/products/urls.txt`); non-camera URLs are kept commented
  out. Generalising `node_builder` per category re-enables them.
