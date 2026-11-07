"""A2 tests: formula refs, SUMIFS extraction, ledger, assignment, end to end."""

import json
import warnings
from pathlib import Path

import pytest
from openpyxl import Workbook

from proof.assign import build_checkinput, matches
from proof.check.py_checker import check
from proof.ingest.ledger import read_ledger
from proof.ingest.sheet import (
    CachedValuesMissing, load_cellgraph, parse_refs, recalc_values_with_formulas,
    to_cents,
)
from proof.schemas import CellGraph, CheckInput, Ledger, Rule, RuleSpec
from proof.spec.rules import (
    UnsupportedFormula, build_rulespec, extract_rule, split_args,
)
from synth.company import generate
from synth.workbook import write_workbook

FIX = Path(__file__).parent / "fixtures"


# ---- formula refs -------------------------------------------------------
def test_parse_refs():
    assert parse_refs("=B11") == ["Runway!B11"]
    assert parse_refs("=SUM(D4:D8)") == ["Runway!D4:D8"]
    f = '=-SUMIFS(Ramp!$D$2:$D$2000,Ramp!$E$2:$E$2000,"Software",Ramp!$B$2:$B$2000,">="&C$1)'
    assert parse_refs(f) == ["Ramp!D2:D2000", "Ramp!E2:E2000", "Ramp!B2:B2000", "Runway!C1"]
    assert parse_refs("=SUMIFS(Bank!D:D,Bank!C:C,\"A1*\")") == ["Bank!D:D", "Bank!C:C"]
    assert parse_refs("='My Sheet'!A1+LOG10(B2)+YEAR(C1)") == ["My Sheet!A1", "Runway!B2", "Runway!C1"]
    assert parse_refs('="B2"') == []


def test_to_cents_half_even():
    assert to_cents(1234.56) == 123456
    assert to_cents(0.125) == 12  # half even
    assert to_cents(0.135) == 14
    assert to_cents(-150) == -15000


# ---- SUMIFS extraction --------------------------------------------------
def test_split_args_quotes_and_parens():
    assert split_args('A!B:B,"x,y",F(1,2),">="&C$1') == ['A!B:B', '"x,y"', 'F(1,2)', '">="&C$1']


def test_extract_wildcard_and_dates_ignored():
    f = ('=SUMIFS(Bank!D:D,Bank!C:C,"Stripe*",Bank!B:B,">="&B$1,Bank!B:B,"<"&EDATE(B$1,1))')
    assert extract_rule(f) == Rule(sources=["bank"], counterparty_in=["Stripe*"])


def test_extract_category_negated():
    f = '=-SUMIFS(Ramp!$D$2:$D$2000,Ramp!$E$2:$E$2000,"Software",Ramp!$B$2:$B$2000,">="&C$1)'
    assert extract_rule(f) == Rule(sources=["ramp"], category_in=["Software"])


def test_extract_multi_term_multi_source():
    f = ('=-(SUMIFS(Ramp!D:D,Ramp!E:E,"Travel")+SUMIFS(Ramp!D:D,Ramp!E:E,"Meals")'
         '+SUMIFS(Ramp!D:D,Ramp!E:E,"Travel")+SUMIFS(Bank!D:D,Bank!C:C,"Bank fee*"))')
    assert extract_rule(f) == Rule(
        sources=["ramp", "bank"], counterparty_in=["Bank fee*"],
        category_in=["Travel", "Meals"])


def test_extract_payroll_no_filters_and_no_sumifs():
    f = "=SUMIFS(Payroll!D:D,Payroll!B:B,\">=\"&B$1)+SUMIFS(Payroll!E:E,Payroll!B:B,\">=\"&B$1)"
    assert extract_rule(f) == Rule(sources=["payroll"])
    assert extract_rule("=B2+B10") is None


@pytest.mark.parametrize("f", [
    '=SUMIFS(Ramp!D:D,Ramp!C:C,"Figma",Ramp!E:E,"Software")',  # AND
    '=SUMIFS(Ramp!D:D,Ramp!E:E,"<>Software")',
    '=SUMIFS(Ramp!D:D,Ramp!E:E,$A$1)',
    '=SUMIFS(Other!D:D,Other!E:E,"x")',
])
def test_extract_unsupported(f):
    with pytest.raises(UnsupportedFormula):
        extract_rule(f)


