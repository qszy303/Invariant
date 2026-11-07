from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from proof.check.py_checker import check
from proof.schemas import CheckInput, CheckLine, CheckTxn

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(p.parent for p in FIXTURES.glob("*/checkinput.json"))


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_fixture_matches_expected(case):
    inp = CheckInput.model_validate_json((case / "checkinput.json").read_text())
    expected = (case / "expected_result.json").read_text()
    assert check(inp).to_canonical_json() + "\n" == expected


def test_double_count_and_coverage():
    inp = CheckInput(months=1,
        lines=[CheckLine(line_id="a", kind="inflow", values_cents=[100]),
               CheckLine(line_id="b", kind="inflow", values_cents=[100])],
        txns=[CheckTxn(id="t1", month_idx=0, amount_cents=100, assigned_line_ids=["a", "b"]),
              CheckTxn(id="t2", month_idx=0, amount_cents=5, assigned_line_ids=[])])
    r = check(inp)
    kinds = sorted(v.invariant for v in r.violations)
    assert kinds == ["coverage", "no_double_count"]
    assert not r.ok


MODEL_LINES = ["rev", "pay", "saas"]


def _build(txns, start0):
    """Consistent CheckInput: rev inflow, pay/saas outflow, 3 months."""
    months = 3
    kinds = {"rev": "inflow", "pay": "outflow", "saas": "outflow"}
    vals = {l: [0] * months for l in MODEL_LINES}
    ctxns = []
    for i, (m, amt, line) in enumerate(txns):
        vals[line][m] += amt
        ctxns.append(CheckTxn(id=f"t{i:04d}", month_idx=m, amount_cents=amt, assigned_line_ids=[line]))
    outs = [vals["pay"][m] + vals["saas"][m] for m in range(months)]
    net = [vals["rev"][m] + outs[m] for m in range(months)]
    start, end = [], []
    for m in range(months):
        start.append(start0 if m == 0 else end[m - 1])
        end.append(start[m] + net[m])
    lines = [CheckLine(line_id="start", kind="start", values_cents=start)]
    lines += [CheckLine(line_id=l, kind=kinds[l], values_cents=vals[l]) for l in MODEL_LINES]
    lines += [CheckLine(line_id="total_outflows", kind="subtotal", values_cents=outs),
              CheckLine(line_id="net", kind="net", values_cents=net),
              CheckLine(line_id="end", kind="end", values_cents=end)]
    return CheckInput(months=months, lines=lines, txns=ctxns)


txn_st = st.lists(st.tuples(st.integers(0, 2), st.integers(-10**7, 10**7), st.sampled_from(MODEL_LINES)),
                  max_size=30)


@given(txn_st, st.integers(-10**9, 10**9))
def test_consistent_input_is_ok(txns, start0):
    assert check(_build(txns, start0)).ok


@given(txn_st, st.integers(-10**9, 10**9), st.integers(0, 6), st.integers(0, 2),
       st.integers(-10**6, 10**6).filter(lambda d: d != 0))
def test_perturbing_any_value_fires(txns, start0, line_idx, month, delta):
    inp = _build(txns, start0)
    inp.lines[line_idx].values_cents[month] += delta
    r = check(inp)
    assert not r.ok and r.violations
