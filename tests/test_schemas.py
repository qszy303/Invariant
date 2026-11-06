import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from proof.schemas import (
    CellGraph,
    CheckInput,
    CheckResult,
    Ledger,
    RuleSpec,
    SpecLine,
    Violation,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_DIRS = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
MODELS = {
    "ledger.json": Ledger,
    "cellgraph.json": CellGraph,
    "rulespec.json": RuleSpec,
    "checkinput.json": CheckInput,
    "expected_result.json": CheckResult,
}


def test_fixture_dirs_present():
    assert {p.name for p in FIXTURE_DIRS} >= {
        "clean",
        "bug_dup_row",
        "bug_broken_rollforward",
    }


@pytest.mark.parametrize("fixture", FIXTURE_DIRS, ids=lambda p: p.name)
@pytest.mark.parametrize("filename", sorted(MODELS))
def test_fixture_round_trips(fixture, filename):
    path = fixture / filename
    if not path.exists():
        pytest.skip(f"{filename} not present in {fixture.name}")
    raw = json.loads(path.read_text())
    model = MODELS[filename].model_validate(raw)
    assert model.model_dump(mode="json") == raw


@pytest.mark.parametrize("fixture", FIXTURE_DIRS, ids=lambda p: p.name)
def test_expected_result_is_canonical(fixture):
    text = (fixture / "expected_result.json").read_text().strip()
    result = CheckResult.model_validate_json(text)
    assert result.to_canonical_json() == text


def test_canonical_json_sorts_violations():
    def v(inv, line, month):
        return Violation(invariant=inv, line_id=line, month_idx=month, message="m")

    shuffled = CheckResult(
        ok=False,
        violations=[
            v("traceability", "saas", 2),
            v("roll_forward", None, None),
            v("traceability", "saas", None),
            v("coverage", "rent", 0),
            v("traceability", "payroll", 5),
        ],
    )
    out = json.loads(shuffled.to_canonical_json())["violations"]
    assert [(x["invariant"], x["line_id"], x["month_idx"]) for x in out] == [
        ("coverage", "rent", 0),
        ("roll_forward", None, None),
        ("traceability", "payroll", 5),
        ("traceability", "saas", None),
        ("traceability", "saas", 2),
    ]


def test_canonical_json_has_no_whitespace_and_sorted_keys():
    text = CheckResult(ok=True, violations=[]).to_canonical_json()
    assert text == '{"ok":true,"violations":[]}'


def test_cents_must_be_int():
    with pytest.raises(ValidationError):
        CheckInput.model_validate(
            {
                "months": 1,
                "lines": [{"line_id": "x", "kind": "inflow", "values_cents": [1.5]}],
                "txns": [],
            }
        )


def test_values_length_must_match_months():
    with pytest.raises(ValidationError):
        CheckInput.model_validate(
            {
                "months": 3,
                "lines": [{"line_id": "x", "kind": "inflow", "values_cents": [1, 2]}],
                "txns": [],
            }
        )


def test_rule_must_be_null_for_derived_kinds():
    with pytest.raises(ValidationError):
        SpecLine(
            line_id="net",
            row_label="Net",
            kind="net",
            rule={"sources": ["ramp"]},
            confidence=1.0,
        )
