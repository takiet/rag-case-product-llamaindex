# RAG Case Products — LlamaIndex Prototype

AI assistant for searching Axis Communications **product specifications** and **deployment case studies**.  
Built with LlamaIndex, Chainlit, and OpenAI.

## Overview

The assistant supports three query patterns:

| Pattern | Description | Example |
|---------|-------------|---------|
| A — Case Search | Deployment examples, industry trends | "Show retail case studies for checkout monitoring" |
| B — Product Search | Specs, comparisons, capabilities | "Which cameras have IP66 and support 4K?" |
| C — Hybrid | Link products to deployment contexts | "What operating temperatures are common in factory deployments?" |

The pattern is recorded as analytics metadata only. The agent autonomously
decides which tools to call based on the rewritten query — hybrid queries are
rewritten into explicit multi-step retrieval instructions during query analysis.

### Architecture

```
User query
    ↓
analyze_query  ── gpt-4o-mini + chat history → QueryAnalysis
                  (pattern, hints, rewritten query;
                   hybrid queries expanded to multi-step instructions)
    ↓
run_agent     ── ReActAgent (gpt-4o-mini) with ChatMemoryBuffer
                  ├─ tool: search_products → VectorRetriever + rerank
                  └─ tool: search_cases    → MMR retriever + rerank
                  Thought → Action → Observation loop (up to 10 iterations)
    ↓
synthesize    ── AnswerBundle (markdown + citations)
    ↓
Chainlit UI   ── per-step cl.Step with tool calls and chunk titles
```

Ingest (offline):

```
data/*/urls.txt
    ↓ download + parse (cached under data/*/raw, data/*/parsed)
Markdown documents
    ↓ embed (text-embedding-3-small)
VectorStoreIndex
  products: MarkdownNodeParser → one node per heading section
  cases:    semantic node builder + contextual prefix
```

## Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- `OPENAI_API_KEY` — embeddings (`text-embedding-3-small`) and LLM (`gpt-4o-mini`)
- `LLAMA_CLOUD_API_KEY` — PDF parsing for products (LlamaCloud agentic tier)

## Setup

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env   # then fill in API keys
```

`.env` file:

```
OPENAI_API_KEY=sk-...
LLAMA_CLOUD_API_KEY=llx-...
```

## Data

URL lists live under `data/`:

```
data/
  products/urls.txt   # one PDF datasheet URL per line (# for comments)
  cases/urls.txt      # one case study URL per line
```

## Ingest

The ingest pipeline downloads, parses, embeds, and stores documents.  
Intermediate files are cached so re-runs skip already-processed steps.

```
data/products/raw/      ← downloaded PDFs
data/products/parsed/   ← markdown parsed by LlamaCloud (cached)
data/cases/raw/         ← downloaded HTML
data/cases/parsed/      ← markdown converted by MarkItDown (cached)
storage/products/       ← VectorStoreIndex (embeddings)
storage/cases/          ← VectorStoreIndex (embeddings)
```

### Commands

```bash
# Ingest everything (incremental — skips already-ingested URLs)
uv run python -m rag_case_products.cli ingest

# Ingest one source
uv run python -m rag_case_products.cli ingest --source cases
uv run python -m rag_case_products.cli ingest --source products

# Test with a small subset
uv run python -m rag_case_products.cli ingest --limit 5

# Preview what would be processed (no writes)
uv run python -m rag_case_products.cli ingest --dry-run

# Force re-ingest all URLs (deletes existing nodes, re-embeds)
uv run python -m rag_case_products.cli ingest --force
```

### Rebuild the index without re-parsing

When the parsed markdown files already exist under `data/*/parsed/`, you can delete
and rebuild the vector index without re-downloading or re-parsing any documents.
This is useful after changing chunking parameters, retrieval config, or embeddings.

```bash
# Delete only the vector index (keep cached markdown)
rm -rf storage/

# Re-ingest — reads from data/*/parsed/*.md, no network calls
uv run python -m rag_case_products.cli ingest
```

For **products** this skips the LlamaCloud PDF parse API call.  
For **cases** this skips the HTTP fetch and MarkItDown conversion.

To rebuild a single source:

```bash
rm -rf storage/cases/
uv run python -m rag_case_products.cli ingest --source cases
```

## Run the App

```bash
uv run chainlit run app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Tests

```bash
uv run pytest
```

## Project Structure

```
src/rag_case_products/
  config.py              # all constants (models, paths, retrieval params)
  ingest/
    pipeline.py          # orchestration: download → parse → embed → store
    loaders.py           # UrlLoader: HTML fetch + MarkItDown (cases)
    pdf_loader.py        # PdfProductLoader: PDF download + LlamaCloud parse
    node_builder.py      # semantic node splitting for case studies
    contextual.py        # contextual prefix injection
    entities.py          # Pydantic entity extraction (Product, Case)
    manifest.py          # ingest manifest (tracks processed URLs)
  retrieval/
    indices.py           # load persisted VectorStoreIndex from storage/
    tools.py             # build QueryEngineTools (products + cases)
    reranker.py          # cross-encoder reranker (SentenceTransformer)
  workflow/
    rag_workflow.py      # 3-step LlamaIndex Workflow (analyze → retrieve → synthesize)
    events.py            # ProgressEvent for Chainlit streaming
app.py                   # Chainlit UI entrypoint
data/
  products/urls.txt
  cases/urls.txt
storage/                 # persisted vector indices (gitignored)
```
