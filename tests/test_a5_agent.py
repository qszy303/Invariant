"""A5 tests: LLM RuleSpec agent, offline, with a fake Anthropic client."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from proof.ingest.sheet import load_cellgraph, recalc_values_with_formulas
from proof.schemas import CellGraph, RuleSpec
from proof.spec import agent
from proof.spec.agent import (
    AgentError, build_rulespec_llm, normalize_formula, summarize_graph,
)
from proof.spec.rules import build_rulespec
from synth.company import generate
from synth.workbook import write_workbook

FIX = Path(__file__).parent / "fixtures"


def _answer_from(spec: RuleSpec) -> dict:
    return {"rows": [
        {"row_label": ln.row_label, "kind": ln.kind,
         "rule": ln.rule.model_dump() if ln.rule else None,
         "confidence": 0.9, "rationale": "fake"} for ln in spec.lines]}


class FakeClient:
    """Plays back canned tool inputs; records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(copy.deepcopy({k: v for k, v in kw.items() if k != "messages"})
                          | {"messages": list(kw["messages"])})
        a = self.answers.pop(0)
        if a is None:  # model replies with text and no tool call
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")])
        return SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", id=f"tu{len(self.calls)}", name=agent.TOOL_NAME, input=a)])


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROOF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PROOF_MODEL", raising=False)


@pytest.fixture(scope="module")
def graphs(tmp_path_factory):
    d = tmp_path_factory.mktemp("a5")
    company = generate(7)
    out = {}
    for name, bug in [("clean", None)]:
        p = write_workbook(company, d / f"{name}.xlsx", bug)
        out[name] = load_cellgraph(p, recalc_values_with_formulas(p))
    return out


@pytest.fixture
def clean(graphs):
    return graphs["clean"]


# ---- prompt construction -------------------------------------------------
def test_normalize_groups_shifted_formulas():
    f = '=SUMIFS(Ramp!D:D,Ramp!E:E,"Software",Ramp!B:B,">="&B$1,Ramp!B:B,"<"&EDATE(B$1,1))'
    g = f.replace("B$1", "C$1")
    assert normalize_formula(f, "Runway!B5") == normalize_formula(g, "Runway!C5")
    assert normalize_formula("=B11", "Runway!C2") == normalize_formula("=C11", "Runway!D2")
    # references to other tabs are left alone
    assert "Ramp!B2:B400" in normalize_formula("=SUM(Ramp!B2:B400)", "Runway!B5")


def test_summary_one_variant_per_clean_row(clean):
    rows = summarize_graph(clean)
    assert [r["row_label"] for r in rows][:2] == ["Starting Cash", "Revenue"]
    saas = next(r for r in rows if r["row_label"] == "SaaS")
    assert len(saas["variants"]) == 1
    assert sorted(saas["variants"][0]["months"]) == list(range(12))
    start = next(r for r in rows if r["row_label"] == "Starting Cash")
    assert {v["formula"] for v in start["variants"]} == {"(hardcoded)", "=[col+0]11"} \
        or len(start["variants"]) == 2


def test_summary_surfaces_a_hardcoded_month(clean):
    g = copy.deepcopy(clean)
    victim = next(c for c in g.cells if c.row_label == "SaaS" and c.month_idx == 4)
    victim.formula = None
    saas = next(r for r in summarize_graph(g) if r["row_label"] == "SaaS")
    assert len(saas["variants"]) == 2
    assert saas["variants"][1] == {"formula": "(hardcoded)", "months": [4]}


def test_request_shape(clean):
    base = build_rulespec(clean)
    c = FakeClient(_answer_from(base))
    build_rulespec_llm(clean, client=c, model="m")
    req = c.calls[0]
    assert req["model"] == "m"
    assert req["tool_choice"] == {"type": "tool", "name": agent.TOOL_NAME}
    assert req["tools"][0]["name"] == agent.TOOL_NAME
    assert "SaaS" in req["messages"][0]["content"]
    # the model never sees ledger data or values
    assert "value_cents" not in req["messages"][0]["content"]


