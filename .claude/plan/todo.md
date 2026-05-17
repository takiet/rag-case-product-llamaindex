# Execution Plan — RAG Prototype

Derived from `SPEC.md`. Build order is bottom-up: scaffolding → config/models →
ingest → retrieval → workflow → UI → tests → end-to-end verification.

Each phase ends with a **verification gate** that must pass before the next phase
starts. Check items off as completed. Do not mark a phase done without proving the
gate (CLAUDE.md "Verification Before Done").

Legend: `[ ]` todo · `[x]` done · `[~]` in progress

## Implementation Discipline

**After implementing each module file** (one `.py` file under `src/`), before
moving to the next module:

1. Run its quick check (import / instantiate) so it is at least valid.
2. **Invoke the `simplify` skill** on the just-changed file and apply its fixes.
3. Only then check the module's `[ ]` item off.

`simplify` is a model-invoked skill (not a shell command), so it cannot run from a
hook — it is run explicitly per module here. Each code phase below also carries a
`simplify` gate item as a backstop in case a module was checked off too early.

---

## Phase 0 — Project Scaffolding

- [x] Add dependencies to `pyproject.toml` (SPEC §9): `llama-index-core>=0.12`,
      `llama-index-llms-openai`, `llama-index-embeddings-openai`,
      `llama-index-agent-openai`, `llama-index-postprocessor-sbert-rerank`,
      `markitdown`, `chainlit`, `pydantic>=2`, `python-dotenv`
- [x] Add dev dependencies: `ruff`, `pytest`
- [x] Configure `[tool.ruff]` and a `src/` package layout in `pyproject.toml`
- [x] Run `uv sync` and confirm the lockfile resolves
- [x] Create `.env.example` with `OPENAI_API_KEY=`
- [x] Append `storage/` and `.env` to `.gitignore`
- [x] Create package skeleton: `src/rag_case_products/__init__.py` and the
      `ingest/`, `retrieval/`, `workflow/` subpackages with `__init__.py`
- [x] Create `data/products/urls.txt` and `data/cases/urls.txt` with the two
      verified URLs as seeds (Q3558-LVE product, ConEd customer story) + comments

**Gate 0**: `uv run python -c "import llama_index, chainlit, markitdown"` succeeds.

---

## Phase 1 — Config & Data Models

- [x] `src/rag_case_products/config.py`: model names (`gpt-4o-mini`,
      `text-embedding-3-small`), storage paths, `similarity_top_k=8`,
      rerank `top_n=4`, contextual-split threshold (~1500 tokens); load `.env`
- [x] `src/rag_case_products/models.py` — Pydantic v2 models (SPEC §4):
  - [x] `DocType`, `QueryPattern`, `ProductCategory` enums
  - [x] `QueryAnalysis`, `Citation`, `RetrievedChunk`
  - [x] `Product`, `CaseStudy` (typed identity + `dict[str, Any]` bag)
  - [x] `AnswerBundle` (with optional `products_compared` / `cases_compared`)
- [x] `simplify` run on `config.py` and `models.py`, fixes applied

**Gate 1**: `uv run python -c "from rag_case_products import models, config"` succeeds;
all models instantiate from sample dicts in a quick scratch check.

---

## Phase 2 — Ingest Pipeline

- [x] `ingest/loaders.py`: `UrlLoader` seam wrapping `markitdown`
      (`MarkItDown().convert(url)` → markdown → `Document`); stable
      `doc.id_ = sha256(url)[:16]`; set `doc.metadata` (`doc_type`, `source`,
      `title`, `fetched_at`)
- [x] `ingest/manifest.py`: read/write `storage/<name>/manifest.json`; `is_new(url)`
      skip decision; record `{url, content_hash, doc_id, node_ids, entity_kind, ingested_at}`
- [x] `ingest/entities.py`: extract `Product` / `CaseStudy` via `gpt-4o-mini`
      structured output; write `doc.metadata["entity"]` + copy filter keys to
      top-level metadata (SPEC §4.4)
