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
    mp.setenv("PROOF_CACHE_DIR", str(core.SAMPLES_DIR / "cache"))
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


def test_one_fix_clears_all_sibling_violations():
    d = check("slope_missing_vendor.xlsx")
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
