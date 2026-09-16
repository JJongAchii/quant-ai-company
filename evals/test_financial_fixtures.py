"""Check development fixtures, not a model's financial competence."""

import ast
import json
import operator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

CASES = json.loads(Path(__file__).with_name("financial-cases.json").read_text())["cases"]


def reference_arithmetic(node):
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -reference_arithmetic(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in operations:
        return operations[type(node.op)](reference_arithmetic(node.left), reference_arithmetic(node.right))
    raise ValueError("Fixture expression must use finite elementary arithmetic")


def test_arithmetic_case_answers_have_explicit_units_and_exact_reference():
    for case in CASES:
        assert case["synthetic"] is True
        if case["kind"] == "arithmetic":
            expected = Decimal(case["expected"])
            assert expected.is_finite() and case["unit"]
            assert reference_arithmetic(ast.parse(case["expression"], mode="eval").body) == expected


def test_point_in_time_fixture_obeys_availability_not_only_publication_date():
    case = next(case for case in CASES if case["id"] == "availability_time")
    as_of = datetime.fromisoformat(case["as_of"])
    assert as_of.tzinfo is not None
    eligible = []
    for source in case["sources"]:
        available = datetime.fromisoformat(source["available_at"])
        published = datetime.fromisoformat(source["published_at"])
        assert available.tzinfo is not None and published.tzinfo is not None
        assert published <= available
        if available <= as_of:
            eligible.append(source["id"])
    assert eligible == case["expected_source_ids"]


def test_resource_catalog_does_not_certify_uningested_knowledge():
    sources = json.loads(Path(__file__).with_name("source-catalog.json").read_text())["sources"]
    assert len({source["id"] for source in sources}) == len(sources)
    assert all(source["url"].startswith("https://") for source in sources)
    assert all(not source["ingested"] and not source["claim_verified"] for source in sources)
    assert all(source["knowledge_status"] == "entry_point_only" for source in sources)