# ---- happy path ----------------------------------------------------------
def test_matches_baseline_on_clean(clean):
    base = build_rulespec(clean)
    got = build_rulespec_llm(clean, client=FakeClient(_answer_from(base)))
    assert [ln.line_id for ln in got.lines] == [ln.line_id for ln in base.lines]
    for a, b in zip(got.lines, base.lines):
        assert (a.kind, a.rule) == (b.kind, b.rule)
        assert a.confidence == 0.9  # no disagreement, not capped


def test_line_ids_are_assigned_in_code(clean):
    base = build_rulespec(clean)
    ans = _answer_from(base)
    for r in ans["rows"]:
        r["line_id"] = "evil"  # extra keys from the model are ignored
    got = build_rulespec_llm(clean, client=FakeClient(ans))
    ids = {ln.row_label: ln.line_id for ln in got.lines}
    assert ids["Starting Cash"] == "start" and ids["Ending Cash"] == "end"
    assert ids["Net Cash Flow"] == "net" and ids["SaaS"] == "saas"
    assert "evil" not in ids.values()


# ---- disagreement with the baseline -------------------------------------
def test_disagreeing_rule_is_capped_and_noted(clean):
    base = build_rulespec(clean)
    ans = _answer_from(base)
    saas = next(r for r in ans["rows"] if r["row_label"] == "SaaS")
    saas["rule"] = {"sources": ["ramp"], "counterparty_in": [], "category_in": ["Software", "Ads"]}
    saas["confidence"] = 0.99
    got = build_rulespec_llm(clean, client=FakeClient(ans))
    line = next(ln for ln in got.lines if ln.line_id == "saas")
    assert line.confidence == agent.DISAGREE_CAP
    assert "disagree" in line.rationale
    # the model's rule is kept, only flagged
    assert "Ads" in line.rule.category_in
    other = next(ln for ln in got.lines if ln.line_id == "rent")
    assert other.confidence == 0.9


def test_rule_order_and_case_do_not_count_as_disagreement(clean):
    base = build_rulespec(clean)
    ans = _answer_from(base)
    row = next(r for r in ans["rows"] if r["row_label"] == "Other")
    row["rule"]["category_in"] = [c.upper() for c in reversed(row["rule"]["category_in"])]
    got = build_rulespec_llm(clean, client=FakeClient(ans))
    assert next(ln for ln in got.lines if ln.line_id == "other").confidence == 0.9


def test_baseline_failure_on_one_row_leaves_other_rows_comparable(clean):
    g = copy.deepcopy(clean)
    for c in g.cells:
        if c.row_label == "Rent" and c.formula:
            c.formula = '=SUMIFS(Bank!D:D,Bank!C:C,"A",Bank!D:D,"<>0")'  # AND: unsupported
    # baseline can't parse Rent; the model's reading of Rent is trusted, others checked
    base = build_rulespec(clean)
    ans = _answer_from(base)
    next(r for r in ans["rows"] if r["row_label"] == "SaaS")["kind"] = "inflow"
    got = build_rulespec_llm(g, client=FakeClient(ans))
    assert next(ln for ln in got.lines if ln.line_id == "saas").confidence == agent.DISAGREE_CAP
    assert next(ln for ln in got.lines if ln.line_id == "rent").confidence == 0.9


# ---- the bug workbooks must not leak into the spec ----------------------
def test_missing_vendor_still_fires_coverage(tmp_path):
    from proof.assign import build_checkinput
    from proof.check.py_checker import check
    from proof.ingest.ledger import read_ledger

    p = write_workbook(generate(7), tmp_path / "mv.xlsx", "missing_vendor")
    graph = load_cellgraph(p, recalc_values_with_formulas(p))
    ledger = read_ledger(p)
    base = build_rulespec(graph)
    spec = build_rulespec_llm(graph, client=FakeClient(_answer_from(base)))
    res = check(build_checkinput(spec, ledger, graph))
    assert not res.ok
    assert "coverage" in {v.invariant for v in res.violations}
    # same verdict as the no-LLM baseline path
    assert res == check(build_checkinput(base, ledger, graph))


