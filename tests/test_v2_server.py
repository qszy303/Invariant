"""The v2 FastAPI app: check -> apply fix -> re-verify, eval and proven endpoints (offline, demo caches)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app import core  # noqa: E402
from app.v2.server import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def demo_env():
    mp = pytest.MonkeyPatch()
    mp.setenv("PROOF_DEMO", "1")
    mp.setenv("PROOF_OFFLINE", "1")
    from app.v2.server import DEMO_CACHE
    mp.setenv("PROOF_CACHE_DIR", str(DEMO_CACHE))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_AI_API_KEY", "PROOF_SPEC"):
        mp.delenv(k, raising=False)
    yield
    mp.undo()


def check(name: str) -> dict:
    r = client.post("/api/check", json={"demo_file": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_clean_has_no_violations_and_matching_cash():
    d = check("slope_clean.xlsx")
    assert d["result"]["ok"] and d["kpi"]["violations"] == 0
    assert d["kpi"]["claimed_end_cents"] == d["kpi"]["verified_end_cents"]


def test_apply_verified_fix_clears_the_cell_with_no_new_violations():
    d = check("slope_dup_row.xlsx")
    (v,) = d["result"]["violations"]
    assert v["cell"] and d["fixes"][v["key"]]["verified"]
    assert d["kpi"]["claimed_end_cents"] != d["kpi"]["verified_end_cents"]
    out = client.post("/api/fix/apply", json={"violation_key": v["key"]}).json()
    assert out["resolved"] is True and out["new_violations"] == 0
    assert out["kpi"]["violations"] == 0
    assert out["kpi"]["claimed_end_cents"] == out["kpi"]["verified_end_cents"]


def test_demo_uses_cached_llm_spec_offline():
    d = check("slope_dup_row.xlsx")
    assert d["spec"] == "llm" and d["spec_model"] and d["spec_note"] is None


def test_hardcoded_rent_is_flagged_for_review_by_the_model():
    d = check("slope_hardcoded_rent.xlsx")
    assert d["spec"] == "llm" and d["needs_review"] == ["rent"]


def test_one_fix_clears_all_sibling_violations(monkeypatch):
    monkeypatch.setenv("PROOF_SPEC", "rules")  # the precomputed fixes are for the rules reading
    monkeypatch.setenv("PROOF_CACHE_DIR", str(core.SAMPLES_DIR / "cache"))
    d = check("slope_missing_vendor.xlsx")
    assert d["spec"] == "rules"
    assert d["kpi"]["violations"] == 5
    out = client.post("/api/fix/apply",
                      json={"violation_key": d["result"]["violations"][0]["key"]}).json()
    assert out["resolved"] and out["kpi"]["violations"] == 0


def test_unknown_inputs_are_clean_errors():
    assert client.post("/api/check", json={"demo_file": "nope.xlsx"}).status_code == 404
    assert client.post("/api/fix/apply", json={"violation_key": "x"}).status_code in (404, 409)
    bad = client.post("/api/check", files={"file": ("a.xlsx", b"not a zip")})
    assert bad.status_code == 400


def test_eval_and_proven():
    e = client.get("/api/eval").json()
    assert "metrics" in e and "known_misses" in e
    p = client.get("/api/proven").json()
    assert p["theorems"] and p["proven"] and p["not_proven"]


def test_openai_proposer_maps_function_call_and_caches(tmp_path):
    import json
    from types import SimpleNamespace as NS

    from app.v2.openai_proposer import OpenAIProposer

    args = {"kind": "edit_formula", "cell_ref": "Runway!C2", "new_formula": "=B11", "reason": "link"}
    calls = []

    def create(**kw):
        calls.append(kw)
        fn = NS(name="propose_fix", arguments=json.dumps(args))
        return NS(choices=[NS(message=NS(tool_calls=[NS(function=fn)]))])

    client = NS(chat=NS(completions=NS(create=create)))
    p = OpenAIProposer(client=client, model="m", cache_dir=tmp_path)
    assert p({"x": 1}) == args and p({"x": 1}) == args
    assert len(calls) == 1 and p.calls == 1  # second call served from cache
    assert calls[0]["tool_choice"]["function"]["name"] == "propose_fix"
    assert calls[0]["tools"][0]["function"]["parameters"]["required"] == ["kind", "reason"]


def test_no_apply_on_rows_flagged_for_review():
    # In the cached demo the LLM spec misreads Other (flagged for review); every fix that
    # touches Other is blocked, in the payload and at the endpoint.
    d = check("slope_missing_vendor.xlsx")
    assert "other" in d["needs_review"]
    blocked = {k: f for k, f in d["fixes"].items() if f and f.get("blocked")}
    assert blocked, "expected fixes on the review row to be blocked"
    for f in blocked.values():
        assert f["blocked_reason"] == "Confirm what this row should contain before fixing."
    on_other = [v["key"] for v in d["result"]["violations"] if v["line_id"] == "other"]
    assert on_other and all(d["fixes"][k]["blocked"] for k in on_other if d["fixes"][k])
    r = client.post("/api/fix/apply", json={"violation_key": next(iter(blocked)),
                                            "check_id": d["check_id"]})
    assert r.status_code == 409 and "Confirm what this row" in r.json()["error"]


def test_fix_not_blocked_when_nothing_needs_review():
    d = check("slope_dup_row.xlsx")
    assert d["needs_review"] == []
    assert not any((f or {}).get("blocked") for f in d["fixes"].values())


def test_fernway_demo_flags_the_dropped_month_and_verifies_the_fix():
    """The larger demo company: one planted dropped month, read by the cached model."""
    d = check("fernway_dropped_month.xlsx")
    assert d["spec"] == "llm" and d["needs_review"] == []
    (v,) = d["result"]["violations"]
    assert v["invariant"] == "traceability" and v["cell"] == {"row": "SaaS", "month": 7}
    assert d["fixes"][v["key"]]["verified"] and d["lean_agrees"]
    assert d["seconds"] < 3
    out = client.post("/api/fix/apply", json={"violation_key": v["key"]}).json()
    assert out["resolved"] is True and out["new_violations"] == 0
    assert out["kpi"]["claimed_end_cents"] == out["kpi"]["verified_end_cents"]


def test_demo_picker_lists_the_fernway_file():
    files = {f["file"] for f in client.get("/api/demo-files").json()}
    assert "fernway_dropped_month.xlsx" in files