- [x] `ingest/node_builder.py`:
  - [x] product nodes — `card` / `prose` (3–5) / `spec` (~10) / `analytics` /
        `procurement`; skip Accessories (SPEC §5.3)
  - [x] case nodes — `case_card` / `case_section` (4–6) / `case_products` /
        `case_partners`; skip related-stories/footer (SPEC §5.4)
  - [x] propagate `doc_id`, `source`, filter keys, `node_kind` to every node
  - [x] cascading `SentenceSplitter(1024/100)` only when a node exceeds ~1500 tokens
- [x] `ingest/contextual.py`: `gpt-4o-mini` 1–2 sentence prefix per node; prepend to
      `node.text`; keep prefix-free body in `metadata["original_text"]`; templates per SPEC §5.5
- [x] `ingest/pipeline.py`: orchestrate steps 1–7 (SPEC §5.2); `--force` deletes old
      nodes (via `index.delete_ref_doc` — see review) ; persist two `VectorStoreIndex`
- [x] `cli.py`: `ingest` command with `--source`, `--limit`, `--force`, `--dry-run` (SPEC §5.7)
- [x] `simplify` run on each ingest module + `cli.py` as completed, fixes applied

**Gate 2** (Option B — verified on a small subset): run with `--limit 5` so only the
first 5 URLs per source are ingested. Confirm: first ingest creates
`storage/{products,cases}/` index files + `manifest.json`; second run (`--limit 5`)
logs "skip (already ingested)" and does not re-fetch any URL; `--force --limit 5`
re-ingests and node count stays stable (no duplicates); `--dry-run` writes nothing.
The full 175-case ingest is deferred to Phase 7.

**Gate 2 result: PASS** (2026-05-16). Staged run (limit 3 → limit 5 → re-run →
`--force` → `--dry-run`): incremental load+append preserves prior nodes
(24/60 → 40/103); re-run skips all; `--force` deletes old nodes and re-ingests
with stable counts (40/103, no duplicates); `--dry-run` leaves the docstore md5
unchanged. See the Phase 2 Review below for three bugs fixed during verification
and one open quality deviation.

### Phase 2 Review

Three bugs were found and fixed during Gate 2 verification:

1. **Entity extraction crashed / returned empty bags.** `entities.py` used
   `llm.complete()` + `json.loads()` (LLM returned markdown-fenced JSON → crash),
   then `structured_predict()` (function-calling cannot fill an open `dict` bag →
   empty `specs`/`details`). Fixed: OpenAI **JSON mode**
   (`response_format={"type": "json_object"}`); `models.py` `Product`/`CaseStudy`
   gained `Field(description=...)` (feeds the LLM schema) + defaults on bag/optional
   fields.
2. **`--force` could not delete old nodes.** `node_builder` built `TextNode`s with
   no `SOURCE` relationship, so `docstore.delete_document(doc_id)` found nothing.
   Fixed: nodes now carry `NodeRelationship.SOURCE → doc_id`; pipeline deletes via
   `index.delete_ref_doc(doc_id, delete_from_docstore=True)`. (SPEC §5.2 step 1
   wording said `docstore.delete_document` — `delete_ref_doc` is the correct API
   for derived nodes.)
3. **Incremental ingest silently wiped prior data.** `pipeline` detected an
   existing index via `vector_store.json`, but `SimpleVectorStore` persists as
   `default__vector_store.json`; the check was always false, so every run rebuilt
   an empty index and `persist()` overwrote storage. Fixed: detect via
   `docstore.json`.

**Node segmentation deviation — RESOLVED** (2026-05-16). markitdown's heading
structure differed from SPEC §5.3/§5.4 assumptions; `node_builder.py` was fixed:
- Product `spec`: the "Technical specifications" H2 body has **no `###` subgroups**
  (markitdown renders bare label lines + tables). Added `_split_spec_body()` to
  split per label+table block. Products: 8 → **16 nodes/URL** (SPEC §5.3 table range).