# ---- validation + retry --------------------------------------------------
def test_unknown_or_missing_rows_rejected(clean):
    base = build_rulespec(clean)
    bad = _answer_from(base)
    bad["rows"].pop()  # drop Ending Cash
    bad["rows"].append({**bad["rows"][0], "row_label": "Mystery"})
    with pytest.raises(AgentError, match="missing=.*Ending Cash.*unknown=.*Mystery"):
        agent.rulespec_from_tool_input(bad, clean)


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.update(confidence=1.5), "outside"),
    (lambda r: r.update(confidence="high"), "number"),
    (lambda r: r.update(rule=None), "needs a rule"),
    (lambda r: r.update(kind="subtotal"), "rule must be null"),
    (lambda r: r["rule"].update(sources=["venmo"]), "sources|Input should be"),
])
def test_field_validation(clean, mutate, msg):
    ans = _answer_from(build_rulespec(clean))
    mutate(next(r for r in ans["rows"] if r["row_label"] == "SaaS"))
    with pytest.raises(AgentError, match=msg):
        agent.rulespec_from_tool_input(ans, clean)


def test_needs_exactly_one_start_and_end(clean):
    ans = _answer_from(build_rulespec(clean))
    next(r for r in ans["rows"] if r["row_label"] == "Ending Cash")["kind"] = "net"
    with pytest.raises(AgentError):  # two nets collide on line id first
        agent.rulespec_from_tool_input(ans, clean)


def test_retry_feeds_error_back_then_succeeds(clean):
    good = _answer_from(build_rulespec(clean))
    bad = copy.deepcopy(good)
    bad["rows"].pop()
    c = FakeClient(bad, good)
    got = build_rulespec_llm(clean, client=c)
    assert len(got.lines) == len(good["rows"])
    assert len(c.calls) == 2
    last = c.calls[1]["messages"][-1]["content"][0]
    assert last["type"] == "tool_result" and last["is_error"]
    assert "Ending Cash" in last["content"]


def test_retry_without_tool_call_uses_plain_nudge(clean):
    good = _answer_from(build_rulespec(clean))
    c = FakeClient(None, good)
    build_rulespec_llm(clean, client=c)
    last = c.calls[1]["messages"][-1]
    assert last["role"] == "user" and isinstance(last["content"], str)


def test_gives_up_after_max_attempts(clean):
    bad = {"rows": []}
    c = FakeClient(bad, bad, bad)
    with pytest.raises(AgentError, match="after 2 attempts"):
        build_rulespec_llm(clean, client=c)
    assert len(c.calls) == agent.MAX_ATTEMPTS


def test_missing_api_key_is_a_clear_error(clean, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AgentError, match="ANTHROPIC_API_KEY"):
        build_rulespec_llm(clean)


