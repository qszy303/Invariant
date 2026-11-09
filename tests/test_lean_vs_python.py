"""Differential test: Lean checker vs Python checker, byte-identical canonical JSON."""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from proof.check import lean_bridge
from proof.check.py_checker import check
from proof.schemas import CheckInput, CheckLine, CheckTxn
from tests.test_py_checker import _build, txn_st

pytestmark = pytest.mark.skipif(
    not lean_bridge.is_available(), reason="Lean binary not built (run `make lean`)"
)

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(p.parent for p in FIXTURES.glob("*/checkinput.json"))


def _assert_same(inp: CheckInput) -> None:
    py = check(inp).to_canonical_json()
    assert lean_bridge.check_json(inp) == py


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_fixture_identical(case):
    inp = CheckInput.model_validate_json((case / "checkinput.json").read_text())
    _assert_same(inp)
    expected = (case / "expected_result.json").read_text()
    assert lean_bridge.check_json(inp) + "\n" == expected


@settings(max_examples=250, deadline=None)
@given(txn_st, st.integers(-10**9, 10**9), st.integers(0, 6), st.integers(0, 2),
       st.integers(-10**6, 10**6))
def test_perturbed_consistent_inputs(txns, start0, line_idx, month, delta):
    inp = _build(txns, start0)
    inp.lines[line_idx].values_cents[month] += delta
    _assert_same(inp)


IDS = st.sampled_from(["a", "b", "rev", "saas", "Zed", "é", "x\"y", "t\\1", "t\n", "日本", "😀", "a,b"])
KINDS = st.sampled_from(["start", "inflow", "outflow", "subtotal", "net", "end"])
BIG = st.integers(-10**20, 10**20)
SMALL = st.integers(-100000, 100000)


@st.composite
def wild_inputs(draw):
    months = draw(st.integers(0, 14))
    vals = st.lists(st.one_of(BIG, SMALL), min_size=months, max_size=months)
    lines = [CheckLine(line_id=draw(IDS), kind=draw(KINDS), values_cents=draw(vals))
             for _ in range(draw(st.integers(0, 8)))]
    txns = [CheckTxn(id=draw(st.text(max_size=4)), month_idx=draw(st.integers(-2, 14)),
                     amount_cents=draw(st.one_of(BIG, SMALL)),
                     assigned_line_ids=draw(st.lists(IDS, max_size=3)))
            for _ in range(draw(st.integers(0, 10)))]
    return CheckInput(months=months, lines=lines, txns=txns)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(wild_inputs())
def test_wild_inputs(inp):
    _assert_same(inp)