- Case `case_section`: the pre-first-H2 text was the whole-site nav dump. Now
  `build_case_nodes` discards everything before the first `# ` (H1). Cases:
  19–22 → **8–10 nodes/URL** (SPEC §5.4: 7–9).
Pipeline mechanics (skip/force/dry-run) are unchanged by this fix — Gate 2 stays PASS.

### Phase 2 — Independent Review (rag-reviewer) & fixes — RESOLVED (2026-05-16)

A `rag-reviewer` pass found three more blockers + robustness gaps; all fixed and
re-verified:

- **B1 — case contextual prefix rendered empty.** `_build_case_prefix` read
  metadata keys that were never propagated, and entity extraction read the wrong
  text. Fixed: `_base_metadata` now propagates `title`/`region`; `extract_case`
  copies `title`/`region` and renders a missing year as `""`; the prefix reads
  `section_path`; and `_extract` strips pre-H1 nav via the shared
  `node_builder.page_content()` (the 6000-char window was 100% site nav for case
  pages). Verified: prefix now fully populated, e.g. `[Case: People counting … |
  Customer: Samsung Electronics … | Industry: exhibition | Region: Czech Republic
  | Year: 2022 | Section: intro …]`.
- **B2 — node metadata polluted the embedding.** `MetadataMode.EMBED` prepended
  every metadata key (incl. `original_text`, the full body) to the embedded text.
  Fixed: `contextual.add_contextual_prefix` sets `excluded_embed_metadata_keys` /
  `excluded_llm_metadata_keys` to all keys. Verified: embed content == `node.text`.
- **B3 — manifest saved per-URL but index persisted once at end.** A mid-run crash
  could orphan recorded URLs. Fixed: `persist()` now runs per URL, before the
  manifest write; the per-URL body is wrapped in `try/except` (S2/S4) so one bad
  URL is logged and skipped, not fatal.
- **S1** — `ruff check src/` is clean. **S3** — `manifest.py` tolerates a
  corrupt/empty `manifest.json`.

Gate 2 mechanics re-checked after B3: re-run skips, `--force` deletes + re-ingests
with stable counts, `--dry-run` writes nothing — still PASS.

### Phase 2 — Product-path re-review (rag-reviewer) & scoping decision (2026-05-16)

A focused `rag-reviewer` pass on the product indexing path found the chunking is
**camera-shaped**: SPEC §5.3 is grounded solely in the Q3558-LVE camera page, and
Gate 2 was verified on a camera-only subset. Live runs on radar / speaker /
access-control pages showed `_split_spec_body` produces garbage `spec` nodes
(those pages have no on-page spec tables — just a datasheet link), and
`## Compatible products` / `## Download` H2s leak in as `prose`.

**Decision: scope the product corpus to network cameras for this prototype.**
`data/products/urls.txt` keeps 99 camera URLs active; 24 non-camera URLs (radar /
speaker / access control / intercom) are commented out. SPEC §11 records this as
out of scope. This aligns the corpus with the camera-grounded §5.3 design — the
camera path produces the expected node kinds (16/URL) and B1–B3 of this re-review
were non-camera issues that no longer apply.

**S4 — RESOLVED** (2026-05-16). `_PRODUCT_PROMPT` now states that `specs` values
are free-form strings copied verbatim (ranges/units included), so the LLM no
longer drops range-valued specs. Verified: the Q3558-LVE `card` node renders
`FOV horizontal: 104.0 - 48.9 °` and `Operating temperature: -50 °C to 55 °C`
(both were blank before).

**Known remaining item:** 3 explosion-protected camera URLs use the
`/en-us/products/` page form; their page structure is unverified — Phase 7's
per-URL error isolation will skip any that do not parse.

---

## Phase 3 — Retrieval & Tools

- [x] `retrieval/indices.py`: `load_products_index()` / `load_cases_index()` from
      persisted storage
