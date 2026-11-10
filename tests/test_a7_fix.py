"""A7 tests: explanations, safe application, verify loop (offline, scripted proposers)."""

from __future__ import annotations

import hashlib
import types
from pathlib import Path

import pytest
from openpyxl import load_workbook

from proof.fix.explain import analyze, explain
from proof.fix.propose import LLMProposer, TOOL_NAME
from proof.fix.report import Proposal
from proof.fix.verify import ProposalError, apply_proposal, fix_workbook
from proof.spec.agent import AgentError
from synth.company import generate
from synth.workbook import write_workbook

COLS = "BCDEFGHIJKLM"


@pytest.fixture(scope="module")
def books(tmp_path_factory):
    d = tmp_path_factory.mktemp("books")
    c = generate(7)
    return {b: write_workbook(c, d / f"{b}.xlsx", b)
            for b in ("dup_row", "missing_vendor", "broken_rollforward")}


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---- scripted proposers (stand-ins for the LLM) ---------------------------------

def oracle(req):
    v = req["violation"]
    if req["duplicate_export_rows"]:
        r = req["duplicate_export_rows"][0]["rows"][1]
        return {"kind": "delete_duplicate_row", "sheet": r["sheet"], "row": r["row"],
                "reason": "remove the repeat"}
    if v["invariant"] == "coverage":
        saas = next(r for r in req["runway_rows"] if r["row"] == "SaaS")
        f = saas["cells"][0]["formula"][2:]
        return {"kind": "edit_formula", "scope": "row", "cell_ref": saas["cells"][0]["ref"],
                "new_formula": f"=-({f}+{f.replace(chr(34) + 'Software' + chr(34), chr(34) + 'Tools' + chr(34))})",
                "reason": "add the Tools category"}
    m = v["month_idx"]
    return {"kind": "edit_formula", "scope": "cell", "cell_ref": f"Runway!{COLS[m]}2",
            "new_formula": f"={COLS[m - 1]}11", "reason": "link to prior ending cash"}


def wildcard(req):  # clears the coverage gap but double counts everything
    saas = next(r for r in req["runway_rows"] if r["row"] == "SaaS")
    f = saas["cells"][0]["formula"][2:]
    return {"kind": "edit_formula", "scope": "row", "cell_ref": saas["cells"][0]["ref"],
            "new_formula": f"=-({f}+{f.replace(chr(34) + 'Software' + chr(34), chr(34) + '*' + chr(34))})",
            "reason": "catch everything"}


def noop(req):  # a formula that changes nothing useful
    v = req["violation"]
    m = v["month_idx"]
    return {"kind": "edit_formula", "scope": "cell", "cell_ref": f"Runway!{COLS[m]}2",
            "new_formula": f"={COLS[m - 1]}11+{COLS[m - 1]}1", "reason": "x"}


# ---- explanations ----------------------------------------------------------------

def test_explanations_name_the_cause(books):
    an = analyze(books["dup_row"])
    text, ctx = explain(an, an.result.violations[0])
    assert "SaaS in Jun" in text and "appears twice in the Ramp tab" in text
    assert ctx["duplicate_rows"] and ctx["cell_ref"] == "Runway!G5"

    an = analyze(books["broken_rollforward"])
    text, _ = explain(an, an.result.violations[0])
    assert "12,500.00 less" in text and "typed number" in text

    an = analyze(books["missing_vendor"])
    text, ctx = explain(an, an.result.violations[0])
    assert "Retool" in text and "'Tools'" in text and "not counted in any Runway line" in text
    assert "Tools" not in ctx["categories_summed"]


# ---- safety of apply_proposal -----------------------------------------------------

def test_apply_rejects_unsafe_proposals_and_leaves_original(books, tmp_path):
    src = books["dup_row"]
    an = analyze(src)
    before = _sha(src)
    bad = [
        Proposal(cell_ref="Runway!D5", new_formula="=1234.56"),
        Proposal(cell_ref="Runway!D5", new_formula="1234.56"),
        Proposal(cell_ref="Runway!D5", new_formula="=SUM(1,2)"),
        Proposal(cell_ref="Ramp!D5", new_formula="=Runway!B2"),
        Proposal(cell_ref="Runway!A5", new_formula="=B2"),
        Proposal(cell_ref="Runway!D1", new_formula="=B2"),
        Proposal(cell_ref="Runway!D5", new_formula="=[1]Sheet1!A1"),
        Proposal(kind="delete_duplicate_row", sheet="Ramp", row=5),       # not a duplicate
        Proposal(kind="delete_duplicate_row", sheet="Ramp", row=121),     # the first copy
        Proposal(kind="delete_duplicate_row", sheet="Runway", row=2),
    ]
    for p in bad:
        with pytest.raises(ProposalError):
            apply_proposal(src, tmp_path / "x.xlsx", p, an)
    assert _sha(src) == before and not (tmp_path / "x.xlsx").exists()
    edited, _ = apply_proposal(src, tmp_path / "ok.xlsx",
                               Proposal(kind="delete_duplicate_row", sheet="Ramp", row=122), an)
    assert edited == ["Ramp!row 122"]
    assert load_workbook(tmp_path / "ok.xlsx")["Ramp"].max_row == load_workbook(src)["Ramp"].max_row - 1


