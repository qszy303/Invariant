"""A6 tests: mutators, suite, scoring and report (all offline)."""

from __future__ import annotations

import pytest

import eval.run as ev
from eval.mutate import BUG_IDS, make_rng
from eval.suite import build_suite
from proof.check import lean_bridge
from proof.schemas import CheckResult, Violation
from proof.spec.rules import build_rulespec


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    """1 seed x 7 bugs x 1 placement + 1 clean model, run with the baseline."""
    root = tmp_path_factory.mktemp("suite")
    ev.CACHE_DIR = tmp_path_factory.mktemp("cache")
    manifest = build_suite(root, bug_seeds=(101,), clean_seeds=(201,), placements=1)
    res = ev.run_suite(manifest, root, agent=False, lean=lean_bridge.is_available(),
                       log=lambda *_: None)
    return manifest, root, res


def test_suite_shape(tmp_path):
    m = build_suite(tmp_path, bug_seeds=(101,), clean_seeds=(201, 202), placements=2,
                    bugs=("dup_row", "sign_flip"))
    assert len(m["models"]) == 1 * 2 * 2 + 2
    for e in m["models"]:
        assert (tmp_path / e["path"]).is_file()
        if e["kind"] == "buggy":
            t = e["truth"]
            assert t["bug_id"] in BUG_IDS and t["cells_affected"] and t["expected_invariants"]
        else:
            assert e["truth"] is None


def test_default_suite_size():
    from eval import suite
    assert len(suite.BUG_SEEDS) * len(BUG_IDS) * suite.PLACEMENTS == 105
    assert len(suite.CLEAN_SEEDS) == 20
    assert not set(suite.BUG_SEEDS) & set(suite.CLEAN_SEEDS)


def test_rng_is_deterministic_and_distinct():
    a = make_rng(1, "dup_row", 0).random()
    assert a == make_rng(1, "dup_row", 0).random()
    assert a != make_rng(1, "dup_row", 1).random()
    assert a != make_rng(2, "dup_row", 0).random()


def test_every_bug_is_caught_by_baseline_with_right_invariant_and_place(small):
    _, _, res = small
    buggy = [m for m in res["models"] if m["kind"] == "buggy"]
    assert {m["bug_id"] for m in buggy} == set(BUG_IDS)
    for m in buggy:
        s = m["sources"]["rules"]
        assert s["status"] == "ok", (m["id"], s["error"])
        py = s["python"]
        assert py["detected"] and py["correct_invariant"] and py["localized"], (m["id"], py)


def test_clean_model_has_no_violations(small):
    _, _, res = small
    clean = next(m for m in res["models"] if m["kind"] == "clean")
    py = clean["sources"]["rules"]["python"]
    assert not py["detected"] and py["n_violations"] == 0


@pytest.mark.skipif(not lean_bridge.is_available(), reason="Lean binary not built")
def test_python_and_lean_agree_on_suite(small):
    _, _, res = small
    assert all(m["sources"]["rules"]["agree"] for m in res["models"])


def test_mutations_change_the_workbook_not_just_the_truth(small):
    manifest, root, _ = small
    from openpyxl import load_workbook
    from synth.company import generate
    from synth.workbook import write_workbook
    clean = load_workbook(write_workbook(generate(101), root / "_clean.xlsx"))
    for e in manifest["models"]:
        if e["kind"] != "buggy":
            continue
        wb = load_workbook(root / e["path"])
        same = all(
            [[c.value for c in r] for r in wb[n].iter_rows()]
            == [[c.value for c in r] for r in clean[n].iter_rows()]
            for n in clean.sheetnames)
        assert not same, e["id"]


def test_agent_skipped_without_key_or_cache(small, monkeypatch, tmp_path):
    manifest, root, _ = small
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PROOF_CACHE_DIR", str(tmp_path / "empty"))
    res = ev.run_suite(manifest, root, agent=True, lean=False, limit=2,
                       log=lambda *_: None)
    assert all(m["sources"]["agent"]["status"] == "skipped" for m in res["models"])
    assert ev.compute_metrics(res["models"])["agent"] == {"status": "skipped"}


def test_agent_scored_with_injected_agent(small):
    manifest, root, _ = small
    res = ev.run_suite(manifest, root, agent=True, lean=False, limit=4,
                       agent_fn=lambda graph, ledger, path: build_rulespec(graph),
                       log=lambda *_: None)
    met = ev.compute_metrics(res["models"])["agent"]
    assert met["status"] == "ok"
    assert met["spec_accuracy"]["assignment_equal_rate"] == 1.0
    assert met["spec_accuracy"]["mean_line_accuracy"] == 1.0


def _v(inv, line=None, month=None, txns=()):
    return Violation(invariant=inv, line_id=line, month_idx=month, txn_ids=list(txns),
                     message="m")