- [x] `retrieval/reranker.py`: `SentenceTransformerRerank("cross-encoder/ms-marco-MiniLM-L-6-v2", top_n=4)`
- [x] `retrieval/tools.py`: `build_tools()` → `search_products` / `search_cases`
      `QueryEngineTool`s (`similarity_top_k=8` + reranker), with steering descriptions (SPEC §6)
- [x] `simplify` run on each retrieval module as completed, fixes applied

**Gate 3**: a scratch script loads both indices and calls each tool with a sample
query, returning reranked nodes with citation metadata intact.

**Gate 3 result: PASS** (2026-05-16). Products index loaded (16 docs), query engine
built with `similarity_top_k=8` + cross-encoder reranker on `mps`, query returned
4 reranked nodes with `source` citation metadata intact (kinds: card, spec, prose).
Cases index is ABSENT — `storage/cases/` was not built in Gate 2 (products-only run);
cases path is verified clean and will be populated by the full ingest in Phase 7.
One fix during gate: `sentence-transformers` was not installed (`uv add
sentence-transformers`); the installed `SentenceTransformerRerank` Pydantic model
requires `device` as an explicit `str` (despite `Optional[str]` in `__init__`);
fixed by calling `infer_torch_device()` in `build_reranker`. `RERANK_MODEL` moved
to `config.py` alongside the other model-name constants (simplify finding).

**Gate 3 re-verified on both indices** (2026-05-16). 5 case URLs ingested via
`uv run python -m rag_case_products.cli ingest --source cases --limit 5`;
`storage/cases/` created with all 5 index files + `manifest.json` (5 entries,
45 total nodes: 8+10+8+10+9). Both tools exercised in a scratch run:
- `search_products` → 4 reranked nodes (kinds: card, spec, prose); `source` and
  `node_kind` metadata intact on all nodes.
- `search_cases` → 4 reranked nodes (kinds: case_section, case_card); full
  `source` URLs intact (display truncation was a `[-60:]` slice artifact).
All Gate 3 checks pass: node counts ≤4, `source` present, `node_kind` present.
Gate 3 is now fully verified on both products and cases indices.

---

## Phase 4 — Workflow

- [x] `workflow/events.py`: `QueryAnalyzedEvent`, `AgentDoneEvent`, `ProgressEvent`
- [x] `workflow/rag_workflow.py` — `RagWorkflow(Workflow)`:
  - [x] `analyze_query` step → structured `QueryAnalysis` (pattern + hints + rewrite)
  - [x] `run_agent` step → `FunctionAgent` with both tools + `AGENT_SYSTEM_PROMPT`
        (pattern-aware steering, citation preservation, table formatting — SPEC §7.2)
  - [x] `synthesize` step → reconstruct citations, return `AnswerBundle`
  - [x] emit `ProgressEvent` via `ctx.write_event_to_stream()` at each step
- [x] keep the workflow free of any Chainlit import (UI separation invariant)
- [x] `simplify` run on `events.py` and `rag_workflow.py`, fixes applied

**Gate 4**: run the workflow headless on one query per pattern (A/B/C); confirm
Pattern A calls only `search_cases`, B only `search_products`, C both; output is a
valid `AnswerBundle` with citations.

**Gate 4 result: PASS** (2026-05-16). Verified headless via `scratch_gate4.py` on
the small ingested subset (1 product page, 5 cases). Routing confirmed by citation
source kind:
- Pattern A ("people counting in exhibitions/schools") → 3 citations, all
  `customer-story` URLs → only `search_cases` used.
- Pattern B ("Q3558-LVE operating temp / IP rating") → 1 citation, `/products/`
  URL → only `search_products` used; answer rendered as a markdown spec table.
- Pattern C ("outdoor cameras + high-IP environments") → 4 citations, both
  `customer-story` and `/products/` URLs → both tools used.
All three returned a valid `AnswerBundle` (`answer_markdown` + deduplicated
`citations` + `used_pattern`); `analyze`/`run_agent`/`synthesize` `ProgressEvent`s
streamed at each step. `grep` confirms no Chainlit import under `src/`.

