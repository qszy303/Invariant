"""A8 tests: app logic and the Invariant web API."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from app import core
from proof.fix.explain import analyze, content_hash

SAMPLES = core.SAMPLES_DIR
BUGGY = {"slope_dup_row.xlsx": 1, "slope_missing_vendor.xlsx": 5,
         "slope_broken_rollforward.xlsx": 1}


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    monkeypatch.setenv("PROOF_OFFLINE", "1")
    monkeypatch.setenv("PROOF_CACHE_DIR", str(SAMPLES / "cache"))


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_sample_bundle_is_complete():
    for name in core.SAMPLE_FILES:
        assert (SAMPLES / name).is_file(), name
    for name in BUGGY:
        assert core.load_fixrun(SAMPLES / name) is not None
    assert core.load_fixrun(SAMPLES / "slope_clean.xlsx") is None


@pytest.mark.parametrize("name,n", [("slope_clean.xlsx", 0), *BUGGY.items()])
def test_check_is_fast_and_finds_the_planted_bug(name, n):
    v = core.check_view(SAMPLES / name)
    assert len(v.violations) == n
    assert v.seconds < 3, f"{v.seconds}s"          # spec: under 3s with cache
    assert len(v.explanations) == n
    if v.checker == "Lean":
        assert v.lean_agrees


def test_cell_colors():
    v = core.check_view(SAMPLES / "slope_dup_row.xlsx")
    assert v.status[("SaaS", 5)] == "violated"
    assert sum(1 for s in v.status.values() if s == "violated") == 1
    assert v.status[("Rent", 5)] == "ok"
    labels, values, marks = core.grid(v)
    assert labels[0] == "Starting Cash" and len(values[0]) == 12
    assert marks[labels.index("SaaS")][5] == "violated"
    assert core.violation_for_cell(v, "SaaS", 5) == 0
    assert core.violation_for_cell(v, "SaaS", 4) is None


def test_low_confidence_spec_lines_are_amber():
    from proof.spec.rules import build_rulespec

    def unsure(graph, ledger, path):
        spec = build_rulespec(graph)
        spec.lines[3] = spec.lines[3].model_copy(update={"confidence": 0.5})
        return spec

    v = core.check_view(SAMPLES / "slope_clean.xlsx", unsure)
    assert v.review_lines and v.status[("SaaS", 0)] == "review"
    assert v.status[("Rent", 0)] == "ok"


@pytest.mark.parametrize("name", list(BUGGY))
def test_apply_fix_makes_a_clean_copy_offline(name, tmp_path, monkeypatch):
    v = core.check_view(SAMPLES / name)
    rep = core.report_for(core.load_fixrun(SAMPLES / name), v.violations[0])
    assert rep.status == "verified"
    before = _sha(SAMPLES / name)
    out = core.apply_fix(v, rep.fix, tmp_path / "fixed.xlsx")
    assert _sha(SAMPLES / name) == before                       # original untouched
    # the fixed copy is in the shipped cache, so re-checking it needs no recalculation
    assert (SAMPLES / "cache" / "values" / f"{content_hash(out)}.json").exists()
    assert analyze(out).result.ok


def test_fix_text():
    run = core.load_fixrun(SAMPLES / "slope_dup_row.xlsx")
    old, new = core.fix_text(run.reports[0].fix)
    assert "Ramp row 122" in old and new == "(row deleted)"
    run = core.load_fixrun(SAMPLES / "slope_broken_rollforward.xlsx")
    old, new = core.fix_text(run.reports[0].fix)
    assert old.startswith("Runway!C2") and new == "=B11"


# ---- the web API (real server on a free port) --------------------------------------------

import json
import threading
import urllib.error
import urllib.request

from app import server


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.delenv("PROOF_OFFLINE", raising=False)
    mp.setenv("PROOF_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    mp.undo()


def call(base, path, body=None, raw=None, method=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if method is None:
        method = "POST" if data is not None or path.endswith(("/fixes", "/apply")) else "GET"
    req = urllib.request.Request(base + path, data=data if method == "POST" else None,
                                 method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def jcall(base, path, body=None, **kw):
    status, _, data = call(base, path, body, **kw)
    return status, json.loads(data)


def start(base, sample):
    status, s = jcall(base, "/api/session", {"sample": sample})
    assert status == 200, s
    return s


def test_static_page_and_branding(base):
    status, headers, data = call(base, "/")
    page = data.decode()
    assert status == 200 and "Invariant" in page and "Proof" not in page.replace("ProofCheck", "")
    assert call(base, "/app.js")[1]["Content-Type"].startswith("text/javascript") or \
        "javascript" in call(base, "/app.js")[1]["Content-Type"]
    assert call(base, "/../server.py")[0] == 404
    assert call(base, "/%2e%2e/server.py")[0] == 404


def test_status_and_samples(base):
    st = jcall(base, "/api/status")[1]
    assert set(st) == {"has_key", "offline", "lean"}
    files = {x["file"] for x in jcall(base, "/api/samples")[1]}
    assert files == set(core.SAMPLE_FILES)


def test_proven_facts(base):
    f = jcall(base, "/api/proven")[1]
    assert "checkTraceability_iff" in f["theorem"] and f["proven"] and f["not_proven"]


def test_session_shape_for_a_buggy_sample(base):
    s = start(base, "slope_dup_row.xlsx")
    assert s["summary"]["violations"] == 1 and s["summary"]["checks"] == 5
    assert len(s["rows"]) == 10 and all(len(r["cells"]) == 12 for r in s["rows"])
    saas = next(r for r in s["rows"] if r["label"] == "SaaS")
    assert [c["s"] for c in saas["cells"]].count("violated") == 1 and saas["cells"][5]["s"] == "violated"
    assert saas["cells"][5]["ref"] == "G5" and saas["cells"][5]["f"].startswith("=-SUMIFS")
    jan = next(r for r in s["rows"] if r["label"] == "Starting Cash")["cells"][0]
    assert jan["f"] is None                                      # the typed input is visible
    v = s["violations"][0]
    assert v["cell"] == {"row": "SaaS", "month": 5} and v["invariant"] == "traceability"
    assert v["txns"] and v["duplicate_rows"] and "appears twice" in v["explanation"]
    assert v["fix"]["verified"] and v["fix"]["kind"] == "delete_duplicate_row"
    assert v["fix"]["recheck"] == {"target_cleared": True, "new_violations": 0}
    assert s["fixes"] == {"found": True, "todo": 1, "can_download": False}


def test_clean_sample_has_nothing_to_apply(base):
    s = start(base, "slope_clean.xlsx")
    assert s["summary"]["violations"] == 0 and s["violations"] == []
    assert s["fixes"]["todo"] == 0
    assert jcall(base, f"/api/session/{s['sid']}/apply", {"all": True})[0] == 409
    assert call(base, f"/api/session/{s['sid']}/download")[0] == 409


def test_apply_all_then_download_leaves_original_alone(base):
    before = _sha(SAMPLES / "slope_dup_row.xlsx")
    s = start(base, "slope_dup_row.xlsx")
    status, s2 = jcall(base, f"/api/session/{s['sid']}/apply", {"all": True})
    assert status == 200 and s2["summary"]["violations"] == 0 and s2["version"] == 1
    assert "untouched" in s2["flash"] and s2["fixes"]["can_download"]
    assert jcall(base, f"/api/session/{s['sid']}")[1]["flash"] is None     # flash shows once
    status, headers, data = call(base, f"/api/session/{s['sid']}/download")
    assert status == 200 and data[:2] == b"PK" and "dup_row-fixed.xlsx" in headers["Content-Disposition"]
    assert _sha(SAMPLES / "slope_dup_row.xlsx") == before


def test_apply_one_violation_and_bad_requests(base):
    s = start(base, "slope_missing_vendor.xlsx")
    sid = s["sid"]
    assert s["summary"]["violations"] == 5 and s["fixes"]["todo"] == 1   # one fix, five violations
    assert jcall(base, f"/api/session/{sid}/apply", {"violation": 99})[0] == 400
    assert jcall(base, f"/api/session/{sid}/apply", {})[0] == 400
    status, s2 = jcall(base, f"/api/session/{sid}/apply", {"violation": 3})
    assert status == 200 and s2["summary"]["violations"] == 0
    assert jcall(base, f"/api/session/{sid}/apply", {"all": True})[0] == 409   # nothing left


def test_errors_are_json_and_never_tracebacks(base):
    assert jcall(base, "/api/session/nope")[0] == 404
    assert jcall(base, "/api/session", {"sample": "../../etc/passwd"})[0] == 404
    assert jcall(base, "/api/nothing")[0] == 404
    status, body = jcall(base, "/api/upload?name=x.xlsx", raw=b"not a zip file")
    assert status == 400 and body["error"] == "not_xlsx"
    status, body = jcall(base, "/api/upload?name=x.txt", raw=b"PK\x03\x04junk")
    assert status == 400
    status, body = jcall(base, "/api/key", {"key": "short"})
    assert status == 400 and body["error"] == "bad_key"
    status, data = call(base, "/api/session", raw=b"{not json")[0], None
    assert status == 400


def test_workbook_without_a_runway_tab_is_rejected_cleanly(base, tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "bad.xlsx"
    Workbook().save(p)
    status, body = jcall(base, "/api/upload?name=bad.xlsx", raw=p.read_bytes())
    assert status == 422 and body["error"] == "unreadable" and "Invariant could not read" in body["message"]


def _upload_buggy(base, tmp_path, seed=9, bug="dup_row"):
    from synth.company import generate
    from synth.workbook import write_workbook
    p = write_workbook(generate(seed), tmp_path / f"{bug}.xlsx", bug)
    status, s = jcall(base, f"/api/upload?name={bug}.xlsx", raw=p.read_bytes())
    assert status == 200, s
    return s


def test_upload_find_fixes_apply_download_with_scripted_proposer(base, tmp_path, monkeypatch):
    from proof.fix.verify import fix_workbook
    from tests.test_a7_fix import oracle
    s = _upload_buggy(base, tmp_path)
    assert s["summary"]["violations"] == 1 and s["fixes"]["found"] is False
    assert s["violations"][0]["fix"] is None and s["fixes"]["todo"] == 0
    monkeypatch.setattr(core, "compute_fixrun",
                        lambda path, wd: fix_workbook(path, oracle, work_dir=wd))
    status, s2 = jcall(base, f"/api/session/{s['sid']}/fixes")
    assert status == 200 and s2["fixes"]["found"] and s2["fixes"]["todo"] == 1
    assert s2["violations"][0]["fix"]["verified"]
    status, s3 = jcall(base, f"/api/session/{s['sid']}/apply", {"all": True})
    assert status == 200 and s3["summary"]["violations"] == 0
    assert call(base, f"/api/session/{s['sid']}/download")[2][:2] == b"PK"


def test_find_fixes_without_a_key_says_so(base, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PROOF_CACHE_DIR", str(tmp_path / "emptycache"))
    s = _upload_buggy(base, tmp_path, seed=11, bug="broken_rollforward")
    status, body = jcall(base, f"/api/session/{s['sid']}/fixes")
    assert status == 402 and body["error"] == "no_key" and "ANTHROPIC_API_KEY" in body["message"]
    assert jcall(base, f"/api/session/{s['sid']}")[1]["fixes"]["found"] is False


def test_find_fixes_reports_a_failing_model_not_a_verdict(base, tmp_path, monkeypatch):
    from proof.fix.verify import fix_workbook

    def broke(req):
        raise RuntimeError("Your credit balance is too low to access the Anthropic API.")

    s = _upload_buggy(base, tmp_path, seed=12, bug="broken_rollforward")
    monkeypatch.setattr(core, "compute_fixrun",
                        lambda path, wd: fix_workbook(path, broke, work_dir=wd))
    status, body = jcall(base, f"/api/session/{s['sid']}/fixes")
    assert status == 502 and body["error"] == "proposer_failed" and "credit balance" in body["message"]
    assert jcall(base, f"/api/session/{s['sid']}")[1]["fixes"]["found"] is False   # not stored as "needs human"


def test_unverified_fix_is_shown_but_cannot_be_applied(base, tmp_path, monkeypatch):
    from proof.fix.verify import fix_workbook
    from tests.test_a7_fix import noop
    s = _upload_buggy(base, tmp_path, seed=13, bug="broken_rollforward")
    monkeypatch.setattr(core, "compute_fixrun",
                        lambda path, wd: fix_workbook(path, noop, work_dir=wd))
    status, s2 = jcall(base, f"/api/session/{s['sid']}/fixes")
    f = s2["violations"][0]["fix"]
    assert status == 200 and f["verified"] is False and len(f["attempts"]) == 3
    assert s2["fixes"]["todo"] == 0
    assert jcall(base, f"/api/session/{s['sid']}/apply", {"violation": 0})[0] == 409
