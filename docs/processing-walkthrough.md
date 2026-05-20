# Processing Walkthrough — Step by Step, with Worked Examples

> Companion to `SPEC.md`. `SPEC.md` is the *design*; this document traces what the
> *implemented code* actually does, one step at a time, following a real piece of
> data through every transformation. Line references point at the current source.
>
> **Note:** The product ingest path was redesigned. Products now use PDF datasheets
> parsed by LlamaCloud + `HierarchicalNodeParser` + `AutoMergingRetriever`.
> Cases are unchanged. Sections that differ per source are labelled **(products)**
> or **(cases)**.

The system has **two independent pipelines**. They never run at the same time and
they only meet at one place — the persisted index on disk.

```
                         OFFLINE                                 ONLINE
            ┌──────────────────────────────────┐   ┌──────────────────────────────┐
   urls.txt │  Ingest pipeline (CLI)            │   │  Query pipeline (Chainlit)   │
      ──────▶  markitdown → entity → nodes →    │   │  analyze → agent → synthesize│
            │  contextual prefix → embed        │   │                              │
            └───────────────┬──────────────────┘   └───────────────▲──────────────┘
                            │ persist()                            │ load_index_from_storage()
                            ▼                                       │
                    ┌───────────────────────────────────────────────┐
                    │  storage/products/   storage/cases/            │
                    │  docstore.json · default__vector_store.json    │
                    │  index_store.json · manifest.json              │
                    └───────────────────────────────────────────────┘
```

- **Part 1 — Ingest pipeline**: `python -m rag_case_products.cli ingest`. Turns a
  list of URLs into two searchable `VectorStoreIndex`es on disk. Run by a developer.
- **Part 2 — Query pipeline**: `chainlit run app.py`. Turns a user question into a
  cited answer. Run per chat message.

Throughout, the worked example follows one real product page —
`https://www.axis.com/products/axis-q3558-lve` — and one real case study —
`https://www.axis.com/customer-story/knoch-school-acs` — using values taken
verbatim from the committed `storage/` indices.

---

# Part 1 — Ingest Pipeline (offline)

Entry point: `src/rag_case_products/cli.py` → `build_and_persist_indices()` in
`src/rag_case_products/ingest/pipeline.py`. SPEC §5.2 calls these "steps 1–7".

Command used for the worked example:

```bash
uv run python -m rag_case_products.cli ingest --source products --limit 1
```

`--source products` ingests only the products corpus; `--limit 1` caps it to the
first URL in `data/products/urls.txt`, which is the Q3558-LVE page.

## Step 0 — CLI parses arguments

`cli.py:_build_parser()` defines the `ingest` sub-command and four flags:

| Flag | Effect | Default |
|---|---|---|
| `--source` | `products` / `cases` / `all` | `all` |
| `--limit N` | process at most the first N URLs **per source** | none |
| `--force` | ignore the manifest, re-ingest everything | off |
| `--dry-run` | print the plan, fetch/write nothing | off |

`main()` calls `build_and_persist_indices(source, limit, force, dry_run)`. Before
anything else, the pipeline pins the embedding model
(`pipeline.py:154`):

```python
Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")
```

This is a LlamaIndex global. Pinning it here means no later call can silently fall
back to a different default embedding model — the index would become unsearchable
if the query side embedded with a different model.

`build_and_persist_indices` then loops over the selected sources. For each it
resolves a `(urls_file, storage_dir, doc_type)` triple from `_SOURCE_CONFIG` and
calls `_ingest_source(...)`. The worked example resolves to:

```
urls_file   = data/products/urls.txt
storage_dir = storage/products
doc_type    = DocType.PRODUCT
```

## Step 1 — Read URLs, load the manifest, decide what to skip

**Read URLs** — `pipeline.py:_read_urls()` reads the file, drops blank lines and
`#` comments, and truncates to `--limit`:

```
data/products/urls.txt  (133 lines, ~99 active URLs)
   ↓ strip comments + blanks
   ↓ apply --limit 1
to_process candidate = ["https://www.axis.com/products/axis-q3558-lve"]
```

**Load the manifest** — `Manifest(storage_dir)` reads
`storage/products/manifest.json`. The manifest is a map `url → record`. A corrupt
or empty file is tolerated — `manifest.py:18` catches `JSONDecodeError` and starts
empty rather than crashing the run.

**Skip decision** — `pipeline.py:71-77`:

- **Without `--force`**: keep only URLs where `manifest.is_new(url)` is true
  (`manifest.py:23` — simply `url not in self._data`). Already-ingested URLs are
  dropped and logged as `skip (already ingested): N URL(s)`.
- **With `--force`**: keep every URL; old nodes are deleted later in Step 6.