Note (rag-reviewer S-1): routing is **not deterministic per pattern** — it depends
on the `analyze_query` LLM classification + the agent's model-driven tool choice
(SPEC §7 leaves tool choice to the model). Clear-cut queries route as above;
borderline product-vs-hybrid queries may classify either way.

### Phase 4 Review

- **S1 (Phase 3 review item) — RESOLVED.** `rag_workflow.py` adds
  `_configure_settings()` (called from `RagWorkflow.__init__`) which pins
  `Settings.embed_model = OpenAIEmbedding(EMBED_MODEL)` and
  `Settings.llm = OpenAI(LLM_MODEL)`, matching the ingest path in `pipeline.py`.
  The retrieval/synthesis path no longer relies on LlamaIndex lazy OpenAI defaults.
- **S2 (Phase 3 review item) — RESOLVED.** `retrieval/indices.py` `_load_index`
  now raises `FileNotFoundError("Index not found at <path>; run the ingest CLI
  first")` when `docstore.json` is absent, instead of a raw deep-stack exception.
- `simplify` on `rag_workflow.py` applied: `FunctionAgent` import hoisted to module
  top-level; `_nodes_to_chunks` calls `get_content()` once per node; dead
  `ctx.set("original_query", ...)` removed.
- Implementation note: the `rag-implementer` agent ended early without a full
  report on two invocations; `rag_workflow.py` was completed on the second run and
  Gate 4 was executed/verified directly in the main session.
- **S-4 (rag-reviewer) — scope decision (2026-05-16):** `products_compared` /
  `cases_compared` stay **deferred** for this prototype. Structured comparisons are
  rendered as a Markdown table inside `answer_markdown` (SPEC §7.2 rule 2);
  `synthesize` leaves both fields `None` by design. Confirmed with the user.
- **S-3 (rag-reviewer):** `_nodes_to_chunks` `doc_type` fallback silently defaults
  to `PRODUCT` — deferred to before the Phase 7 full ingest.

---

## Phase 5 — Chainlit UI

- [x] `app.py`: `on_chat_start` builds `RagWorkflow` into the session
- [x] `on_message`: run workflow, consume `stream_events()`, render each
      `ProgressEvent` as a `cl.Step`, send `AnswerBundle` with citation elements
- [x] confirm `app.py` is the only module importing Chainlit
- [x] `simplify` run on `app.py`, fixes applied

**Gate 5**: `uv run chainlit run app.py -w` serves; the three pattern queries return
answers with visible citations and analyze/agent/synthesize steps in the UI.

**Gate 5 result: PASS** (2026-05-16).
Script-verified:
- `uv run chainlit run app.py --headless` starts and serves — HTTP `200` on
  `http://127.0.0.1:8765/`; log shows "Your app is available".
- `grep` confirms `chainlit` is imported only in `app.py`; nothing under `src/`.
- `ruff check app.py` clean.
Live UI click-through confirmed by the user (2026-05-16): answers show citation
elements and the analyze / agent / synthesize `cl.Step`s render.

### Phase 5 Review

- **S-2 (Phase 4 review item) — RESOLVED.** `app.py` `on_message` builds
  `cl.Text` citation elements from the structured `bundle.citations` list (title +
  full `source` URL); it never parses URLs out of `answer_markdown`. The agent's
  inline-URL abbreviation no longer affects displayed citations.
- **N-4 (Phase 4 review item) — RESOLVED.** `RagWorkflow.__init__` now builds the
  tools once (`self._tools = build_tools()`); `run_agent` reuses `self._tools`
  instead of calling `build_tools()` per query. `app.py` builds the `RagWorkflow`
  once in `on_chat_start` and reuses it across messages — indices + reranker load
  only once per chat session.
- `simplify` on `app.py` applied: dropped a redundant `AnswerBundle` type
  annotation and a what-comment; kept the S-2 invariant comment.
- Minor (not blocking): the server log emits a Pydantic
  `UnsupportedFieldAttributeWarning` about `validate_default=True` on a `Field()`
  in `models.py` — the attribute has no effect there and should be cleaned up
  (candidate for Phase 6 alongside the test work).