def test_wildcard_matching():
    from proof.schemas import Transaction
    t = Transaction(id="x", source="bank", date="2026-01-01", month_idx=0,
                    amount_cents=-1, counterparty="Rent - Landlord LLC")
    assert matches(Rule(sources=["bank"], counterparty_in=["rent*"]), t)
    assert matches(Rule(sources=["bank"], counterparty_in=["*landlord?llc"]), t)
    assert not matches(Rule(sources=["ramp"], counterparty_in=["rent*"]), t)
    assert not matches(Rule(sources=["bank"], counterparty_in=["Landlord*"]), t)
    assert matches(Rule(sources=["bank"], counterparty_in=["Rent - ~*"]), t) is False


# ---- fixtures reproduce through assign ----------------------------------
@pytest.mark.parametrize("name", ["clean", "bug_dup_row", "bug_broken_rollforward"])
def test_assign_reproduces_fixtures(name):
    d = FIX / name
    load = lambda f, m: m.model_validate_json((d / f).read_text())  # noqa: E731
    got = build_checkinput(load("rulespec.json", RuleSpec),
                           load("ledger.json", Ledger),
                           load("cellgraph.json", CellGraph))
    assert got == load("checkinput.json", CheckInput)


def test_fixture_rulespec_from_cellgraph_formulas_is_rejected_or_ok():
    # fixture cellgraph uses whole-column SUMIFS; baseline must parse them
    g = CellGraph.model_validate_json((FIX / "clean" / "cellgraph.json").read_text())
    spec = build_rulespec(g)
    assert [l.line_id for l in spec.lines] == [
        "start", "revenue", "payroll", "saas", "contractors", "rent", "other",
        "total_outflows", "net", "end"]


# ---- workbook end to end ------------------------------------------------
@pytest.fixture(scope="module")
def company():
    return generate(7)


@pytest.fixture(scope="module")
def run(company, tmp_path_factory):
    d = tmp_path_factory.mktemp("wb")
    cache = {}

    def go(bug):
        if bug not in cache:
            p = write_workbook(company, d / f"{bug}.xlsx", bug)
            graph = load_cellgraph(p, recalc_values_with_formulas(p))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ledger = read_ledger(p)
            ci = build_checkinput(build_rulespec(graph), ledger, graph)
            cache[bug] = (graph, ledger, ci, check(ci))
        return cache[bug]
    return go


def test_missing_cached_values_message(company, tmp_path):
    p = write_workbook(company, tmp_path / "c.xlsx")
    with pytest.raises(CachedValuesMissing, match="open it in Excel"):
        load_cellgraph(p)


def test_clean(run, company):
    graph, ledger, ci, res = run(None)
    assert res.ok, res.violations
    assert len(ledger.transactions) == len(company.ledger().transactions)
    exp = company.expected_runway()
    by = {l.line_id: l.values_cents for l in ci.lines}
    assert by["saas"] == [-v for v in exp["SaaS"]]
    assert by["revenue"] == exp["Revenue"]
    assert by["end"] == exp["Ending Cash"]
    assert all(len(t.assigned_line_ids) == 1 for t in ci.txns)
    refs = {c.ref: c for c in graph.cells}
    assert refs["Runway!C5"].deps[0] == "Ramp!D2:D2000"


def test_dup_row(run):
    _, ledger, ci, res = run("dup_row")
    ids = [t.id for t in ledger.transactions]
    assert len(ids) == len(set(ids))  # sheet-only duplicate not re-counted
    assert {v.invariant for v in res.violations} == {"traceability"}
    (v,) = res.violations
    assert v.line_id == "saas" and v.month_idx == 5
    assert v.actual_cents - v.expected_cents < 0 and len(v.txn_ids) > 0


def test_missing_vendor(run):
    _, _, _, res = run("missing_vendor")
    assert {v.invariant for v in res.violations} == {"coverage"}
    assert len(res.violations) == 5  # Retool Aug-Dec


def test_broken_rollforward(run):
    _, _, _, res = run("broken_rollforward")
    assert {v.invariant for v in res.violations} == {"roll_forward"}
    assert [(v.line_id, v.month_idx) for v in res.violations] == [("start", 1)]


def test_ledger_conflicting_duplicate_id(tmp_path):
    wb = Workbook()
    wb.active.title = "Ramp"
    wb["Ramp"].append(["id", "date", "merchant", "amount", "category", "memo"])
    wb["Ramp"].append(["r1", "2026-01-02", "A", -1, "Software", ""])
    wb["Ramp"].append(["r1", "2026-01-02", "A", -2, "Software", ""])
    wb.create_sheet("Bank").append(["id", "date", "description", "amount"])
    wb.create_sheet("Payroll").append(["id", "pay_date", "employee", "gross", "employer_taxes"])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    with pytest.raises(ValueError, match="different contents"):
        read_ledger(p)
