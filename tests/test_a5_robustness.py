"""A5 robustness set + acceptance logic, offline (oracle/fake clients, no API)."""

import copy
from types import SimpleNamespace

import pytest

from proof.spec import agent
from proof.spec.robustness import VARIANTS, build_cases, run_acceptance
from proof.spec.rules import UnsupportedFormula, build_rulespec


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROOF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PROOF_CONFIDENCE_MIN", raising=False)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("rob")
    gold, gold_ledger, cases = build_cases(d)
    return d, gold, gold_ledger, cases


def oracle_answer(gold, case, conf=0.95, mutate=None):
    """What a perfect model would say about this variant's rows."""
    rows = []
    labels = list(dict.fromkeys(c.row_label for c in case.graph.cells))
    for label, ln in zip(labels, gold.lines):
        rule = ln.rule.model_dump() if ln.rule else None
        if case.name == "merged_categories" and ln.line_id == "other":
            rule["category_in"] = ["Opex"]
        rows.append({"row_label": label, "kind": ln.kind, "rule": rule,
                     "confidence": conf, "rationale": "oracle"})
    if mutate:
        mutate(case.name, rows)
    return {"rows": rows}


class Script:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", id="t", name=agent.TOOL_NAME, input=self.answers.pop(0))])


def test_five_cases_same_shape(world):
    _, _, _, cases = world
    assert [c.name for c in cases] == ["clean", *VARIANTS]
    assert all(len(c.graph.cells) == 120 for c in cases)  # 10 rows x 12 months


def test_variants_defeat_the_baseline_where_expected(world):
    _, _, _, cases = world
    by = {c.name: c for c in cases}
    build_rulespec(by["clean"].graph)
    for name in ("renamed_rows", "hardcoded_row"):
        with pytest.raises(UnsupportedFormula):
            build_rulespec(by[name].graph)
    # reordered columns: baseline parses but reads the wrong column roles
    from proof.spec.agent import spec_accuracy
    wrong = build_rulespec(by["reordered_columns"].graph)
    gold = build_rulespec(by["clean"].graph)
    assert not spec_accuracy(wrong, gold, by["clean"].ledger)["assignment_equal"]


def test_variant_specific_content(world):
    _, _, _, cases = world
    by = {c.name: c for c in cases}
    labels = {c.row_label for c in by["renamed_rows"].graph.cells}
    assert {"Software & Tools", "Misc Opex"} <= labels and "SaaS" not in labels
    rent = [c for c in by["hardcoded_row"].graph.cells if c.row_label == "Rent"]
    assert len(rent) == 12 and all(c.formula is None for c in rent)
    assert any(t.category_hint == "Opex" for t in by["merged_categories"].ledger.transactions)
    assert agent.read_row_notes(by["hardcoded_row"].path)["Rent"].startswith("Typed in")
    assert agent.export_headers(by["reordered_columns"].path)["Ramp"][2] == "category"


def test_oracle_passes_acceptance(world):
    d, gold, gl, cases = world
    built = (gold, gl, cases)
    client = Script(oracle_answer(gold, c) for c in cases)
    res = run_acceptance(client, d, built=built)
    s = res["_summary"]
    assert s["passed"] and s["variants_failing"] == [] and s["clean_matches_baseline"]
    assert len(client.calls) == 5
    # the model was handed the right context for the reordered variant
    msg = client.calls[4]["messages"][0]["content"]
    assert '"C": "category"' in msg and "sample_rows" in msg


def test_one_confident_miss_fails_acceptance(world):
    d, gold, gl, cases = world
    built = (gold, gl, cases)

    def steal(name, rows):  # rent variant: confidently wrong, high confidence
        if name == "hardcoded_row":
            next(r for r in rows if r["row_label"] == "Rent")["rule"] = {
                "sources": ["bank"], "counterparty_in": [], "category_in": []}

    res = run_acceptance(Script(oracle_answer(gold, c, mutate=steal) for c in cases), d, built=built)
    s = res["_summary"]
    assert s["variants_failing"] == ["hardcoded_row"]
    assert not res["hardcoded_row"]["failures_flagged_low_confidence"]
    assert not s["passed"]  # wrong AND confident is not allowed, even at 3/4


def test_low_confidence_miss_is_accepted_if_3_of_4(world):
    d, gold, gl, cases = world
    built = (gold, gl, cases)

    def hedge(name, rows):
        if name == "hardcoded_row":
            r = next(r for r in rows if r["row_label"] == "Rent")
            r["rule"] = {"sources": ["bank"], "counterparty_in": [], "category_in": []}
            r["confidence"] = 0.4

    res = run_acceptance(Script(oracle_answer(gold, c, mutate=hedge) for c in cases), d, built=built)
    assert res["hardcoded_row"]["failures_flagged_low_confidence"]
    assert res["_summary"]["variants_matching"] == [
        "renamed_rows", "merged_categories", "reordered_columns"]
    assert res["_summary"]["passed"]


def test_two_misses_fail_even_if_flagged(world):
    d, gold, gl, cases = world
    built = (gold, gl, cases)

    def hedge(name, rows):
        if name in ("hardcoded_row", "renamed_rows"):
            r = next(r for r in rows if r["row_label"] in ("Rent", "Office Rent"))
            r["rule"] = {"sources": ["bank"], "counterparty_in": [], "category_in": []}
            r["confidence"] = 0.3

    res = run_acceptance(Script(oracle_answer(gold, c, mutate=hedge) for c in cases), d, built=built)
    assert res["_summary"]["all_failures_flagged"] and not res["_summary"]["passed"]
