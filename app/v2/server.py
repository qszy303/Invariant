"""Invariant web server, v2: FastAPI + one static page.  `python -m app.v2.server`

A thin JSON layer over `app.core` (which calls the existing check / explain / fix
pipeline; nothing in `proof/` is changed). Runs fully offline: the page ships its own
fonts, and PROOF_DEMO=1 serves cached LLM responses so output is deterministic.

  GET  /api/demo-files          bundled demo workbooks (for the picker)
  POST /api/check               {"demo_file": name}  or multipart "file" upload
  POST /api/fix/apply           {"violation_key": k, "check_id"?: id}
  GET  /api/eval                eval/results.json (+ "known_misses" from eval/RESULTS.md)
  GET  /api/proven              theorem statements + what is / is not proven
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import core
from app.v2.openai_proposer import OpenAIProposer
from proof.fix.explain import content_hash, vkey
from proof.fix.report import FixReport, FixRun
from proof.fix.verify import fix_workbook
from proof.openai_client import OpenAIAsAnthropic, openai_key
from proof.openai_client import default_model as openai_model
from proof.spec.agent import DEFAULT_MODEL, AgentError, build_rulespec_llm, export_headers, \
    read_row_notes
from proof.spec.rules import build_rulespec

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
ROOT = core.ROOT
MAX_UPLOAD = 25 * 1024 * 1024
DEMO_CACHE = ROOT / "demo" / "cache"     # committed: spec + fix responses + values for demo files
DEMO_FILES = {**core.SAMPLE_FILES,
              "slope_hardcoded_rent.xlsx": "Rent typed in from the lease (an ambiguous row)"}


def demo_mode() -> bool:
    return os.environ.get("PROOF_DEMO") == "1"


# ---- which model reads the spec and proposes fixes -------------------------------------

class _NoNetwork:
    """Stand-in client for PROOF_DEMO=1: a cache miss fails loudly instead of calling out."""

    def __init__(self):
        self.messages = self
        self.chat = type("C", (), {"completions": self})()

    def create(self, **_):
        raise AgentError("PROOF_DEMO=1 and no cached LLM response for this input")


@dataclass
class LLM:
    provider: str | None        # "anthropic" | "openai" | None (no key: rules only)
    spec_client: Any
    spec_model: str
    proposer: Any               # fix proposer, or None


def llm_config() -> LLM:
    if demo_mode():
        meta = {}
        try:
            meta = json.loads((DEMO_CACHE / "meta.json").read_text())
        except (OSError, ValueError):
            pass
        prov = meta.get("provider", "anthropic")
        model = meta.get("spec_model", DEFAULT_MODEL)
        if prov == "openai":
            prop = OpenAIProposer(client=_NoNetwork(), model=meta.get("fix_model"),
                                  cache_dir=DEMO_CACHE)
        else:
            from proof.fix.propose import LLMProposer
            prop = LLMProposer(client=_NoNetwork(), model=meta.get("fix_model"),
                               cache_dir=DEMO_CACHE)
        return LLM(prov, _NoNetwork(), model, prop)
    if core.has_key():
        from proof.fix.propose import LLMProposer
        return LLM("anthropic", None, os.environ.get("PROOF_MODEL", DEFAULT_MODEL),
                   LLMProposer(cache_dir=core.cache_dir()))
    if openai_key():
        return LLM("openai", OpenAIAsAnthropic(), openai_model(),
                   OpenAIProposer(cache_dir=core.cache_dir()))
    return LLM(None, None, DEFAULT_MODEL, None)


def spec_mode() -> str:
    """PROOF_SPEC=rules forces the deterministic baseline; default is the LLM spec agent."""
    return "rules" if os.environ.get("PROOF_SPEC") == "rules" else "llm"


def make_spec_fn(cfg: LLM, status: dict[str, Any]):
    """Spec reader for analyze(): the LLM agent, falling back to rules (and saying so)."""
    def fn(graph, ledger, path):
        if spec_mode() == "rules":
            status.update(spec="rules", note="rules baseline selected (PROOF_SPEC=rules)")
            return build_rulespec(graph)
        try:
            spec = build_rulespec_llm(graph, ledger, headers=export_headers(path),
                                      notes=read_row_notes(path), client=cfg.spec_client,
                                      model=cfg.spec_model)
            status.update(spec="llm", model=cfg.spec_model, note=None)
            return spec
        except Exception as e:  # noqa: BLE001 - fall back, visibly
            status.update(spec="rules", note=f"LLM spec unavailable ({type(e).__name__}: "
                                             f"{str(e)[:160]}); used the rules baseline")
            return build_rulespec(graph)
    return fn


# ---- state (one local user; in memory) -----------------------------------------------

@dataclass
class Check:
    id: str
    name: str
    work: Path
    cur: Path
    fixrun: FixRun | None
    version: int = 0
    applied: list[str] = field(default_factory=list)
    fix_error: str | None = None


CHECKS: dict[str, Check] = {}
LATEST: list[str] = []
_VIEWS: dict[tuple[str, str], tuple[core.View, dict[str, Any]]] = {}
_LOCK = threading.Lock()
_TMP = Path(tempfile.mkdtemp(prefix="invariant_v2_"))


def _view(path: Path) -> core.View:
    return _view_status(path)[0]


def _view_status(path: Path) -> tuple[core.View, dict[str, Any]]:
    key = (content_hash(path), spec_mode())
    with _LOCK:
        if key not in _VIEWS:
            status: dict[str, Any] = {}
            _VIEWS[key] = (core.check_view(path, make_spec_fn(llm_config(), status)), status)
        return _VIEWS[key]


# ---- JSON shaping ------------------------------------------------------------------------

def violation_key(v) -> str:
    k = vkey(v)
    return ":".join([k[0], ",".join(k[1]) if isinstance(k[1], tuple) else str(k[1]),
                     "" if len(k) < 3 else str(k[2])])


def _root_report(run: FixRun | None, rep: FixReport | None) -> FixReport | None:
    """Siblings (resolved_by) share the fix of the report that cleared them."""
    if run is None or rep is None:
        return rep
    seen = 0
    while rep is not None and rep.fix is None and rep.resolved_by is not None and seen < 20:
        rep = run.reports[rep.resolved_by]
        seen += 1
    return rep


def _fix_json(run: FixRun | None, v) -> dict[str, Any] | None:
    root = _root_report(run, core.report_for(run, v))
    if root is None:
        return None
    rc = root.recheck
    verified = bool(root.status == "verified" and root.fix is not None
                    and rc is not None and rc.target_cleared and not rc.new_violations)
    out: dict[str, Any] = {"verified": verified,
                           "note": ("; ".join(a.detail for a in root.attempts if a.detail)
                                    or root.note)[:400]}
    if root.fix is not None:
        old, new = core.fix_text(root.fix)
        out.update(kind=root.fix.kind, old=old, new=new, reason=root.fix.reason,
                   cell_ref=root.cell_ref, target_ref=root.fix.cell_ref,
                   recheck={"target_cleared": bool(rc and rc.target_cleared),
                            "new_violations": len(rc.new_violations) if rc else 0})
    return out


REVIEW_BLOCK = "Confirm what this row should contain before fixing."


def review_block(v: core.View, viol, fix: dict[str, Any] | None) -> bool:
    """True when the spec is not trusted for the row this violation sits on, or for a row
    the proposed fix would edit. A fix "verified" against a low-confidence reading of a
    row can rewrite a correct formula to match the misreading, so it is not offered."""
    review = set(v.review_lines)
    if not review:
        return False
    an = v.analysis
    labels = {s.line_id: s.row_label for s in an.spec.lines}
    if labels.get(viol.line_id or "") in review:
        return True
    ref = (fix or {}).get("cell_ref") or ""
    by_ref = {c.ref.split("!")[-1]: c.row_label for c in an.graph.cells}
    target = (fix or {}).get("target_ref")
    for r in (ref, target):
        if r and by_ref.get(r.split("!")[-1]) in review:
            return True
    return False


def _ending_cash(v: core.View) -> tuple[int | None, int | None]:
    """(claimed, verified) Dec ending cash in cents.

    claimed  = what the sheet's Ending Cash cell says for the last month.
    verified = opening cash + every transaction in the export tabs, which is what the
               money actually did if the ledger is complete.
    """
    an = v.analysis
    cells = {(c.row_label, c.month_idx): c for c in an.graph.cells}
    start, end = cells.get(("Starting Cash", 0)), cells.get(("Ending Cash", 11))
    if start is None or end is None:
        return None, None
    return end.value_cents, start.value_cents + sum(t.amount_cents for t in an.ledger.transactions)


def check_json(c: Check, v: core.View) -> dict[str, Any]:
    st = _view_status(c.cur)[1]
    an = v.analysis
    line_of = {s.row_label: (s.line_id, s.kind) for s in an.spec.lines}
    labels, values, marks = core.grid(v)
    by = {(x.row_label, x.month_idx): x for x in an.graph.cells}
    vio_at: dict[tuple[str, int], str] = {}
    violations, explanations, fixes = [], {}, {}
    for i, viol in enumerate(v.violations):
        key = violation_key(viol)
        text, ctx = v.explanations[i]
        pos = core.violation_cell_pos(v, viol)
        if pos:
            vio_at[(pos["row"], pos["month"])] = key
        txns = ctx.get("matched_txns") or ctx.get("problem_txns") or []
        violations.append({
            "key": key, "invariant": viol.invariant, "line_id": viol.line_id,
            "kind": line_of.get(next((s.row_label for s in an.spec.lines
                                      if s.line_id == viol.line_id), ""), (None, None))[1],
            "month_idx": viol.month_idx, "label": core.violation_label(v, i),
            "cell": pos, "message": viol.message, "expected_cents": viol.expected_cents,
            "actual_cents": viol.actual_cents, "txn_ids": viol.txn_ids, "txns": txns,
            "txns_title": {"coverage": "Transaction counted nowhere",
                           "no_double_count": "Transaction counted twice"}.get(
                viol.invariant, "Transactions that belong in this cell"),
            "duplicate_rows": ctx.get("duplicate_rows", [])})
        explanations[key] = text
        fx = _fix_json(c.fixrun, viol)
        if fx is not None and review_block(v, viol, fx):
            fx.update(blocked=True, blocked_reason=REVIEW_BLOCK)
        fixes[key] = fx
    rows = []
    for li, label in enumerate(labels):
        lid, kind = line_of.get(label, (None, None))
        cells = []
        for m in range(12):
            x = by.get((label, m))
            cells.append(None if x is None else {
                "ref": x.ref.split("!")[-1], "value_cents": x.value_cents,
                "formula": x.formula, "state": marks[li][m],
                "violation_key": vio_at.get((label, m))})
        rows.append({"label": label, "line_id": lid, "kind": kind, "cells": cells})
    review_ids = [s.line_id for s in an.spec.lines if s.row_label in v.review_lines]
    claimed, verified = _ending_cash(v)
    return {
        "check_id": c.id, "name": c.name, "version": c.version,
        "cellgraph": {"months": core.MONTHS, "rows": rows},
        "result": {"ok": not v.violations, "violations": violations},
        "needs_review": review_ids, "explanations": explanations, "fixes": fixes,
        "fix_error": c.fix_error,
        "spec": st.get("spec", "rules"), "spec_model": st.get("model"),
        "spec_note": st.get("note"),
        "checker": "lean" if v.checker == "Lean" else "python",
        "lean_agrees": v.lean_agrees, "seconds": v.seconds,
        "kpi": {"checks": v.n_checks, "violations": len(v.violations),
                "needs_review": len(review_ids),
                "claimed_end_cents": claimed, "verified_end_cents": verified},
    }


# ---- app ----------------------------------------------------------------------------------

app = FastAPI(title="Invariant", docs_url=None, redoc_url=None)


@app.middleware("http")
async def no_store(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _fail(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


@app.exception_handler(HTTPException)
async def _http_error(_: Request, e: HTTPException):
    return JSONResponse({"error": e.detail}, status_code=e.status_code)


def start_check(src: Path, name: str, precomputed: bool) -> Check:
    cid = uuid.uuid4().hex[:12]
    work = _TMP / cid
    work.mkdir(parents=True)
    orig, cur = work / "original.xlsx", work / "v0.xlsx"
    shutil.copyfile(src, orig)
    shutil.copyfile(src, cur)
    run = core.load_fixrun(orig) if spec_mode() == "rules" else None
    c = Check(cid, name, work, cur, run)
    try:
        v = _view(cur)
    except (ValueError, KeyError) as e:
        shutil.rmtree(work, ignore_errors=True)
        raise _fail(422, f"Invariant could not read this workbook: {e}") from e
    cfg = llm_config()
    if run is None and v.violations and cfg.proposer is not None:
        try:  # proposals (live, or cached under PROOF_DEMO=1) verified on copies
            c.fixrun = fix_workbook(cur, cfg.proposer, work_dir=work / "fix_0",
                                    spec_fn=make_spec_fn(cfg, {}))
        except Exception as e:  # noqa: BLE001 - the check still works without fixes
            c.fix_error = f"{type(e).__name__}: {e}"
    CHECKS[cid] = c
    LATEST[:] = [cid]
    return c


@app.get("/api/demo-files")
def demo_files():
    return [{"file": n, "label": l} for n, l in DEMO_FILES.items()
            if (core.SAMPLES_DIR / n).exists()]


@app.post("/api/check")
async def api_check(request: Request):
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise _fail(400, "Attach a workbook as the 'file' field.")
        data = await up.read()
        name = Path(up.filename or "upload.xlsx").name
        if len(data) > MAX_UPLOAD:
            raise _fail(413, "File is larger than 25 MB.")
        if not name.lower().endswith(".xlsx") or data[:2] != b"PK":
            raise _fail(400, "Please upload an .xlsx workbook.")
        tmp = _TMP / f"upload_{uuid.uuid4().hex[:8]}.xlsx"
        tmp.write_bytes(data)
        try:
            c = start_check(tmp, name, False)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        try:
            body = await request.json()
        except ValueError:
            raise _fail(400, "Send JSON {\"demo_file\": name} or a multipart upload.")
        name = body.get("demo_file") if isinstance(body, dict) else None
        if name not in DEMO_FILES or not (core.SAMPLES_DIR / name).exists():
            raise _fail(404, "Unknown demo file.")
        c = start_check(core.SAMPLES_DIR / name, name, True)
    return check_json(c, _view(c.cur))


@app.post("/api/fix/apply")
async def api_apply(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    key = body.get("violation_key") if isinstance(body, dict) else None
    cid = (body.get("check_id") if isinstance(body, dict) else None) or (LATEST[0] if LATEST else None)
    c = CHECKS.get(cid or "")
    if c is None:
        raise _fail(404, "No check to apply a fix to. Run a check first.")
    v = _view(c.cur)
    idx = next((i for i, x in enumerate(v.violations) if violation_key(x) == key), None)
    if idx is None:
        raise _fail(404, "That violation is not in the current check.")
    fix = _fix_json(c.fixrun, v.violations[idx])
    root = _root_report(c.fixrun, core.report_for(c.fixrun, v.violations[idx]))
    if not fix or not fix["verified"] or root is None or root.fix is None:
        raise _fail(409, "That violation has no verified fix.")
    if review_block(v, v.violations[idx], fix):
        raise _fail(409, REVIEW_BLOCK)
    before = {violation_key(x) for x in v.violations}
    nxt = c.work / f"v{c.version + 1}.xlsx"
    try:
        core.apply_fix(v, root.fix, nxt)
        nv = _view(nxt)
    except (ValueError, KeyError) as e:
        raise _fail(409, f"Could not apply the fix: {e}") from e
    after = {violation_key(x) for x in nv.violations}
    c.cur, c.version = nxt, c.version + 1
    c.applied.append(root.fix.model_dump_json())
    out = check_json(c, nv)
    out.update(resolved=key not in after, new_violations=len(after - before))
    return out


@app.get("/api/eval")
def api_eval():
    p = ROOT / "eval" / "results.json"
    if not p.exists():
        raise _fail(404, "eval/results.json not found. Run `make eval`.")
    data = json.loads(p.read_text())
    data["known_misses"] = _known_misses()
    return data


def _known_misses() -> list[dict[str, str]]:
    try:
        md = (ROOT / "eval" / "RESULTS.md").read_text()
        part = md.split("## Known misses", 1)[1].split("\n## ", 1)[0]
    except (OSError, IndexError):
        return []
    rows = []
    for line in part.splitlines():
        cells = [x.strip().strip("`") for x in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("spec", "---") and not set(cells[0]) <= {"-"}:
            rows.append({"spec": cells[0], "model": cells[1], "bug": cells[2], "why": cells[3]})
    return rows


@app.get("/api/proven")
def api_proven():
    facts = core.lean_facts()
    theorems = []
    try:
        src = (ROOT / "lean" / "ProofCheck" / "Spec.lean").read_text()
        theorems = [re.sub(r"\s*:= by$", "", m).strip()
                    for m in re.findall(r"theorem check\w+_iff.*?:= by", src, re.S)]
    except OSError:
        pass
    # lean/README.md writes "What is proven" as one wrapped paragraph: rejoin, then split by sentence
    proven = re.split(r"(?<=[.)])\s+(?=[A-Z])", " ".join(facts.get("proven", [])).strip())
    return {"theorems": theorems, "definition": facts.get("definition", ""),
            "proven": [x for x in proven if x], "not_proven": facts.get("not_proven", [])}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env (git-ignored) into the environment.

    Real environment variables win, and empty values are skipped.
    """
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.removeprefix("export ").strip(), v.strip().strip("'\"")
        if k and v:
            os.environ.setdefault(k, v)


# ---- entry point ---------------------------------------------------------------------------

def setup_cache() -> None:
    if demo_mode():  # committed demo cache only; LLM clients are network-blocked stubs
        os.environ["PROOF_OFFLINE"] = "1"
        os.environ["PROOF_CACHE_DIR"] = str(DEMO_CACHE)
    else:
        core.setup_cache_env()


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(prog="python -m app.v2.server")
    ap.add_argument("--port", type=int, default=8502)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)
    load_dotenv()
    setup_cache()
    url = f"http://{args.host}:{args.port}"
    print(f"Invariant is running at {url}"
          + ("  (PROOF_DEMO=1: cached, offline)" if demo_mode() else ""))
    if args.open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
