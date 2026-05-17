"""Tests for ingest/node_builder.py — node kinds and counts from inline fixtures."""

import json

from llama_index.core.schema import Document

from rag_case_products.ingest.node_builder import build_case_nodes, build_nodes, build_product_nodes

_PRODUCT_ENTITY = {
    "model_name": "AXIS Q3558-LVE",
    "category": "network_camera",
    "subcategory": "fixed",
    "specs": {"ip_rating": "IP66/IP67", "resolution": "4K", "fov_horizontal_deg": "104 deg"},
    "source_url": "https://example.com/q3558",
    "raw_summary": "Fixed 4K camera for wide-area surveillance.",
}

_PRODUCT_MARKDOWN = """\
# AXIS Q3558-LVE

## Overview
Outstanding image quality for wide-area surveillance with ARTPEC-9 chipset.

## Advanced features
Supports AV1 compression and FIPS 140-3 encryption for enhanced security.

## Technical specifications

Camera
| Sensor | Progressive scan |
| Resolution | 4K |

Video
| Codec | H.264, H.265, AV1 |

Lens
| Horizontal FOV | 104 deg |

## Analytics
Includes AXIS Object Analytics and AXIS Video Motion Detection.

## How to buy

### Part numbers
| 03206-001 | EMEA region |
| 03207-001 | Americas |

## Accessories
Mounts and brackets — excluded from embedding.
"""

_CASE_ENTITY = {
    "title": "Surveillance at Museum of Modern Art",
    "industry": "cultural",
    "customer": "MoMA",
    "region": "USA",
    "deployment_year": 2022,
    "referenced_models": ["AXIS P3245-V"],
    "details": {
        "challenges": "Security gaps in public areas",
        "solution": "Axis network cameras installed",
        "outcomes": "Improved visitor safety",
    },
    "source_url": "https://example.com/moma",
    "raw_summary": "MoMA deployed Axis cameras to improve security.",
}

_CASE_MARKDOWN = """\
# Surveillance at Museum of Modern Art

Intro text describing the deployment context and museum background.

## The challenge
The museum faced persistent security challenges across its public galleries.

## The solution
They installed AXIS P3245-V cameras with video analytics.

## The outcome
Security incidents dropped by 40% in the first year.

## Products & solutions
The AXIS P3245-V network camera was the primary device deployed.

## Our partner organizations
[Visit website](https://partnerA.com) Partner A provided integration services.
[Visit website](https://partnerB.com) Partner B handled installation.

## You may also be interested in
Other customer stories.
"""


def _make_product_doc() -> Document:
    return Document(
        text=_PRODUCT_MARKDOWN,
        id_="prod-abc123",
        metadata={
            "doc_type": "product",
            "source": _PRODUCT_ENTITY["source_url"],
            "entity": json.dumps(_PRODUCT_ENTITY),
            "model_name": _PRODUCT_ENTITY["model_name"],
            "category": _PRODUCT_ENTITY["category"],
            "subcategory": _PRODUCT_ENTITY["subcategory"],
        },
    )


def _make_case_doc() -> Document:
    return Document(
        text=_CASE_MARKDOWN,
        id_="case-xyz456",
        metadata={
            "doc_type": "case",
            "source": _CASE_ENTITY["source_url"],
            "entity": json.dumps(_CASE_ENTITY),
            "title": _CASE_ENTITY["title"],
            "industry": _CASE_ENTITY["industry"],
            "customer": _CASE_ENTITY["customer"],
            "region": _CASE_ENTITY["region"],
            "deployment_year": _CASE_ENTITY["deployment_year"],
        },
    )


# ── Product node tests ────────────────────────────────────────────────────────


def test_product_node_kinds():
    nodes = build_product_nodes(_make_product_doc())
    kinds = [n.metadata["node_kind"] for n in nodes]
    assert "card" in kinds
    assert "prose" in kinds
    assert "spec" in kinds
    assert "analytics" in kinds
    assert "procurement" in kinds


def test_product_node_count_range():
    nodes = build_product_nodes(_make_product_doc())
    # SPEC §5.3: 10–14 nodes per product (card + 3-5 prose + ~10 spec + analytics + procurement)
    # Our fixture has 2 prose, 3 spec groups, 1 analytics, 1 procurement → 8 total minimum
    assert len(nodes) >= 6


