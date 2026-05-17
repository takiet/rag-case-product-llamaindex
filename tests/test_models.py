"""Tests for models.py — Pydantic round-trip for all domain models (SPEC §4)."""

import pytest

from rag_case_products.models import (
    AnswerBundle,
    CaseStudy,
    Citation,
    DocType,
    Product,
    ProductCategory,
    QueryAnalysis,
    QueryPattern,
    RetrievedChunk,
)


@pytest.fixture()
def sample_citation() -> Citation:
    return Citation(
        doc_type=DocType.PRODUCT,
        title="AXIS X",
        source="https://example.com/x",
        snippet="A sample snippet.",
    )


def test_product_roundtrip_minimal():
    data = {
        "model_name": "AXIS Q3558-LVE",
        "category": "network_camera",
    }
    p = Product.model_validate(data)
    assert p.model_name == "AXIS Q3558-LVE"
    assert p.category == ProductCategory.NETWORK_CAMERA
    assert p.subcategory is None
    assert p.specs == {}
    assert p.source_url == ""
    assert p.raw_summary == ""


def test_product_roundtrip_full():
    specs = {"ip_rating": "IP66/IP67", "resolution": "4K", "fov_horizontal_deg": "104 deg"}
    p = Product(
        model_name="AXIS Q3558-LVE",
        category=ProductCategory.NETWORK_CAMERA,
        subcategory="fixed",
        specs=specs,
        source_url="https://example.com/q3558",
        raw_summary="A 4K fixed camera.",
    )
    dumped = p.model_dump()
    reloaded = Product.model_validate(dumped)
    assert reloaded.specs == specs
    assert reloaded.subcategory == "fixed"


def test_product_specs_bag_accepts_arbitrary_keys():
    p = Product(
        model_name="AXIS X",
        category="network_camera",
        specs={"custom_key": [1, 2, 3], "nested": {"a": "b"}},
    )
    assert p.specs["custom_key"] == [1, 2, 3]
    assert p.specs["nested"] == {"a": "b"}


def test_case_study_roundtrip_minimal():
    cs = CaseStudy(title="Factory Case", industry="manufacturing")
    assert cs.title == "Factory Case"
    assert cs.industry == "manufacturing"
    assert cs.customer is None
    assert cs.region is None
    assert cs.deployment_year is None
    assert cs.referenced_models == []
    assert cs.details == {}


def test_case_study_roundtrip_full():
    details = {"challenges": "Low visibility", "outcomes": "40% fewer incidents"}
    cs = CaseStudy(
        title="Night Parking Case",
        industry="parking",
        customer="ParkCo",
        region="Sweden",
        deployment_year=2023,
        referenced_models=["AXIS P3245-V", "AXIS Q3515-LV"],
        details=details,
        source_url="https://example.com/case",
        raw_summary="ParkCo deployed cameras.",
    )
    dumped = cs.model_dump()
    reloaded = CaseStudy.model_validate(dumped)
    assert reloaded.deployment_year == 2023
    assert reloaded.referenced_models == ["AXIS P3245-V", "AXIS Q3515-LV"]
    assert reloaded.details == details


def test_case_study_details_bag_accepts_arbitrary_keys():
    cs = CaseStudy(
        title="T",
        industry="retail",
        details={"decision_factors": ["cost", "IP66"], "partners": ["Axis", "Bosch"]},
    )
    assert cs.details["decision_factors"] == ["cost", "IP66"]


def test_query_analysis_roundtrip():
    qa = QueryAnalysis(
        pattern=QueryPattern.HYBRID,
        product_hints=["Q3558-LVE"],
        industry_hints=["factory"],
        spec_hints=["IP66"],
        rewritten_query="Show factory cameras with IP66.",
    )
    assert qa.pattern == QueryPattern.HYBRID
    assert qa.product_hints == ["Q3558-LVE"]
    reloaded = QueryAnalysis.model_validate(qa.model_dump())
    assert reloaded.pattern == QueryPattern.HYBRID


def test_citation_roundtrip():
    cit = Citation(
        doc_type=DocType.PRODUCT,
        title="AXIS Q3558-LVE",
        source="https://example.com/q3558",
        snippet="A 4K fixed camera.",
    )
    assert cit.doc_type == DocType.PRODUCT
    reloaded = Citation.model_validate(cit.model_dump())
    assert reloaded.source == "https://example.com/q3558"


def test_retrieved_chunk_roundtrip():
    cit = Citation(
        doc_type=DocType.CASE,
        title="Factory Case",
        source="https://example.com/case",
        snippet="Cameras improved safety.",
    )
    chunk = RetrievedChunk(text="Cameras improved safety.", score=0.87, citation=cit)
    assert chunk.score == pytest.approx(0.87)
    reloaded = RetrievedChunk.model_validate(chunk.model_dump())
    assert reloaded.citation.doc_type == DocType.CASE


def test_answer_bundle_minimal(sample_citation):
    bundle = AnswerBundle(
        answer_markdown="# Answer\nHere it is.",
        citations=[sample_citation],
        used_pattern=QueryPattern.PRODUCT_SEARCH,
    )
    assert bundle.products_compared is None
    assert bundle.cases_compared is None
    assert bundle.used_pattern == QueryPattern.PRODUCT_SEARCH


def test_answer_bundle_with_compared_lists(sample_citation):
    product = Product(model_name="AXIS X", category="network_camera")
    case = CaseStudy(title="Case A", industry="retail")
    bundle = AnswerBundle(
        answer_markdown="# Comparison",
        citations=[sample_citation],
        used_pattern=QueryPattern.HYBRID,
        products_compared=[product],
        cases_compared=[case],
    )
    assert len(bundle.products_compared) == 1
    assert len(bundle.cases_compared) == 1


def test_enum_values():
    assert DocType.PRODUCT.value == "product"
    assert DocType.CASE.value == "case"
    assert QueryPattern.CASE_SEARCH.value == "A"
    assert QueryPattern.PRODUCT_SEARCH.value == "B"
    assert QueryPattern.HYBRID.value == "C"
    assert ProductCategory.NETWORK_CAMERA.value == "network_camera"
