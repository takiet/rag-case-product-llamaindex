# Processing Walkthrough — Step by Step, with Worked Examples

> Companion to `SPEC.md`. `SPEC.md` is the *design*; this document traces what the
> *implemented code* actually does, one step at a time, following a real piece of
> data through every transformation. Line references point at the current source.

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

`ingest/loaders.py`, `UrlLoader.load(url, doc_type)`.

`markitdown` fetches the HTML page and converts it to Markdown locally — no API
key, no LLM. The result is wrapped in a LlamaIndex `Document`:

```python
Document(
    text    = "<the whole page as markdown>",
    id_     = "d8c75a60d1da2021",        # sha256(url)[:16]  — stable, URL-derived
    metadata = {
        "doc_type":     "product",
        "source":       "https://www.axis.com/products/axis-q3558-lve",
        "title":        "AXIS Q3558-LVE Dome Camera | Axis Communications",
        "fetched_at":   "2026-05-16T12:27:52...+00:00",
        "content_hash": "9e3edf416a4b89a1bbaaead2d5eb20bb693da4e86e999f089291ec773c64ae6b",
    },
)
```

Two design points:

- **`id_` is `sha256(url)[:16]`** — deterministic. The same URL always yields the
  same `doc_id`, which is what lets `--force` find and delete a previous version's
  nodes. (The value `d8c75a60d1da2021` is the real `doc_id` recorded for this URL
  in `storage/products/manifest.json`.)
- **`content_hash` is `sha256(markdown)`** — reserved for a future "URL unchanged
  but content changed" check. Currently it is only stored, not compared.

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

## Step 4 — Node build: one `Document` → many typed `TextNode`s

`ingest/node_builder.py`, `build_nodes(doc)` → dispatches to
`build_product_nodes` / `build_case_nodes`.

A whole product page is too coarse to embed as one vector — a query about
"operating temperature" would be diluted by marketing prose. So the page is split
into many small, **typed** nodes. Each node carries a `node_kind` so retrieval and
debugging can tell *what* a chunk is.

### 4a. The card node — built from the entity, not the page

`_build_product_card()` ignores the page text and renders the extracted entity
into a fixed template:

```
Model: AXIS Q3558-LVE
Category: network_camera
Subcategory: dome
Resolution: 8 MP
FOV horizontal: 104.0 - 48.9 °
IP rating: IK10, IP66, IP6K9K, NEMA 4X
Operating temperature: -50 °C to 55 °C

The AXIS Q3558-LVE is an advanced 8 MP AI-powered dome camera designed for
outdoor security. ...
```

This single, dense node is the "identity card" of the product — the one chunk
most likely to answer "tell me about model X". A case page gets the analogous
`case_card`.

### 4b. The other nodes — built by splitting the page on headings

The page Markdown is split on `## ` (H2) headings by `_split_by_heading()`. Each
section is routed by its heading:

| Heading on the page | → `node_kind` | Notes |
|---|---|---|
| "Outstanding image quality", "ARTPEC-9 …" | `prose` | one node per H2 |
| "Technical specifications" | `spec` | re-split per spec subgroup (see below) |
| "Analytics" | `analytics` | one node |
| "How to buy" → "Part numbers" sub-block | `procurement` | one node |
| "Accessories", "Support and resources", footer/menus | **skipped** | navigational noise — never embedded |

The **`spec` split is special**. markitdown renders "Technical specifications" as
bare label lines ("Camera", "Video", "Lens" …) each followed by a Markdown table —
there are no `###` headings to split on. `_split_spec_body()` (`node_builder.py:167`)
detects a label line as a non-empty line that does *not* start with `|`, `[`, `#`,
or `-`, and starts a new subgroup there. The Q3558-LVE page yields ~10 `spec`
nodes, one per subgroup.

A real `spec` node (subgroup "Camera", from a sibling product page):

```
| Property description | | Property value |
| --- | --- | --- |
| Image sensor | CMOS |
| Image sensor size | 1/1.8" |
| Lightfinder | Lightfinder 2.0 |
| Wide dynamic range | Forensic WDR |
| Min illumination/ light sensitivity (Color) | 0.11 lux |
| Min illumination/ light sensitivity (B/W) | 0 lux |
```