- Chainlit auto-created `chainlit.md` (default welcome screen) and `.chainlit/` on
  first run — left in place; `.chainlit/` was already untracked.

### Phase 5 Enhancement — retrieved-chunk & prompt visibility (2026-05-16)

Plan: `~/.claude/plans/cl-step-nested-hollerith.md`. The three workflow
`cl.Step`s only showed a short `detail` string; retrieved chunks and the actual
LLM prompts were not visible. User chose **auto-instrumentation only**.

- `app.py`: registers `cl.LlamaIndexCallbackHandler` on `Settings.callback_manager`
  in `on_chat_start`, **before** `RagWorkflow(...)` (so `as_query_engine()` snapshots
  it). `on_message` step loop restructured to close-previous/open-next, so the
  handler's auto steps (`retrieve` / `query` / `llm`) nest under the active workflow
  phase and the three manual steps become clean siblings.
- `rag_workflow.py`: `analyze_query` and `run_agent` now use `Settings.llm` instead
  of a standalone `OpenAI(model=LLM_MODEL)` (3× construction deduped). Standalone
  LLMs carried an empty callback manager; routing through `Settings.llm` makes the
  analyze + agent LLM calls emit `LLM` callback events too. No Chainlit import added
  to `src/`.
- `.chainlit/config.toml`: unchanged — `[UI] cot = "full"` already set; steps render
  as a collapsible chain-of-thought tree.

Verified (script): `ruff` clean; `grep` shows no Chainlit under `src/`; Chainlit
server boots (HTTP 200). A headless callback-recorder run of `RagWorkflow` confirmed
the events fire through the `Workflow` + `FunctionAgent` + `QueryEngineTool` stack —
`retrieve`×1 (chunks render), `llm`×4 with `MESSAGES` payload (prompts render incl.
the synthesis prompt), `query`×1.
Live UI click-through confirmed by the user (2026-05-16): the nested
`retrieve`/`llm` steps and chunk/prompt content render. Known deviation: the
handler shows full node text in a side panel, not a trimmed snippet
(auto-instrumentation format is fixed). Risk noted: `FUNCTION_CALL` tool-step
events may not fire under the new agent API; `Settings` is process-global
(concurrent sessions could cross-talk).

---

## Phase 6 — Tests

- [x] `tests/test_manifest.py`: skip decision (new vs. already-ingested URL)
- [x] `tests/test_node_builder.py`: product/case node kinds and counts from a
      fixed markdown fixture
- [x] `tests/test_models.py`: Pydantic round-trip incl. `specs` / `details` bags
- [x] `tests/test_tools.py`: tool invocation with a mocked index/query engine
- [x] `simplify` run on the test modules, fixes applied
- [x] `ruff check` and `ruff format` clean

**Gate 6**: `uv run pytest` green; `uv run ruff check` clean.

**Gate 6 result: PASS** (2026-05-16). `uv run pytest` → **42 passed**
(`test_manifest` 8, `test_models` 12, `test_node_builder` 18, `test_tools` 4).
`uv run ruff check` clean; `uv run ruff format --check` clean (24 files).
Tests are hermetic — no network, no OpenAI calls, no `storage/` dependency
(`test_tools.py` mocks the index/query engine and reranker via `monkeypatch`;
`test_node_builder.py` uses inline markdown fixtures).

### Phase 6 Review

- `validate_default` finding: the Pydantic `UnsupportedFieldAttributeWarning` does
  **not** originate in `models.py` (the Phase 5 attribution was wrong — `grep`
  finds no `validate_default` there). It comes from LlamaIndex's own internal
  `Field()` usage (node-parser / agent code). Not our code, so not modified;
  `tests/conftest.py` documents the true source and filters the third-party
  warning so test output stays readable.
- `simplify` on the test modules: removed an unused `import pytest` from
  `test_node_builder.py`; `test_tools.py` reviewed inline (clean — all imports
  used, mock-based, no duplication).