def test_product_accessories_excluded():
    nodes = build_product_nodes(_make_product_doc())
    texts = [n.text for n in nodes]
    assert not any("Mounts and brackets" in t for t in texts)


def test_product_card_content():
    nodes = build_product_nodes(_make_product_doc())
    card = next(n for n in nodes if n.metadata["node_kind"] == "card")
    assert "AXIS Q3558-LVE" in card.text
    assert "IP66/IP67" in card.text


def test_product_spec_nodes_have_section_path():
    nodes = build_product_nodes(_make_product_doc())
    spec_nodes = [n for n in nodes if n.metadata["node_kind"] == "spec"]
    assert len(spec_nodes) >= 1
    for node in spec_nodes:
        assert node.metadata["section_path"].startswith("Technical specifications")


def test_product_nodes_carry_doc_id():
    doc = _make_product_doc()
    nodes = build_product_nodes(doc)
    for node in nodes:
        assert node.metadata["doc_id"] == doc.id_


def test_product_nodes_carry_source():
    nodes = build_product_nodes(_make_product_doc())
    for node in nodes:
        assert node.metadata["source"] == _PRODUCT_ENTITY["source_url"]


def test_product_nodes_carry_node_kind():
    nodes = build_product_nodes(_make_product_doc())
    for node in nodes:
        assert "node_kind" in node.metadata


# ── Case node tests ───────────────────────────────────────────────────────────


def test_case_node_kinds():
    nodes = build_case_nodes(_make_case_doc())
    kinds = [n.metadata["node_kind"] for n in nodes]
    assert "case_card" in kinds
    assert "case_section" in kinds
    assert "case_products" in kinds
    assert "case_partners" in kinds


def test_case_node_count_range():
    nodes = build_case_nodes(_make_case_doc())
    # SPEC §5.4: 7–9 nodes (case_card + 4-6 case_section + case_products + 0-1 case_partners)
    # Our fixture has intro + 3 H2 sections + products + partners = 7 min
    assert len(nodes) >= 6


def test_case_skip_prefixes_excluded():
    nodes = build_case_nodes(_make_case_doc())
    texts = [n.text for n in nodes]
    assert not any("Other customer stories" in t for t in texts)


def test_case_card_content():
    nodes = build_case_nodes(_make_case_doc())
    card = next(n for n in nodes if n.metadata["node_kind"] == "case_card")
    assert "MoMA" in card.text
    assert "cultural" in card.text


def test_case_partners_standalone_when_two_or_more():
    nodes = build_case_nodes(_make_case_doc())
    kinds = [n.metadata["node_kind"] for n in nodes]
    assert "case_partners" in kinds


def test_case_partners_folded_when_single():
    single_partner_md = _CASE_MARKDOWN.replace(
        "[Visit website](https://partnerA.com) Partner A provided integration services.\n"
        "[Visit website](https://partnerB.com) Partner B handled installation.",
        "[Visit website](https://partnerA.com) Partner A only.",
    )
    doc = Document(
        text=single_partner_md,
        id_="case-single",
        metadata={**_make_case_doc().metadata},
    )
    nodes = build_case_nodes(doc)
    kinds = [n.metadata["node_kind"] for n in nodes]
    assert "case_partners" not in kinds


def test_case_nodes_carry_doc_id():
    doc = _make_case_doc()
    nodes = build_case_nodes(doc)
    for node in nodes:
        assert node.metadata["doc_id"] == doc.id_


def test_case_nodes_carry_source():
    nodes = build_case_nodes(_make_case_doc())
    for node in nodes:
        assert node.metadata["source"] == _CASE_ENTITY["source_url"]


# ── Dispatcher test ────────────────────────────────────────────────────────────


def test_build_nodes_dispatches_product():
    nodes = build_nodes(_make_product_doc())
    kinds = {n.metadata["node_kind"] for n in nodes}
    assert "card" in kinds


def test_build_nodes_dispatches_case():
    nodes = build_nodes(_make_case_doc())
    kinds = {n.metadata["node_kind"] for n in nodes}
    assert "case_card" in kinds