### 4c. Every node gets a metadata envelope

`_base_metadata()` stamps every node with `doc_id`, `source`, `node_kind`, the
filter keys from Step 3, and a `section_path`. `_make_node()` also sets a
`NodeRelationship.SOURCE` pointing at the parent `doc_id` — this is what groups a
URL's nodes so `--force` can delete them all via `index.delete_ref_doc(doc_id)`
(Phase 2 Review, bug 2).

### 4d. Cascading split — only if a node is too big

`_maybe_split()` (`node_builder.py:149`): a node under ~1500 tokens
(`CONTEXTUAL_SPLIT_THRESHOLD`, estimated at 4 chars/token) is kept whole. Only an
oversized node is passed through `SentenceSplitter(chunk_size=1024,
chunk_overlap=100)`. Most heading sections are small, so this rarely fires — it is
a safety valve, not the primary splitter.

**Result for Q3558-LVE:** 16 typed nodes (1 `card` + several `prose` + ~10 `spec`
+ `analytics` + `procurement`). The committed `manifest.json` lists exactly 16
`node_ids` for this URL.

## Step 5 — Contextual prefix: make each node self-describing

`ingest/contextual.py`, `add_contextual_prefixes(nodes)`.

**The problem it solves:** a bare `spec` node is a table of numbers. Embedded
alone, it is hard to retrieve — nothing in `| Image sensor | CMOS |` says *which
product* or *what kind of section* this is. **Contextual retrieval** fixes that by
prepending context to every node *before* embedding.

For each node, two things are prepended:

1. **A structured header line** built from metadata (`_build_product_prefix`):

   ```
   [Product: AXIS Q3558-LVE | Category: network_camera/dome | Section: card | Kind: card]
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

**Worked example — the Q3558-LVE `card` node after Step 5** (verbatim from
`storage/products/docstore.json`):

```
[Product: AXIS Q3558-LVE | Category: network_camera/dome | Section: card | Kind: card]
The AXIS Q3558-LVE is an 8 MP dome network camera with a horizontal field of view
of 104.0 - 48.9°, an IP rating of IK10, IP66, IP6K9K, and an operating temperature
range of -50 °C to 55 °C. It is designed for outdoor security applications.

Model: AXIS Q3558-LVE
Category: network_camera
Subcategory: dome
Resolution: 8 MP
FOV horizontal: 104.0 - 48.9 °
IP rating: IK10, IP66, IP6K9K, NEMA 4X
Operating temperature: -50 °C to 55 °C

The AXIS Q3558-LVE is an advanced 8 MP AI-powered dome camera designed for outdoor
security. ...
```

The first three lines are the contextual prefix; everything below is the original
card body, also kept verbatim in `metadata["original_text"]`.

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
the full **retrieve → rerank → synthesize** chain configured in `tools.py:27-30`:

```
1. EMBED      the query string with text-embedding-3-small.
2. RETRIEVE   similarity_top_k = 8  → 8 nearest nodes by cosine similarity
              (config.py SIMILARITY_TOP_K). The query is matched against the
              CONTEXTUAL-PREFIXED node text — this is where Step 5 pays off:
              the Q3558-LVE `card` and `spec` nodes both name the model and
              the operating-temperature value, so they rank high.
3. RERANK     SentenceTransformerRerank("cross-encoder/ms-marco-MiniLM-L-6-v2")
              re-scores all 8 nodes against the query and keeps top_n = 4
              (config.py RERANK_TOP_N). A cross-encoder reads query+node
              together, so it is sharper than the bi-encoder similarity used
              for retrieval — but too slow to run over the whole index, hence
              "retrieve 8 cheaply, rerank to 4 precisely".
4. SYNTHESIZE the query engine sends those 4 nodes to gpt-4o-mini and gets a
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

Every searchable unit in the system is a `TextNode`. Putting Steps 4–6 together,
one finished node looks like this:

```
TextNode(
  id_  = "d46ce3fb-274e-4fde-97f9-b839e5c00201",     # random uuid4
  text = "[Product: AXIS Q3558-LVE | Category: network_camera/dome | "
         "Section: card | Kind: card]\n"
         "<1-2 sentence LLM summary>\n\n"
         "<original card body>",                      # ← embedded as a vector
  metadata = {
    "doc_id":        "d8c75a60d1da2021",              # parent document
    "source":        "https://www.axis.com/products/axis-q3558-lve",
    "node_kind":     "card",                          # card|prose|spec|analytics|…
    "section_path":  "card",
    "doc_type":      "product",
    "model_name":    "AXIS Q3558-LVE",                # filter keys (Step 3)
    "category":      "network_camera",
    "subcategory":   "dome",
    "title":         "AXIS Q3558-LVE Dome Camera | Axis Communications",
    "original_text": "Model: AXIS Q3558-LVE\nCategory: ...",   # prefix-free body
  },
  relationships = { SOURCE: "d8c75a60d1da2021" },     # enables --force delete
  excluded_embed_metadata_keys = [<all metadata keys>],
  excluded_llm_metadata_keys   = [<all metadata keys>],
)
```

`node_kind` values: `card`, `prose`, `spec`, `analytics`, `procurement`
(products); `case_card`, `case_section`, `case_products`, `case_partners` (cases).

# Appendix B — Where each step lives in the code

| Step | Module | Key function |
|---|---|---|
| **Ingest** | | |
| CLI parse | `cli.py` | `_build_parser`, `main` |
| Orchestration | `ingest/pipeline.py` | `build_and_persist_indices`, `_ingest_source` |
| 1 — manifest / skip | `ingest/manifest.py` | `Manifest.is_new`, `record`, `save` |
| 2 — load | `ingest/loaders.py` | `UrlLoader.load` |
| 3 — entity extract | `ingest/entities.py` | `extract_entity` |
| 4 — node build | `ingest/node_builder.py` | `build_nodes`, `_split_by_heading`, `_split_spec_body` |
| 5 — contextual prefix | `ingest/contextual.py` | `add_contextual_prefixes` |
| 6 — embed & persist | `ingest/pipeline.py` | `index.insert_nodes`, `storage_context.persist` |
| **Query** | | |
| UI | `app.py` | `on_chat_start`, `on_message` |
| Workflow | `workflow/rag_workflow.py` | `RagWorkflow` |
| 1 — analyze | `workflow/rag_workflow.py` | `analyze_query` |
| 2 — agent | `workflow/rag_workflow.py` | `run_agent` |
| Tools | `retrieval/tools.py` | `build_tools` |
| Index load | `retrieval/indices.py` | `load_products_index`, `load_cases_index` |
| Rerank | `retrieval/reranker.py` | `build_reranker` |
| 3 — synthesize | `workflow/rag_workflow.py` | `synthesize` |
| Events | `workflow/events.py` | `QueryAnalyzedEvent`, `AgentDoneEvent`, `ProgressEvent` |
| Data models | `models.py` | `Product`, `CaseStudy`, `QueryAnalysis`, `AnswerBundle`, … |
| Config | `config.py` | model names, paths, `SIMILARITY_TOP_K`, `RERANK_TOP_N` |

# Appendix C — Models and tunable parameters

| Parameter | Value | Where | Role |
|---|---|---|---|
| Generation LLM | `gpt-4o-mini` | `config.py LLM_MODEL` | entity extraction, summaries, analyze, agent, synthesis |
| Embedding model | `text-embedding-3-small` | `config.py EMBED_MODEL` | node + query vectors (1536-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `config.py RERANK_MODEL` | re-score retrieved nodes (CPU/MPS) |
| `SIMILARITY_TOP_K` | 8 | `config.py` | nodes retrieved per tool call before rerank |
| `RERANK_TOP_N` | 4 | `config.py` | nodes kept after rerank |
| `CONTEXTUAL_SPLIT_THRESHOLD` | ~1500 tokens | `config.py` | cascade-split a node only above this size |
| Entity page window | 6000 chars | `entities.py` | page text shown to the extractor |
| Summary chunk window | 3000 chars | `contextual.py` | chunk text shown to the summariser |
| Workflow timeout | 120 s | `app.py` | `RagWorkflow(timeout=120)` |
