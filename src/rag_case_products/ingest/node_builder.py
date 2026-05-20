"""Node builder: convert a loaded Document into typed TextNodes (SPEC §5.4).

Case nodes → case_card / case_section / case_products / case_partners
             (skip related-stories / Get in touch / footer / breadcrumb)

Product documents are now chunked via HierarchicalNodeParser in pipeline.py;
this module only handles case study Documents.
"""

from __future__ import annotations

import json
import re
import uuid

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import (
    Document,
    NodeRelationship,
    RelatedNodeInfo,
    TextNode,
)

from rag_case_products.config import (
    CONTEXTUAL_SPLIT_THRESHOLD,
    SENTENCE_SPLITTER_CHUNK_OVERLAP,
    SENTENCE_SPLITTER_CHUNK_SIZE,
)

# Rough token estimate: 1 token ≈ 4 chars (good enough for a threshold guard).
_CHARS_PER_TOKEN = 4
_SPLIT_CHAR_THRESHOLD = CONTEXTUAL_SPLIT_THRESHOLD * _CHARS_PER_TOKEN

_splitter = SentenceSplitter(
    chunk_size=SENTENCE_SPLITTER_CHUNK_SIZE,
    chunk_overlap=SENTENCE_SPLITTER_CHUNK_OVERLAP,
)

# Compiled once; reused by _split_by_heading.
_H2_RE = re.compile(r"^(## .+)$", re.MULTILINE)
# H1 heading marks the start of real page content; everything before it is
# site-navigation chrome that markitdown includes but should not be embedded.
_H1_RE = re.compile(r"^# .+$", re.MULTILINE)


def page_content(text: str) -> str:
    """Return text from the first H1 onward, or the full text if there is no H1."""
    m = _H1_RE.search(text)
    return text[m.start() :] if m else text


# ── Headings that signal the end of useful case content ─────────────────────
# Prefix match handles trailing punctuation ("you may also be interested in...")
_CASE_SKIP_H2_PREFIXES = (
    "you may also be interested in",
    "get in touch",
    "footer menu",
    "social menu",
    "legal menu",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _heading_key(heading: str) -> str:
    return heading.lstrip("#").strip().lower()


def _fmt(val: object) -> str:
    """Format a scalar or list value for card templates; returns '—' for missing."""
    if val is None:
        return "—"
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else "—"
    return str(val)


def _split_by_heading(text: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs at each heading matched by pattern.

    Text before the first match is emitted as ("", body).
    """
    parts: list[tuple[str, str]] = []
    positions = [(m.start(), m.group(1)) for m in pattern.finditer(text)]

    if not positions:
        return [("", text.strip())]

    if positions[0][0] > 0:
        parts.append(("", text[: positions[0][0]].strip()))

    for idx, (pos, heading) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        body_start = pos + len(heading)
        parts.append((heading.lstrip("#").strip(), text[body_start:end].strip()))

    return parts


def _base_metadata(doc: Document, node_kind: str) -> dict:
    """Build the metadata dict every node must carry (SPEC §4.4, §5.2 step 4)."""
    meta = {
        "doc_id": doc.id_,
        "source": doc.metadata.get("source", ""),
        "node_kind": node_kind,
    }
    for key in (
        "doc_type",
        "model_name",
        "category",
        "subcategory",
        "source_url",
        "industry",
        "customer",
        "deployment_year",
        "title",
        "region",
    ):
        if key in doc.metadata:
            meta[key] = doc.metadata[key]
    return meta


def _make_node(text: str, metadata: dict) -> TextNode:
    # The SOURCE relationship groups every node under its parent doc_id in the
    # docstore, so `--force` re-ingest can drop a URL's old nodes via delete_ref_doc.
    return TextNode(
        text=text,
        id_=str(uuid.uuid4()),
        metadata=metadata,
        relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=metadata["doc_id"])},
    )


def _maybe_split(text: str, metadata: dict) -> list[TextNode]:
    """Return one node, or multiple if the text exceeds the cascade threshold."""
    if len(text) <= _SPLIT_CHAR_THRESHOLD:
        return [_make_node(text, metadata)]

    tmp_doc = Document(text=text, metadata=metadata)
    child_nodes = _splitter.get_nodes_from_documents([tmp_doc])
    result = []
    for child in child_nodes:
        child.id_ = str(uuid.uuid4())
        # Our keys (node_kind, section_path, doc_id, …) must win over LlamaIndex internals.
        child.metadata = {**child.metadata, **metadata}
        # Re-point SOURCE from the throwaway split doc to the real parent doc_id.
        child.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=metadata["doc_id"])
        result.append(child)
    return result


# ── Case node builder (SPEC §5.4) ────────────────────────────────────────────


def _build_case_card(doc: Document, entity_json: str) -> TextNode:
    """One case_card node: structured entity template for case identification."""
    entity = json.loads(entity_json)
    details = entity.get("details", {})

    lines = [
        f"Title: {entity.get('title', '')}",
        f"Customer: {_fmt(entity.get('customer'))}",
        f"Industry: {_fmt(entity.get('industry'))}",
        f"Region: {_fmt(entity.get('region'))}",
        f"Year: {_fmt(entity.get('deployment_year'))}",
        f"Challenge: {_fmt(details.get('challenges') or details.get('customer_need'))}",
        f"Solution: {_fmt(details.get('solution'))}",
        f"Outcome: {_fmt(details.get('outcomes', details.get('outcome')))}",
        f"Products: {_fmt(entity.get('referenced_models'))}",
        f"Partners: {_fmt(details.get('partners'))}",
        "",
        entity.get("raw_summary", ""),
    ]
    text = "\n".join(lines).strip()
    meta = _base_metadata(doc, "case_card")
    meta["section_path"] = "case_card"
    return _make_node(text, meta)


def build_case_nodes(doc: Document) -> list[TextNode]:
    """Produce typed TextNodes for one case study Document (SPEC §5.4)."""
    entity_json: str = doc.metadata.get("entity", "{}")

    sections = _split_by_heading(page_content(doc.text), _H2_RE)

    nodes: list[TextNode] = []
    nodes.append(_build_case_card(doc, entity_json))

    partners_body: str = ""

    for heading, body in sections:
        key = _heading_key(heading) if heading else ""

        # Prefix match handles trailing punctuation ("you may also be interested in...")
        if any(key.startswith(prefix) for prefix in _CASE_SKIP_H2_PREFIXES):
            continue

        if not heading:
            if body.strip():
                meta = _base_metadata(doc, "case_section")
                meta["section_path"] = "intro"
                nodes.extend(_maybe_split(body, meta))
            continue

        if key == "products & solutions":
            if body.strip():
                meta = _base_metadata(doc, "case_products")
                meta["section_path"] = heading
                nodes.extend(_maybe_split(body, meta))
            continue

        if key == "our partner organizations":
            partners_body = body
            continue

        if body.strip():
            meta = _base_metadata(doc, "case_section")
            meta["section_path"] = heading
            nodes.extend(_maybe_split(body, meta))

    # Standalone partners node only when 2+ partners; single partner folds into case_card.
    if partners_body.strip() and partners_body.count("[Visit website]") >= 2:
        meta = _base_metadata(doc, "case_partners")
        meta["section_path"] = "Our partner organizations"
        nodes.extend(_maybe_split(partners_body, meta))

    return nodes


# ── Dispatcher ───────────────────────────────────────────────────────────────


def build_nodes(doc: Document) -> list[TextNode]:
    """Build typed nodes for a case study Document."""
    return build_case_nodes(doc)