For the worked example, suppose the Q3558-LVE URL is *not yet* in the manifest, so
it survives the diff. (On a second run it would be skipped — see "Incremental
behaviour" below.)

**`--dry-run`** stops here: it logs the URLs it *would* process and returns without
fetching or writing anything (`pipeline.py:79-83`).

**Open or create the index** — `pipeline.py:91-98`. The pipeline checks for
`storage/products/docstore.json`:

- exists → `load_index_from_storage()` — append to the existing index.
- absent → build a fresh empty `VectorStoreIndex(nodes=[])`.

> Why `docstore.json` and not `vector_store.json`? `SimpleVectorStore` persists
> under the name `default__vector_store.json`. An earlier version checked for
> `vector_store.json`, never found it, and so rebuilt an empty index every run —
> `persist()` then overwrote the real data. `docstore.json` is the namespace-
> independent "an index exists here" marker. (Phase 2 Review, bug 3.)

## Step 2 — Load: URL → Markdown → `Document`

### Step 2 (products) — `ingest/pdf_loader.py`, `PdfProductLoader.load(pdf_url)`

`data/products/urls.txt` already contains direct PDF datasheet URLs. The loader runs three sub-steps:

1. **Download** (`download_pdf`): HTTP GET the PDF, write to
   `data/products/raw/<sha256(url)[:16]>.pdf`. Cache hit → return immediately.
2. **Parse** (`parse_pdf_to_markdown`): upload the PDF to LlamaCloud, request
   agentic-tier parsing, join the returned page-level markdown, strip Axis copyright
   and `www.axis.com` noise. Write result to `data/products/parsed/<stem>.md`.
   Cache hit → return the file content without calling LlamaCloud.
3. **Wrap in Document**:

```python
Document(
    text    = "<clean datasheet markdown>",
    id_     = "6a122cd836b758d2",       # sha256(pdf_url)[:16]
    metadata = {
        "doc_type":     "product",
        "source":       "https://www.axis.com/dam/public/.../datasheet-axis-q3558-lve-…pdf",
        "fetched_at":   "2026-05-20T…+00:00",
        "content_hash": "<sha256 of markdown>",
    },
)
```

If LlamaCloud returns no markdown or all pages fail, `parse_pdf_to_markdown` raises
`RuntimeError` — the URL is logged and skipped, the manifest is not updated.

### Step 2 (cases) — `ingest/loaders.py`, `UrlLoader.load(url, doc_type)`

`markitdown` fetches the HTML page and converts it to Markdown locally — no API
key, no LLM. The result is wrapped in a LlamaIndex `Document`:

```python
Document(
    text    = "<the whole page as markdown>",
    id_     = "d8c75a60d1da2021",        # sha256(url)[:16]  — stable, URL-derived
    metadata = {
        "doc_type":     "case",
        "source":       "https://www.axis.com/customer-story/…",
        "title":        "…",
        "fetched_at":   "2026-05-16T12:27:52...+00:00",
        "content_hash": "<sha256 of markdown>",
    },
)
```

The raw Markdown at this point still contains the whole page: a large block of
site-navigation chrome, then the real content starting at the first `# ` (H1).

## Step 3 — Entity extraction: `Document` → `Product` / `CaseStudy`

`ingest/entities.py`, `extract_entity(doc)` → dispatches on `doc_type` to
`extract_product` / `extract_case`.

`gpt-4o-mini` reads the page and returns one structured JSON object. Two
implementation choices matter:

1. **OpenAI JSON mode**, not function-calling
   (`entities.py:65-69`, `response_format={"type": "json_object"}`). Function-
   calling structured output cannot express the open-ended `specs` / `details`
   bag — it returns those empty. A prompt-described schema + JSON mode can.
2. **The page text is pre-trimmed and capped.** `page_content(doc.text)` (in
   `node_builder.py`) drops everything before the first H1 — i.e. the navigation
   chrome — then `[:6000]` caps it. Without the trim, the 6000-char window of a
   case page is *100% site navigation* and the model extracts nothing useful.
   (Phase 2 Review, bug B1.)

The `Field(description=...)` text on the Pydantic models (`models.py:67-116`) is
not just documentation — it is part of the prompt. `models.py` even instructs the
model to always populate `fov_horizontal_deg` and `operating_temp_c` as verbatim
strings, ranges included.

**Worked example — extracted `Product` for Q3558-LVE:**

```python
Product(
    model_name = "AXIS Q3558-LVE",
    category   = ProductCategory.NETWORK_CAMERA,
    subcategory = "dome",
    specs = {
        "resolution":        "8 MP",
        "fov_horizontal_deg": "104.0 - 48.9 °",       # a range string, kept verbatim
        "ip_rating":         "IK10, IP66, IP6K9K, NEMA 4X",
        "operating_temp_c":  "-50 °C to 55 °C",
        "features":          [...],
    },
    source_url  = "https://www.axis.com/products/axis-q3558-lve",
    raw_summary = "The AXIS Q3558-LVE is an advanced 8 MP AI-powered dome camera "
                  "designed for outdoor security. ...",
)
```

The entity is then stored back onto the document **two ways** (`entities.py:85-89`,
SPEC §4.4):

- **Full JSON** in `doc.metadata["entity"]` — the complete entity, bag included.
- **Filter keys only** copied to top-level metadata: `model_name`, `category`,
  `subcategory`, `source_url`. The `specs` bag is *not* expanded to top-level
  metadata — a growing, irregular key set there would pollute future metadata
  filtering.

(For a case page `extract_case` instead copies `title`, `industry`, `customer`,
`region`, `deployment_year`.)

## Step 4 — Node build: one `Document` → many `TextNode`s

### Step 4 (products) — `pipeline.py`, `HierarchicalNodeParser`

```python
parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])
all_nodes = parser.get_nodes_from_documents([doc])
leaf_nodes = get_leaf_nodes(all_nodes)
```

The parser creates a three-level hierarchy in a single pass:

| Level | Token budget | What it contains |
|---|---|---|
| Parent | 2048 | A ~half-page section — chapter-level context |
| Child | 512 | A paragraph or spec block |
| Leaf | 128 | A few sentences — the unit that gets embedded |

All nodes at every level automatically inherit `doc.metadata` (including
`model_name`, `category`, `subcategory` written by Step 3). Heavy fields are then
excluded from embedding and LLM rendering:

```python
for node in all_nodes:
    node.excluded_embed_metadata_keys = ["entity", "content_hash", "fetched_at"]
    node.excluded_llm_metadata_keys   = ["entity", "content_hash", "fetched_at"]
```

`model_name`, `category`, and `source` remain visible to both the embedding model
and the LLM, providing product identity without polluting the vector with large blobs.

**Storage split:**

- `storage_context.docstore.add_documents(all_nodes)` — registers **all** nodes.
- `index.insert_nodes(leaf_nodes)` — embeds **only leaf nodes**.

`AutoMergingRetriever` queries the vector index (leaves), then looks up parent nodes
from the docstore when the merge threshold is met.

### Step 4 (cases) — `ingest/node_builder.py`, `build_case_nodes(doc)`

The page Markdown is split on `## ` (H2) headings by `_split_by_heading()`. Each
section is routed by its heading:

| Heading on the page | → `node_kind` | Notes |
|---|---|---|
| *(intro text before first H2)* | `case_section` (intro) | one node |
| "The challenge", "The solution", etc. | `case_section` | one node per H2 |
| "Products & solutions" | `case_products` | one node |
| "Our partner organizations" | `case_partners` | standalone if ≥ 2 partners |
| "You may also be interested in", "Get in touch", footer/menus | **skipped** | navigational noise |

The `case_card` is built from the extracted entity (Step 3) into a fixed template,
not from page text. Every node carries `doc_id`, `source`, `node_kind`, filter keys,
and a `NodeRelationship.SOURCE` pointing at the parent `doc_id` (enables
`--force` deletion via `index.delete_ref_doc(doc_id)`).

Cascading split: a case node exceeding ~1500 tokens is split with
`SentenceSplitter(chunk_size=1024, chunk_overlap=100)`.

**Result:** 7–9 typed nodes per case study URL.

## Step 5 — Contextual prefix: make each node self-describing (cases only)

`ingest/contextual.py`, `add_contextual_prefixes(nodes)`.

> **Products skip this step.** Product leaf nodes already carry `model_name`,
> `category`, and `source` in their inherited metadata, and the hierarchical
> structure from Step 4 provides the surrounding context. `add_contextual_prefixes`
> is called only for case nodes.

**The problem it solves for cases:** a bare section node — e.g. a paragraph from
"The challenge" section — has nothing in its text to indicate *which* case study or
*what industry* it describes. **Contextual retrieval** fixes that by prepending
context to every node *before* embedding.

For each case node, two things are prepended:

1. **A structured header line** built from metadata (`_build_case_prefix`):

   ```
   [Case: A lesson in creating a cohesive, modern surveillance system |
    Customer: Knoch School District | Industry: education |
    Region: Pennsylvania, United States | Year: 2026 |
    Section: case_section | Kind: case_section]
   ```

2. **A 1–2 sentence LLM summary** of the chunk. `gpt-4o-mini` is told to summarise
   *only* the chunk text and not to add or speculate (`contextual.py:15-19`) — this
   keeps the summary grounded, per CLAUDE.md's anti-hallucination rule.

The node is mutated in place (`contextual.py:59-82`):

```python
node.metadata["original_text"] = original          # prefix-free body, preserved
node.text = f"{prefix}\n\n{original}"               # what gets embedded
node.excluded_embed_metadata_keys = all_keys        # don't ALSO prepend metadata
node.excluded_llm_metadata_keys   = all_keys
```

The `excluded_*` lines matter: the prefix is already inside `node.text`. Without
excluding the metadata keys, LlamaIndex's `MetadataMode.EMBED` would *also* prepend
every metadata value — including `original_text`, the entire body again — and the
embedding would be computed over duplicated, polluted text (Phase 2 Review, bug B2).

**Worked example — a Knoch School District `case_section` node after Step 5:**

```
[Case: A lesson in creating a cohesive, modern surveillance system |
 Customer: Knoch School District | Industry: education |
 Region: Pennsylvania, United States | Year: 2026 |
 Section: case_section | Kind: case_section]
Knoch School District replaced its outdated analog cameras with 150 high-resolution
Axis cameras, integrating them with existing door-control systems.

## The solution
An end-to-end Axis solution was implemented. The district worked with
system integrator ... to install 150 IP cameras ...
```

The header lines are the contextual prefix; everything below is the original section
body, also kept verbatim in `metadata["original_text"]`.

## Step 6 — Embed & persist

`pipeline.py:118`, `index.insert_nodes(nodes)`.

`VectorStoreIndex` sends every node's `node.text` (prefix + body) to
`text-embedding-3-small`. Each node becomes a 1536-dimensional vector. The text and
metadata go to the **docstore**; the vectors go to the **vector store**.

`pipeline.py:123`, `storage_context.persist("storage/products")` writes four files:

| File | Holds |
|---|---|
| `docstore.json` | node text + metadata + relationships |
| `default__vector_store.json` | the embedding vectors |
| `index_store.json` | the index structure |
| `graph_store.json` | unused here (empty) |

**`--force` deletion** happens just before insertion (`pipeline.py:103-111`): for
each URL the manifest record gives the old `doc_id`, and
`index.delete_ref_doc(doc_id, delete_from_docstore=True)` removes every node that
carried that `SOURCE` relationship — preventing duplicates on re-ingest.

**Crash safety** (`pipeline.py:120-123`, Phase 2 Review bug B3): `persist()` runs
*per URL*, and *before* the manifest is updated. If the process dies between the
two writes, the URL is simply absent from the manifest and gets re-ingested next
run — better than a manifest entry pointing at nodes that were never persisted.
The whole per-URL body is wrapped in `try/except`, so one unreachable URL is
logged and skipped, not fatal to the run.

## Step 7 — Manifest update

`manifest.py`, `record()` + `save()`. After a URL's nodes are persisted, an entry
is appended to `storage/products/manifest.json`:

```json
"https://www.axis.com/products/axis-q3558-lve": {
  "url": "https://www.axis.com/products/axis-q3558-lve",
  "content_hash": "9e3edf416a4b89a1bbaaead2d5eb20bb693da4e86e999f089291ec773c64ae6b",
  "doc_id": "d8c75a60d1da2021",
  "node_ids": ["d46ce3fb-274e-...", "8044c3d5-f590-...", ... 16 ids ...],
  "entity_kind": "Product",
  "ingested_at": "2026-05-16T12:27:52.729021+00:00"
}
```

This entry is what makes the *next* run skip this URL in Step 1.

## Incremental behaviour — three runs of the same command

```bash
# Run 1 — fresh
uv run python -m rag_case_products.cli ingest --source products --limit 5
#   manifest empty → 5 URLs fetched, embedded, persisted; 5 manifest entries.

# Run 2 — identical command
uv run python -m rag_case_products.cli ingest --source products --limit 5
#   "[products] skip (already ingested): 5 URL(s)" → 0 fetches, 0 writes.

# Run 3 — force
uv run python -m rag_case_products.cli ingest --source products --limit 5 --force
#   "deleted old nodes for doc_id=..." ×5 → 5 URLs re-fetched, re-embedded.
#   Node count stays stable (delete-then-insert, no duplicates).
```

This is the SPEC §10 incremental-ingest verification, and the Gate 2 / Gate 7
result in `.claude/plan/todo.md`.

---

# Part 2 — Query Pipeline (online)

Entry point: `app.py` (Chainlit). Business logic: `RagWorkflow` in
`src/rag_case_products/workflow/rag_workflow.py`. The workflow is a 3-step
LlamaIndex `Workflow`: **`analyze_query` → `run_agent` → `synthesize`**.

**UI separation invariant:** only `app.py` imports Chainlit. The workflow emits
plain `ProgressEvent`s; the UI listens. Nothing under `src/` knows the UI exists.

## Step 0 — Chainlit receives the message

`app.py`:

- `on_chat_start` (once per chat): registers `cl.LlamaIndexCallbackHandler` on
  `Settings.callback_manager`, **then** constructs `RagWorkflow(timeout=120)` and
  stores it in the session. Order matters — `as_query_engine()` snapshots the
  callback manager when `RagWorkflow.__init__` builds the tools, so retrieval/LLM
  sub-steps surface in the UI.
- `RagWorkflow.__init__` (`rag_workflow.py:110-114`): pins `Settings.embed_model`
  and `Settings.llm`, then calls `build_tools()` **once** — loading both indices
  and the cross-encoder reranker is expensive and must not happen per message.
- `on_message` (per message): `handler = workflow.run(query=message.content)`,
  then iterates `handler.stream_events()`.

The worked example for this part uses three queries, one per pattern:

- **Pattern B** — *"What is the operating temperature of the AXIS Q3558-LVE?"*
- **Pattern A** — *"Show education case studies about school surveillance upgrades."*
- **Pattern C** — *"What operating temperatures are common for cameras used in factories?"*

## Step 1 — `analyze_query`: classify and rewrite

`rag_workflow.py:116-134`. The `StartEvent` carries the raw query.

`Settings.llm.astructured_predict(QueryAnalysis, _ANALYZE_PROMPT, query=...)` asks
`gpt-4o-mini` to return a structured `QueryAnalysis` (`models.py:28-35`). The
prompt (`rag_workflow.py:28-41`) defines the three patterns and asks for hints +
a rewritten query.

**Worked example — Pattern B query analysed:**

```python
QueryAnalysis(
    pattern         = QueryPattern.PRODUCT_SEARCH,      # "B"
    product_hints   = ["AXIS Q3558-LVE"],
    industry_hints  = [],
    spec_hints      = ["operating temperature"],
    rewritten_query = "AXIS Q3558-LVE operating temperature range",
)
```

The step writes two `ProgressEvent`s to the stream (`Analysing query…`, then
`Pattern: B | rewrite: …`), stores `used_pattern` in the workflow `Context`, and
emits a `QueryAnalyzedEvent`.

Why this step exists: the pattern routes tool selection in Step 2, and the
rewritten query is cleaner to embed than raw conversational text. `QueryAnalysis`
also concentrates hallucination control at the front of the pipeline.

> Note (Phase 4 Review, S-1): routing is **not hard-deterministic**. The pattern
> comes from an LLM classification, and the agent in Step 2 still makes its own
> tool-choice decision. Clear-cut queries route as expected; borderline
> product-vs-hybrid queries may go either way.

## Step 2 — `run_agent`: the `FunctionAgent` retrieves and reasons

`rag_workflow.py:136-181`. This is the heart of the pipeline.

### 2a. Build a pattern-aware system prompt

`AGENT_SYSTEM_PROMPT` (`rag_workflow.py:43-58`) is formatted with the pattern, a
pattern-specific instruction, and the hints from Step 1:

```
Pattern B (product question): call search_products only.
Current query pattern: B
Hints — products: AXIS Q3558-LVE | industries: none | specs: operating temperature
```

### 2b. Construct the agent

```python
agent = FunctionAgent(tools=self._tools, llm=Settings.llm, system_prompt=...)
handler = agent.run(user_msg=analysis.rewritten_query)
```

`self._tools` are the two `QueryEngineTool`s from `retrieval/tools.py`:

- **`search_products`** — the products index as a query engine.
- **`search_cases`** — the cases index as a query engine.

Each tool's `description` (`tools.py:7-21`) is the only thing steering the agent
toward the right one — it lists what the tool is for ("resolution, field of view,
IP rating, operating temperature…" for products).

### 2c. The agent's tool-use loop, and what one tool call does

The agent (an LLM loop) reads the system prompt + user message and decides which
tool(s) to call. For the Pattern B query it calls `search_products` once.

A single `search_products("AXIS Q3558-LVE operating temperature range")` call runs
the full **retrieve → merge → rerank → synthesize** chain configured in `tools.py`:

```
1. EMBED      the query string with text-embedding-3-small.
2. RETRIEVE   similarity_top_k = 8  → 8 nearest leaf nodes by cosine similarity
              (config.py SIMILARITY_TOP_K). Leaf nodes carry model_name, category,
              and source in metadata (inherited from the Document in Step 4), so
              product-identity context is present in every vector even though the
              text itself is a short 128-token chunk.
3. MERGE      AutoMergingRetriever checks each retrieved leaf against its parent:
              if enough sibling leaves were retrieved, the parent node (512 or 2048
              tokens) is substituted. This surfaces broader context (e.g. a whole
              spec section) when multiple nearby leaves all matched the query.
4. RERANK     SentenceTransformerRerank("cross-encoder/ms-marco-MiniLM-L-6-v2")
              re-scores the merged candidate set against the query and keeps
              top_n = 4 (config.py RERANK_TOP_N). A cross-encoder reads
              query+node together — sharper than bi-encoder similarity, but too
              slow over the full index, hence "retrieve 8 cheaply, rerank to 4
              precisely".
5. SYNTHESIZE the query engine sends those 4 nodes to gpt-4o-mini and gets a
              short natural-language answer for THIS tool call.
```

The tool returns that synthesised answer text to the agent **and** a
`raw_output` `Response` object whose `source_nodes` are the 4 reranked nodes.

### 2d. Capturing the chunks

The workflow listens to the agent's own event stream (`rag_workflow.py:162-170`):

```python
async for agent_ev in handler.stream_events():
    if isinstance(agent_ev, ToolCallResult):
        raw = getattr(agent_ev.tool_output, "raw_output", None)
        if isinstance(raw, Response) and raw.source_nodes:
            source_nodes.extend(raw.source_nodes)
```

So `source_nodes` accumulates the reranked nodes from *every* tool call the agent
made — 4 for a single-tool Pattern B query, up to 8 for a two-tool Pattern C query.

### 2e. The agent's final answer

`agent_output = await handler` gives the agent's final composed message
(`agent_output.response.content`) — this already reconciles every tool result and
follows the response rules in the system prompt (cite every fact with `Source:
<url>`, use a Markdown table for multi-model comparisons).

`_nodes_to_chunks()` (`rag_workflow.py:73-96`) converts the captured
`NodeWithScore`s into `RetrievedChunk`s, each with a `Citation` (doc type, title,
source URL, 300-char snippet). The step emits an `AgentDoneEvent(raw_answer,
chunks)`.

**Worked example — Pattern B, what `run_agent` produces:**

- `raw_answer`: "The AXIS Q3558-LVE has an operating temperature range of **-50 °C
  to 55 °C**. Source: https://www.axis.com/products/axis-q3558-lve"
- `chunks`: 4 `RetrievedChunk`s, top one being the Q3558-LVE `card` node shown in
  Step 5.

## Step 3 — `synthesize`: assemble the `AnswerBundle`

`rag_workflow.py:183-208`. This step is deliberately thin — the agent already
wrote the prose; `synthesize` only packages it.

1. Recover `used_pattern` from the `Context` (falls back to `HYBRID` if unset).
2. `_deduplicate_citations()` — one citation per unique source URL, keeping first
   occurrence (`rag_workflow.py:99-106`). Four chunks from the same product page
   collapse to one citation.
3. Build the final `AnswerBundle` (`models.py:122-128`):

```python
AnswerBundle(
    answer_markdown   = ev.raw_answer,                 # the agent's prose
    citations         = [Citation(doc_type="product",
                                  title="AXIS Q3558-LVE Dome Camera | Axis…",
                                  source="https://www.axis.com/products/axis-q3558-lve",
                                  snippet="[Product: AXIS Q3558-LVE | …")],
    used_pattern      = QueryPattern.PRODUCT_SEARCH,
    products_compared = None,        # deferred — comparisons rendered inline as a table
    cases_compared    = None,
)
```

The step emits a final `ProgressEvent` and returns `StopEvent(result=bundle)`.

> `products_compared` / `cases_compared` are intentionally left `None` (Phase 4
> Review, S-4): structured comparisons are rendered as a Markdown table *inside*
> `answer_markdown`, per SPEC §7.2 rule 2.

## Step 4 — Chainlit renders the result

Back in `app.py:on_message`:

- While `stream_events()` yields `ProgressEvent`s, the UI opens one `cl.Step` per
  workflow phase ("Analyse query" / "Retrieve & reason" / "Synthesise answer").
  The previous step is closed before the next opens, so the callback handler's
  auto sub-steps (`retrieve`, `llm`, `query`) nest cleanly under the active phase.
- `bundle = await handler` gets the final `AnswerBundle`.
- Citation elements are built **from the structured `bundle.citations` list** —
  never by parsing URLs out of `answer_markdown` (the agent abbreviates inline
  URLs; the structured list is authoritative — Phase 5 Review, S-2).
- `cl.Message(content=bundle.answer_markdown, elements=citation_elements).send()`.

## The three patterns, side by side

The only behavioural difference between patterns is **which tools the agent
calls** — driven by the Step 1 classification feeding the Step 2 system prompt.

### Pattern A — *"Show education case studies about school surveillance upgrades."*

- `analyze_query` → `pattern=A`, `industry_hints=["education"]`.
- System prompt: "Pattern A … call search_cases only."
- `run_agent` → agent calls **`search_cases`** once. Retrieval hits `case_card` /
  `case_section` nodes; the Knoch School District case (`industry: education`,
  the case study used as this part's running example) ranks high.
- `synthesize` → `AnswerBundle` with `used_pattern=A`, citations are
  `customer-story` URLs.

A retrieved `case_card` node for that case (verbatim from `storage/cases/`):

```
[Case: A lesson in creating a cohesive, modern surveillance system |
 Customer: Knoch School District | Industry: education |
 Region: Pennsylvania, United States | Year: 2026 |
 Section: case_card | Kind: case_card]
Knoch School District in Pennsylvania replaced its outdated analog surveillance
system with a modern Axis solution, installing 150 high-resolution cameras ...

Title: A lesson in creating a cohesive, modern surveillance system
Customer: Knoch School District
Industry: education
...
Challenge: The school district had a patchwork system of legacy analog cameras ...
Solution: An end-to-end Axis solution was implemented ...
Outcome: The district replaced 200 analog cameras with 150 Axis cameras ...
```

### Pattern B — *"What is the operating temperature of the AXIS Q3558-LVE?"*

The fully worked example above. Agent calls **`search_products`** only; the answer
is a single spec value with one product citation.

### Pattern C — *"What operating temperatures are common for cameras used in factories?"*

- `analyze_query` → `pattern=C`, `industry_hints=["factory"]`,
  `spec_hints=["operating temperature"]`.
- System prompt: "Pattern C … call BOTH tools and reconcile the results."
- `run_agent` → agent calls **`search_cases`** (which factory deployments exist,
  which models they used — via `case_card.referenced_models`) **and**
  `search_products` (the `operating_temp_c` spec of those models). `source_nodes`
  now accumulates up to 8 chunks across both tools.
- The agent reconciles: it links factory case studies to the cameras they used and
  reports the operating-temperature range of those cameras, formatted as a table.
- `synthesize` → `AnswerBundle` with `used_pattern=C`, citations from **both**
  `customer-story` and `/products/` URLs.

Pattern C is the reason the architecture is an *agent* and not a fixed `if/else`:
the "retrieve from both indices, then reconcile" loop is something the model does
on its own inside `run_agent`.

---

# Appendix A — Anatomy of one node

Every searchable unit in the system is a `TextNode`. The shape differs between
products (hierarchical, no contextual prefix) and cases (semantic, with prefix).

### Product leaf node (after Steps 4 and 6)

```
TextNode(
  id_  = "d46ce3fb-274e-4fde-97f9-b839e5c00201",     # random uuid4
  text = "Sensor: 1/1.2 progressive scan RGB CMOS\n"
         "Resolution: Up to 3840×2160\n"
         "Lens: Varifocal, F1.6",                     # ← raw 128-token chunk, no prefix
  metadata = {
    "doc_type":      "product",
    "source":        "https://www.axis.com/dam/public/.../datasheet-q3558-lve.pdf",
    "model_name":    "AXIS Q3558-LVE",                # inherited from Document (Step 3)
    "category":      "network_camera",
    "subcategory":   "dome",
    "content_hash":  "9e3edf41…",                    # excluded from embed + LLM
    "fetched_at":    "2026-05-20T…+00:00",            # excluded from embed + LLM
    "entity":        "{\"model_name\": …}",           # excluded from embed + LLM
  },
  relationships = {
    SOURCE: "<document id>",
    PARENT: "<child node id>",                        # child is the 512-token parent
  },
  excluded_embed_metadata_keys = ["entity", "content_hash", "fetched_at"],
  excluded_llm_metadata_keys   = ["entity", "content_hash", "fetched_at"],
)
```

Parent and child nodes at higher levels (512 and 2048 tokens) have the same metadata
and exclusion settings. `AutoMergingRetriever` may return a parent node instead of
a leaf when enough sibling leaves matched the query.

### Case node (after Steps 4, 5, and 6)

```
TextNode(
  id_  = "8044c3d5-f590-4b21-b2d6-1e9b3a7f0c22",
  text = "[Case: A lesson in creating … | Customer: Knoch School District | "
         "Industry: education | Region: Pennsylvania… | Year: 2026 | "
         "Section: case_section | Kind: case_section]\n"
         "<1-2 sentence LLM summary>\n\n"
         "<original section body>",                   # ← contextual prefix + body
  metadata = {
    "doc_id":        "d8c75a60d1da2021",
    "source":        "https://www.axis.com/customer-story/knoch-school-acs",
    "node_kind":     "case_section",
    "doc_type":      "case",
    "title":         "A lesson in creating a cohesive, modern surveillance system",
    "industry":      "education",
    "customer":      "Knoch School District",
    "region":        "Pennsylvania, United States",
    "original_text": "<prefix-free section body>",    # preserved for display
  },
  relationships = { SOURCE: "d8c75a60d1da2021" },     # enables --force delete
  excluded_embed_metadata_keys = [<all metadata keys>],
  excluded_llm_metadata_keys   = [<all metadata keys>],
)
```

`node_kind` values (cases): `case_card`, `case_section`, `case_products`, `case_partners`.

# Appendix B — Where each step lives in the code

| Step | Module | Key function / note |
|---|---|---|
| **Ingest** | | |
| CLI parse | `cli.py` | `_build_parser`, `main` |
| Orchestration | `ingest/pipeline.py` | `build_and_persist_indices`, `_ingest_source` |
| 1 — manifest / skip | `ingest/manifest.py` | `Manifest.is_new`, `record`, `save` |
| 2 (products) — PDF load | `ingest/pdf_loader.py` | `PdfProductLoader.load`, `download_pdf`, `parse_pdf_to_markdown` |
| 2 (cases) — HTML load | `ingest/loaders.py` | `UrlLoader.load` |
| 3 — entity extract | `ingest/entities.py` | `extract_product`, `extract_case` |
| 4 (products) — hierarchical chunk | `ingest/pipeline.py` | `HierarchicalNodeParser`, `get_leaf_nodes` |
| 4 (cases) — semantic node build | `ingest/node_builder.py` | `build_case_nodes`, `_split_by_heading` |
| 5 (cases only) — contextual prefix | `ingest/contextual.py` | `add_contextual_prefixes` |
| 6 (products) — embed & persist | `ingest/pipeline.py` | `docstore.add_documents(all_nodes)`, `index.insert_nodes(leaf_nodes)` |
| 6 (cases) — embed & persist | `ingest/pipeline.py` | `index.insert_nodes(nodes)`, `storage_context.persist` |
| **Query** | | |
| UI | `app.py` | `on_chat_start`, `on_message` |
| Workflow | `workflow/rag_workflow.py` | `RagWorkflow` |
| 1 — analyze | `workflow/rag_workflow.py` | `analyze_query` |
| 2 — agent | `workflow/rag_workflow.py` | `run_agent` |
| Tools | `retrieval/tools.py` | `build_tools` |
| Index load (products) | `retrieval/indices.py` | `load_products_index` → `(VectorStoreIndex, StorageContext)` |
| Index load (cases) | `retrieval/indices.py` | `load_cases_index` → `VectorStoreIndex` |
| Products retriever | `retrieval/tools.py` | `AutoMergingRetriever` + `RetrieverQueryEngine` |
| Cases retriever | `retrieval/tools.py` | `VectorStoreIndex.as_query_engine` |
| Rerank | `retrieval/reranker.py` | `build_reranker` |
| 3 — synthesize | `workflow/rag_workflow.py` | `synthesize` |
| Events | `workflow/events.py` | `QueryAnalyzedEvent`, `AgentDoneEvent`, `ProgressEvent` |
| Data models | `models.py` | `Product`, `CaseStudy`, `QueryAnalysis`, `AnswerBundle`, … |
| Config | `config.py` | model names, paths, `SIMILARITY_TOP_K`, `RERANK_TOP_N`, `HIERARCHICAL_CHUNK_SIZES` |

# Appendix C — Models and tunable parameters

| Parameter | Value | Where | Role |
|---|---|---|---|
| Generation LLM | `gpt-4o-mini` | `config.py LLM_MODEL` | entity extraction, summaries, analyze, agent, synthesis |
| Embedding model | `text-embedding-3-small` | `config.py EMBED_MODEL` | node + query vectors (1536-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `config.py RERANK_MODEL` | re-score retrieved nodes (CPU/MPS) |
| `SIMILARITY_TOP_K` | 8 | `config.py` | nodes retrieved per tool call before rerank |
| `RERANK_TOP_N` | 4 | `config.py` | nodes kept after rerank |
| `HIERARCHICAL_CHUNK_SIZES` | `[2048, 512, 128]` | `config.py` | parent/child/leaf token budgets for product nodes |
| `CONTEXTUAL_SPLIT_THRESHOLD` | ~1500 tokens | `config.py` | cascade-split a case node only above this size |
| Entity page window | 6000 chars | `entities.py` | page text shown to the extractor |
| Summary chunk window | 3000 chars | `contextual.py` | case chunk text shown to the summariser |
| Workflow timeout | 120 s | `app.py` | `RagWorkflow(timeout=120)` |

**Environment variables required:**

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | LLM (entity extraction, analysis, synthesis) + embedding model |
| `LLAMA_CLOUD_API_KEY` | `parse_pdf_to_markdown` — LlamaCloud agentic PDF parse (products only) |
