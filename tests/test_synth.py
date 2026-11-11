"""A1 tests: synthetic Slope company and workbook."""

import warnings

import pytest
from openpyxl import load_workbook

from proof.schemas import Ledger
from synth.__main__ import main
from synth.company import (
    LATE_VENDOR,
    LATE_VENDOR_FIRST_MONTH,
    MONTHS,
    OTHER_RAMP_CATEGORIES,
    generate,
)
from synth.workbook import ROW, write_workbook


@pytest.fixture(scope="module")
def company():
    return generate(7)


def test_deterministic():
    a, b = generate(7), generate(7)
    assert a.ledger() == b.ledger()
    assert generate(8).ledger() != a.ledger()


def test_shape(company):
    ledger = Ledger.model_validate(company.ledger().model_dump())
    txns = ledger.transactions
    assert len({t.id for t in txns}) == len(txns)
    assert {t.month_idx for t in txns} == set(range(MONTHS))
    assert all(t.date.startswith("2026-") for t in txns)
    saas = {r.merchant for r in company.ramp if r.category == "Software"}
    assert len(saas) >= 20
    assert sum(1 for r in company.ramp if r.amount_cents > 0) >= 3  # refunds
    first = min(r.date.month - 1 for r in company.ramp
                if r.merchant == LATE_VENDOR)
    assert first == LATE_VENDOR_FIRST_MONTH
    headcount = [len({p.employee for p in company.payroll
                      if p.pay_date.month - 1 == m}) for m in range(MONTHS)]
    assert headcount[0] == 8 and max(headcount) >= 9 and headcount[-1] == 9
    assert {r.category for r in company.ramp} <= (
        {"Software", "Contractors"} | set(OTHER_RAMP_CATEGORIES))
    exp = company.expected_runway()
    assert exp["Starting Cash"][0] == 240_000_000
    burn = [-n / 100 for n in exp["Net Cash Flow"]]
    assert all(130_000 <= b <= 200_000 for b in burn)
    for m in range(1, MONTHS):
        assert exp["Starting Cash"][m] == exp["Ending Cash"][m - 1]


def _recalc(path):
    import formulas

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    out = {}
    for k, v in sol.items():
        if "RUNWAY'!" in k.upper():
            ref = k.split("!")[-1]
            if ":" not in ref:
                out[ref] = v.value[0][0]
    return out


def test_clean_workbook_matches_independent_totals(company, tmp_path):
    path = write_workbook(company, tmp_path / "clean.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames[:1] == ["Runway"]
    assert set(wb.sheetnames) == {"Runway", "Ramp", "Bank", "Payroll"}
    ws = wb["Runway"]
    assert ws.freeze_panes == "B2"
    # real formulas, not pasted values
    assert ws["D5"].value.startswith("=-SUMIFS(")
    assert ws["D2"].value == "=C11"
    assert ws["B2"].value == 2_400_000
    assert ws["D9"].value == "=SUM(D4:D8)"
    assert [ws.cell(r, 1).value for r in range(2, 12)] == list(ROW)

    values = _recalc(path)
    exp = company.expected_runway()
    for label, row in ROW.items():
        for m in range(MONTHS):
            ref = f"{chr(66 + m)}{row}"
            got = values[ref]
            assert not isinstance(got, str), (ref, got)  # no #REF!/#VALUE!
            assert round(float(got) * 100) == exp[label][m], (ref, label, m)


def test_variants_differ_structurally(company, tmp_path):
    clean = write_workbook(company, tmp_path / "c.xlsx")
    dup = load_workbook(write_workbook(company, tmp_path / "d.xlsx", "dup_row"))
    assert dup["Ramp"].max_row == load_workbook(clean)["Ramp"].max_row + 1
    ids = [r[0] for r in dup["Ramp"].iter_rows(min_row=2, values_only=True)]
    assert len(ids) - len(set(ids)) == 1
    mv = load_workbook(write_workbook(company, tmp_path / "m.xlsx",
                                      "missing_vendor"))
    cats = {r[2]: r[4] for r in mv["Ramp"].iter_rows(min_row=2, values_only=True)}
    assert cats[LATE_VENDOR] == "Tools"
    br = load_workbook(write_workbook(company, tmp_path / "b.xlsx",
                                      "broken_rollforward"))
    assert not str(br["Runway"]["C2"].value).startswith("=")
    assert br["Runway"]["D2"].value == "=C11"


def test_cli(tmp_path):
    out = tmp_path / "x" / "slope_clean.xlsx"
    main(["--seed", "7", "--out", str(out), "--variants"])
    names = {p.name for p in out.parent.iterdir()}
    assert names == {"slope_clean.xlsx", "slope.xlsx",
                     "slope_missing_vendor.xlsx",
                     "slope_broken_rollforward.xlsx"}


# ---- profiles: Slope is unchanged, Fernway is the larger demo company ----------

def test_slope_profile_output_is_unchanged():
    """Refactoring the generator into profiles must not move a single Slope row."""
    import hashlib
    import json

    c = generate(7)
    rows = [[*map(str, vars(r).values())] for r in c.ramp + c.bank + c.payroll]
    digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    assert digest == "242ed70adb6ba8ea22def6dc7cc9ff14f5fd811496f1f4f4814d0417d91ed114"
    assert (len(c.ramp), len(c.bank), len(c.payroll)) == (279, 48, 204)


def test_fernway_is_a_bigger_different_company():
    from synth.fernway import FERNWAY

    c = generate(9, FERNWAY)
    slope = generate(7)
    assert c.name == "Fernway" and slope.name == "Slope"
    assert len(c.ramp) >= 1000 and len(c.payroll) >= 500
    assert len({p.employee for p in c.payroll}) == 25
    assert "Cooley LLP" in {r.merchant for r in c.ramp} and "Clickhouse Cloud" in {r.merchant for r in c.ramp}
    assert len({r.merchant for r in c.ramp}) > 2 * len({r.merchant for r in slope.ramp})
    assert len(c.ramp) < 1900  # stays inside the workbook's SUMIFS range (row 2000)
    exp = c.expected_runway()
    assert exp["Starting Cash"][0] == 680_000_000
    assert all(e > 0 for e in exp["Ending Cash"])
    for m in range(1, MONTHS):
        assert exp["Starting Cash"][m] == exp["Ending Cash"][m - 1]


def test_fernway_workbook_matches_its_independent_expected_values(tmp_path):
    from synth.fernway import FERNWAY

    c = generate(9, FERNWAY)
    path = write_workbook(c, tmp_path / "fernway.xlsx")
    wb = load_workbook(path)
    assert wb["Runway"]["A1"].value == "Fernway runway"
    vals = _recalc(path)
    exp = c.expected_runway()
    for label, row in ROW.items():
        for m in range(MONTHS):
            ref = f"{chr(66 + m)}{row}"
            assert round(vals[ref] * 100) == exp[label][m], (label, ref)


def test_demo_workbook_builder_plants_one_dropped_month(tmp_path):
    from scripts.build_demo_workbook import BUG_CELL, build

    path = build(tmp_path)
    ws = load_workbook(path)["Runway"]
    assert path.name == "fernway_dropped_month.xlsx"
    assert 'DATE(YEAR(I$1),MONTH(I$1)+1,1),' in ws[BUG_CELL].value
    assert '">="&J$1' not in ws[BUG_CELL].value
    assert '">="&H$1' in ws["H5"].value  # neighbours keep the correct bound