def test_score_localization_and_off_target():
    truth = {"expected_invariants": ["traceability"], "runway_cells": ["Runway!D5"],
             "txn_ids": ["ramp-1"]}
    labels = {"saas": "SaaS", "other": "Other"}
    good = CheckResult(ok=False, violations=[_v("traceability", "saas", 2)])
    s = ev.score(good, truth, labels)
    assert s["detected"] and s["correct_invariant"] and s["localized"] and s["off_target"] == 0
    wrong_cell = CheckResult(ok=False, violations=[_v("traceability", "other", 2)])
    s = ev.score(wrong_cell, truth, labels)
    assert s["correct_invariant"] and not s["localized"] and s["off_target"] == 1
    by_txn = CheckResult(ok=False, violations=[_v("traceability", "other", 9, ["ramp-1"])])
    assert ev.score(by_txn, truth, labels)["localized"]
    wrong_inv = CheckResult(ok=False, violations=[_v("coverage", None, 2, ["ramp-1"])])
    s = ev.score(wrong_inv, truth, labels)
    assert s["detected"] and not s["correct_invariant"] and not s["localized"]
    none = ev.score(CheckResult(ok=True, violations=[]), truth, labels)
    assert not none["detected"] and not none["correct_invariant"]
    assert ev.score(CheckResult(ok=True, violations=[]), None, labels)["n_violations"] == 0


def test_report_lists_every_undetected_bug_and_refusal(small):
    manifest, _, res = small
    models = [dict(m, sources=dict(m["sources"])) for m in res["models"]]
    miss = next(m for m in models if m["bug_id"] == "dup_row")
    miss["sources"]["rules"] = dict(miss["sources"]["rules"], python={
        "detected": False, "correct_invariant": False, "localized": False,
        "n_violations": 0, "invariants": [], "off_target": 0})
    err = next(m for m in models if m["bug_id"] == "sign_flip")
    err["sources"]["rules"] = {"status": "error", "error": "UnsupportedFormula: x"}
    out = {"models": models, "metrics": ev.compute_metrics(models),
           "meta": {"agent_model": "m", "confidence_min": 0.8, "lean": False,
                    "agent_status": "skipped"}}
    md = ev.render_markdown(out, manifest)
    assert "## Known misses" in md
    assert miss["id"] in md and err["id"] in md
    assert "pipeline refused: UnsupportedFormula: x" in md
    assert "unexplained" in md                      # no invented excuse
    rules = out["metrics"]["rules"]["python"]["overall"]
    assert rules["n"] == 7 and rules["detected"] == 5 and rules["errors"] == 1
    assert out["metrics"]["rules"]["python"]["clean"]["flagged"] == 0


def test_fix_metrics_count_unrun_models_separately():
    from eval.fix_run import summarize
    ok = {"status": "ok", "bug_id": "dup_row", "fixed": True, "first_try": True,
          "needs_human": 0, "restored_clean": True, "llm_calls": 1, "attempts": 1}
    rows = [dict(ok, id="a"), dict(ok, id="b", fixed=False, needs_human=1, restored_clean=None),
            {"status": "error", "bug_id": "dup_row", "id": "c", "error": "billing"},
            {"status": "skipped", "bug_id": "dup_row", "id": "d", "error": "no key"}]
    o = summarize(rows, ["dup_row"])["overall"]
    assert (o["n"], o["ran"], o["errors"], o["skipped"]) == (4, 2, 1, 1)
    assert o["fixed"] == 1 and o["fix_rate"] == 0.5          # of those that ran
    assert o["restored_clean"] == 1 and o["restored_clean_of"] == 1


def test_known_miss_note_is_rendered(small):
    manifest, _, res = small
    models = [dict(m, sources=dict(m["sources"])) for m in res["models"]]
    m = next(x for x in models if x["bug_id"] == "dup_row")
    m["id"] = "overlap_category/s105_p2"
    m["sources"]["rules"] = dict(m["sources"]["rules"], python={
        "detected": True, "correct_invariant": True, "localized": False,
        "n_violations": 1, "invariants": ["traceability"], "off_target": 1})
    models = [x for x in models if x is m or x["kind"] == "clean"]
    models[0]["sources"]["agent"] = dict(models[0]["sources"]["rules"])
    for x in models:
        x["sources"]["agent"] = {"status": "skipped", "error": "x"}
    m["sources"]["agent"] = dict(m["sources"]["rules"])
    out = {"models": models, "metrics": ev.compute_metrics(models),
           "meta": {"agent_model": "m", "confidence_min": 0.8, "lean": False,
                    "agent_status": "ran"}}
    assert "misread the `Other` row" in ev.render_markdown(out, manifest)