def test_tool_schema_is_json_serialisable_and_closed():
    s = agent.tool_schema()
    json.dumps(s)
    item = s["input_schema"]["properties"]["rows"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


# ---- ledger context, cache, review flags, accuracy ----------------------
@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    from proof.ingest.ledger import read_ledger
    p = write_workbook(generate(7), tmp_path_factory.mktemp("l") / "c.xlsx")
    return read_ledger(p)


def test_ledger_context_is_small_and_deterministic(ledger):
    ctx = agent.ledger_context(ledger)
    assert 20 <= len(ctx["sample_rows"]) <= 30
    assert {r["source"] for r in ctx["sample_rows"]} == {"ramp", "bank", "payroll"}
    assert "Software" in ctx["distinct_values"]["ramp"]["categories"]
    assert ctx == agent.ledger_context(ledger)
    assert len(ledger.transactions) > 3 * len(ctx["sample_rows"])  # not the full ledger


def test_user_message_includes_context(clean, ledger):
    msg = agent.build_user_message(
        clean, ledger=ledger, headers={"Ramp": ["id", "date", "merchant"]},
        notes={"Rent": "from the lease"})
    assert "from the lease" in msg and '"C": "merchant"' in msg
    assert "sample_rows" in msg and "distinct_values" in msg


def test_model_comes_from_proof_model(clean, monkeypatch):
    monkeypatch.setenv("PROOF_MODEL", "my-model")
    c = FakeClient(_answer_from(build_rulespec(clean)))
    build_rulespec_llm(clean, client=c)
    assert c.calls[0]["model"] == "my-model"


def test_cache_hit_needs_no_client_or_key(clean, monkeypatch):
    ans = _answer_from(build_rulespec(clean))
    c = FakeClient(ans)
    first = build_rulespec_llm(clean, client=c)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    again = build_rulespec_llm(clean)  # no client, no key
    assert again == first and len(c.calls) == 1


def test_cache_key_changes_with_input(clean, ledger):
    ans = _answer_from(build_rulespec(clean))
    c = FakeClient(ans, ans)
    build_rulespec_llm(clean, client=c)
    build_rulespec_llm(clean, ledger, client=c)
    assert len(c.calls) == 2


def test_invalid_response_is_not_cached(clean, tmp_path):
    c = FakeClient({"rows": []}, {"rows": []})
    with pytest.raises(AgentError):
        build_rulespec_llm(clean, client=c)
    assert not list((tmp_path / "cache").glob("*.json"))


def test_corrupt_cache_entry_is_ignored(clean, tmp_path):
    ans = _answer_from(build_rulespec(clean))
    build_rulespec_llm(clean, client=FakeClient(ans))
    for f in (tmp_path / "cache").glob("*.json"):
        f.write_text("not json")
    c = FakeClient(ans)
    build_rulespec_llm(clean, client=c)
    assert len(c.calls) == 1


def test_needs_review_uses_threshold(clean, monkeypatch):
    spec = build_rulespec(clean).model_copy(deep=True)
    spec.lines[3].confidence = 0.79
    spec.lines[4].confidence = 0.8
    assert agent.needs_review(spec) == [spec.lines[3].line_id]
    monkeypatch.setenv("PROOF_CONFIDENCE_MIN", "0.95")
    assert len(agent.needs_review(spec)) == 2  # 1.0-confidence lines still pass
    assert agent.needs_review(spec, 0.0) == []


def test_spec_accuracy_perfect_and_renamed(clean, ledger):
    gold = build_rulespec(clean)
    acc = agent.spec_accuracy(gold, gold, ledger)
    assert acc["assignment_equal"] and acc["accuracy"] == 1.0
    renamed = RuleSpec(lines=[ln.model_copy(update={"line_id": "x_" + ln.line_id,
                                                    "row_label": "Renamed"})
                              for ln in gold.lines])
    assert agent.spec_accuracy(renamed, gold, ledger)["assignment_equal"]


def test_spec_accuracy_flags_wrong_rule_and_kind(clean, ledger):
    gold = build_rulespec(clean)
    bad = gold.model_copy(deep=True)
    saas = next(ln for ln in bad.lines if ln.line_id == "saas")
    saas.rule.category_in = ["Software", "Travel"]  # steals Travel from Other
    next(ln for ln in bad.lines if ln.line_id == "net").kind = "subtotal"
    acc = agent.spec_accuracy(bad, gold, ledger)
    assert not acc["assignment_equal"]
    assert acc["per_line"]["saas"] is False and acc["per_line"]["other"] is True
    assert acc["per_line"]["net"] is False
    assert acc["accuracy"] == 8 / 10