- One genuine test-quality bug fixed during implementation: an earlier
  `test_manifest.py` test took a second `tmp_path` fixture that pytest
  instantiated as a *separate* temp dir, so the assertion passed for the wrong
  reason; fixed to assert on the intended manifest state.
- `ruff format` reformatted 5 pre-existing files (whitespace only — earlier phases
  ran `ruff check` but not `ruff format`); `pytest` green confirms no behavioural
  change.
- Implementation note: the `rag-implementer` agent ended early without a full
  report on both invocations; `test_tools.py`, the line-length fix, `ruff format`,
  and Gate 6 verification were completed directly in the main session.

---

## Phase 7 — End-to-End Verification (SPEC §10)

- [x] `uv sync` clean
- [x] `data/cases/urls.txt` populated — 175 case URLs (2021-01 onwards)
- [x] `data/products/urls.txt` populated — 99 camera URLs active (32 non-camera
      commented out per the Phase 2 scoping decision; the original "2–3 URLs"
      estimate predates that)
- [x] **subset ingest** (scope decision — see Gate 7 note): `ingest --limit 30` →
      30 products + 30 cases ingested; incremental skip fetched only the
      not-yet-ingested URLs
- [x] re-run ingest → skip behavior confirmed
- [x] `--force` → re-ingest + old-node deletion confirmed
- [ ] Chainlit: Pattern A / B / C behave as specified
- [ ] answers show citations (title + source URL)
- [ ] `cl.Step` shows analyze / agent / synthesize progress
- [x] `pytest` green

**Gate 7**: §10 checklist — script-verifiable items PASS; Chainlit A/B/C
click-through pending a human.

**Gate 7 result: PASS (subset scope) — UI click-through pending** (2026-05-16).

Scope decision (confirmed with the user): instead of the full 175-case + 99-product
ingest, Phase 7 was verified on a **bounded subset** (`--limit 30` per source) to
keep runtime and OpenAI cost proportionate for a prototype. The full corpus ingest
remains available via `ingest` without `--limit` (incremental — it would fetch only
the ~214 not-yet-ingested URLs).

Script-verified:
- `uv sync` — clean (190 packages).
- Subset ingest (`ingest --limit 30`, ~54 new URLs fetched): `storage/products` 30
  manifest entries / 471 nodes, `storage/cases` 30 / 273 nodes; 683 nodes total,
  6–17 nodes/URL; no errors, no error-skips (only the 6 already-ingested URLs
  skipped).
- Re-run `ingest --limit 30` → both sources log `skip (already ingested): 30
  URL(s)`, 0 URLs fetched.
- `ingest --force --limit 3` → "deleted old nodes for doc_id=…" logged for all 6
  doc_ids; node counts unchanged (products 471, cases 273 — no duplicates);
  manifest preserved at 30/30 (a sub-limit `--force` does not wipe other entries).
- `uv run pytest` → 42 passed.
- Chainlit server boots (verified in Gate 5).

Needs a human (not scriptable): `uv run chainlit run app.py -w`, run one query per
pattern (A/B/C) against the now-broader corpus, confirm Pattern A/B/C tool routing,
citations (title + source URL), and the analyze/agent/synthesize `cl.Step`s. (The
Phase 5 enhancement's nested `retrieve`/`llm` debug steps were already
click-through-confirmed.)

---

## Notes

- Check in with the user after Gate 2 (ingest is the riskiest external dependency:
  markitdown output shape, OpenAI structured output).
- Ingest scale (Option B): `data/cases/urls.txt` holds 175 case URLs. Phase 2 / Gate 2
  is verified with `--limit 5`; the full ingest (no `--limit`) runs in Phase 7 after
  the pipeline is proven correct.
- If anything goes sideways, stop and re-plan (CLAUDE.md workflow rule).
- Record any user correction in `.claude/plan/lessons.md`.
- Out of scope (SPEC §11): full metadata filtering, evaluation pipeline,
  multi-vendor, BM25 hybrid search, content-change-aware ingest.