def test_row_scope_shifts_formula_across_months(books, tmp_path):
    an = analyze(books["broken_rollforward"])
    edited, old = apply_proposal(
        books["broken_rollforward"], tmp_path / "r.xlsx",
        Proposal(scope="row", cell_ref="Runway!C2", new_formula="=B11"), an)
    ws = load_workbook(tmp_path / "r.xlsx")["Runway"]
    assert [ws[f"{c}2"].value for c in "CDM"] == ["=B11", "=C11", "=L11"]
    assert ws["B2"].value == 2_400_000  # the typed January input is left alone
    assert len(edited) == 11 and float(old) < 2_237_421


# ---- the verify loop ---------------------------------------------------------------

@pytest.fixture(scope="module")
def happy(books, tmp_path_factory):
    out = {}
    for b, p in books.items():
        before = _sha(p)
        run = fix_workbook(p, oracle, work_dir=tmp_path_factory.mktemp(b))
        out[b] = (run, before, p)
    return out


@pytest.mark.parametrize("bug", ["dup_row", "missing_vendor", "broken_rollforward"])
def test_verified_fix_for_each_phase1_bug(happy, bug):
    run, before, src = happy[bug]
    assert not run.initial_ok and run.final_ok and run.final_violations == []
    assert all(r.status == "verified" and r.recheck.target_cleared
               and not r.recheck.new_violations for r in run.reports)
    assert _sha(src) == before                      # the original is never written
    assert run.fixed_path and run.fixed_path != str(src)
    assert analyze(run.fixed_path).result.ok        # fresh pipeline run agrees


def test_sibling_violations_cleared_by_one_fix(happy):
    run, _, _ = happy["missing_vendor"]
    assert len(run.reports) == 5
    assert run.reports[0].resolved_by is None and run.reports[0].attempts
    assert all(r.resolved_by == 0 and r.fix == run.reports[0].fix for r in run.reports[1:])


def test_before_after_values(happy):
    run, _, _ = happy["broken_rollforward"]
    r = run.reports[0]
    assert r.cell_ref == "Runway!C2"
    assert r.before_cents == 222492188 and r.after_cents == 223742188
    assert r.fix.old_formula == "2224921.88" and r.fix.new_formula == "=B11"


def test_rejected_proposal_gets_feedback_then_second_try_accepted(books, tmp_path):
    seen = []

    def proposer(req):
        seen.append(req["previous_attempts"])
        return wildcard(req) if len(seen) == 1 else oracle(req)

    run = fix_workbook(books["missing_vendor"], proposer, work_dir=tmp_path)
    assert run.final_ok
    first = run.reports[0].attempts
    assert [a.outcome for a in first] == ["rejected", "accepted"]
    assert "new violation" in first[0].detail
    assert seen[0] == [] and seen[1][0]["outcome"] == "rejected" and seen[1][0]["why"]


def test_three_bad_proposals_means_needs_human(books, tmp_path):
    src = books["broken_rollforward"]
    before = _sha(src)
    calls = []
    run = fix_workbook(src, lambda r: calls.append(1) or noop(r), work_dir=tmp_path)
    assert len(calls) == 3
    r = run.reports[0]
    assert r.status == "needs_human" and r.fix is None
    assert [a.outcome for a in r.attempts] == ["rejected"] * 3
    assert not run.final_ok and run.fixed_path is None and _sha(src) == before


def test_invalid_and_failing_proposers_never_verify(books, tmp_path):
    run = fix_workbook(books["broken_rollforward"],
                       lambda r: {"kind": "edit_formula", "cell_ref": "Runway!C2",
                                  "new_formula": "=4"}, work_dir=tmp_path / "a")
    assert run.reports[0].status == "needs_human"
    assert all(a.outcome == "invalid" for a in run.reports[0].attempts)

    def boom(req):
        raise AgentError("ANTHROPIC_API_KEY is not set")

    run = fix_workbook(books["broken_rollforward"], boom, work_dir=tmp_path / "b")
    assert run.reports[0].status == "needs_human" and len(run.reports[0].attempts) == 1
    assert "ANTHROPIC_API_KEY" in run.reports[0].attempts[0].detail


# ---- the LLM proposer --------------------------------------------------------------

class FakeClient:
    def __init__(self, answer):
        self.answer, self.requests = answer, []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        return types.SimpleNamespace(content=[types.SimpleNamespace(
            type="tool_use", name=TOOL_NAME, id="t1", input=self.answer)])


def test_llm_proposer_request_shape_and_cache(tmp_path):
    ans = {"kind": "edit_formula", "cell_ref": "Runway!C2", "new_formula": "=B11", "reason": "r"}
    client = FakeClient(ans)
    p = LLMProposer(client=client, cache_dir=tmp_path)
    req = {"violation": {"invariant": "roll_forward"}, "previous_attempts": []}
    assert p(req) == ans and p.calls == 1
    kw = client.requests[0]
    assert kw["tools"][0]["name"] == TOOL_NAME and kw["tool_choice"] == {"type": "auto"}
    assert "Never compute" in kw["system"]
    assert p(req) == ans and p.calls == 1            # served from the disk cache
    p(dict(req, previous_attempts=[{"why": "no"}]))  # different input -> new call
    assert p.calls == 2


def test_llm_proposer_without_tool_call_raises(tmp_path):
    client = FakeClient({})
    client.messages.create = lambda **kw: types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="hi")])
    with pytest.raises(AgentError):
        LLMProposer(client=client, cache_dir=tmp_path)({"x": 1})
