"""Invariant web server: `python -m app.server` (owner: A8).

A thin JSON API over the check / explain / fix logic in `app.core`, plus the
static frontend in app/static. Standard library only; binds to 127.0.0.1 and
keeps sessions in memory (one local user). Uploaded files and their fixed
copies live in a temp dir; the original upload is never modified.

  GET  /api/status                      keys, checker availability
  GET  /api/samples                     bundled sample workbooks
  POST /api/session   {"sample": file}  start from a sample
  POST /api/upload?name=x.xlsx          start from an uploaded workbook (raw body)
  GET  /api/session/<sid>               full state: summary, grid, violations, fixes
  POST /api/session/<sid>/fixes         ask the model for verified fixes
  POST /api/session/<sid>/apply         {"violation": i} or {"all": true}
  GET  /api/session/<sid>/download      the fixed copy (.xlsx)
  GET  /api/proven                      what the Lean proofs cover
  POST /api/key       {"key": "..."}    set ANTHROPIC_API_KEY for this process
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app import core
from proof.check import lean_bridge
from proof.fix.explain import content_hash
from proof.fix.report import FixRun

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 25 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Session:
    id: str
    name: str
    work: Path
    cur: Path
    version: int = 0
    fixrun: FixRun | None = None
    applied: list[str] = field(default_factory=list)
    flash: str | None = None


SESSIONS: dict[str, Session] = {}
_VIEWS: dict[tuple[str, str], core.View] = {}
_LOCK = threading.Lock()
_ROOT = Path(tempfile.mkdtemp(prefix="proof_app_"))


def _view(s: Session) -> core.View:
    key = (str(s.cur), content_hash(s.cur))
    with _LOCK:
        if key not in _VIEWS:
            _VIEWS[key] = core.check_view(s.cur)
        return _VIEWS[key]


# ---- sessions --------------------------------------------------------------------

def new_session(src: Path, name: str) -> Session:
    sid = uuid.uuid4().hex[:12]
    work = _ROOT / sid
    work.mkdir(parents=True)
    orig = work / "original.xlsx"
    shutil.copyfile(src, orig)
    cur = work / "v0.xlsx"
    shutil.copyfile(src, cur)
    s = Session(sid, name, work, cur, fixrun=core.load_fixrun(orig))
    try:
        _view(s)
    except (ValueError, KeyError) as e:  # unreadable / unsupported workbook
        shutil.rmtree(work, ignore_errors=True)
        raise ApiError(422, "unreadable", f"Invariant could not read this workbook: {e}") from e
    SESSIONS[sid] = s
    return s


def get_session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise ApiError(404, "no_session", "Unknown session; reload the page and start again.")
    return s


# ---- JSON views -----------------------------------------------------------------------

def _todo(s: Session) -> list:
    return [p for p in core.unique_fixes(s.fixrun) if p.model_dump_json() not in s.applied]


def _fix_json(s: Session, v) -> dict[str, Any] | None:
    rep = core.report_for(s.fixrun, v)
    if rep is None:
        return None
    out: dict[str, Any] = {
        "verified": rep.status == "verified" and rep.fix is not None,
        "attempts": [{"outcome": a.outcome, "detail": a.detail} for a in rep.attempts],
    }
    if rep.fix is not None:
        old, new = core.fix_text(rep.fix)
        out.update(kind=rep.fix.kind, old=old, new=new, reason=rep.fix.reason,
                   recheck={"target_cleared": bool(rep.recheck and rep.recheck.target_cleared),
                            "new_violations": len(rep.recheck.new_violations)
                            if rep.recheck else 0})
    return out


def session_json(s: Session) -> dict[str, Any]:
    v = _view(s)
    labels, values, marks = core.grid(v)
    by = {(c.row_label, c.month_idx): c for c in v.analysis.graph.cells}
    rows = []
    for li, label in enumerate(labels):
        cells = []
        for m in range(12):
            c = by.get((label, m))
            cells.append(None if c is None else {
                "v": values[li][m], "s": marks[li][m], "ref": c.ref.split("!")[-1],
                "f": c.formula})
        rows.append({"label": label, "cells": cells})
    violations = []
    for i, viol in enumerate(v.violations):
        text, ctx = v.explanations[i]
        cell = core.violation_cell_pos(v, viol)
        violations.append({
            "i": i, "label": core.violation_label(v, i), "invariant": viol.invariant,
            "cell": cell, "explanation": text,
            "expected_cents": viol.expected_cents, "actual_cents": viol.actual_cents,
            "txns": ctx.get("matched_txns") or ctx.get("problem_txns") or [],
            "txns_title": "contradicting" if ctx.get("problem_txns") else "in this cell",
            "duplicate_rows": ctx.get("duplicate_rows", []),
            "fix": _fix_json(s, viol)})
    flash, s.flash = s.flash, None
    return {
        "sid": s.id, "name": s.name, "version": s.version, "flash": flash,
        "summary": {"checks": v.n_checks, "violations": len(v.violations),
                    "checker": v.checker, "lean_agrees": v.lean_agrees,
                    "seconds": v.seconds, "review_lines": v.review_lines},
        "months": core.MONTHS, "rows": rows, "violations": violations,
        "fixes": {"found": s.fixrun is not None, "todo": len(_todo(s)),
                  "can_download": s.version > 0},
    }


# ---- handlers ----------------------------------------------------------------------------

def find_fixes(s: Session) -> dict[str, Any]:
    if not _view(s).violations:
        return session_json(s)
    try:
        run = core.compute_fixrun(s.cur, s.work / f"fix_{s.version}")
    except Exception as e:  # noqa: BLE001 - reported to the user, not hidden
        raise ApiError(502, "proposer_failed", f"{type(e).__name__}: {e}") from e
    if core.key_missing(run):
        raise ApiError(402, "no_key", "No ANTHROPIC_API_KEY, so no new fix proposals. "
                       "Add your key in Settings, or try a sample workbook.")
    failed = [a.detail for r in run.reports for a in r.attempts
              if a.detail.startswith("no proposal:")]
    if failed:
        raise ApiError(502, "proposer_failed", failed[0][len("no proposal: "):][:400])
    s.fixrun = run
    return session_json(s)


def apply(s: Session, body: dict[str, Any]) -> dict[str, Any]:
    v = _view(s)
    try:
        if body.get("all"):
            todo = _todo(s)
            if not todo:
                raise ApiError(409, "nothing_to_apply", "There are no verified fixes to apply.")
            last, k = core.apply_sequence(s.cur, todo, s.work, s.version)
            s.applied += [p.model_dump_json() for p in todo]
            s.cur, s.version = last, s.version + k
            s.flash = (f"Applied {k} verified fix{'es' if k != 1 else ''} to a copy and re-ran "
                       "the full check. Your original file is untouched.")
        else:
            i = body.get("violation")
            if not isinstance(i, int) or not 0 <= i < len(v.violations):
                raise ApiError(400, "bad_request", "violation must be an index from the list")
            rep = core.report_for(s.fixrun, v.violations[i])
            if rep is None or rep.status != "verified" or rep.fix is None:
                raise ApiError(409, "no_verified_fix", "That violation has no verified fix.")
            nxt = s.work / f"v{s.version + 1}.xlsx"
            core.apply_fix(v, rep.fix, nxt)
            s.applied.append(rep.fix.model_dump_json())
            s.cur, s.version = nxt, s.version + 1
            s.flash = ("Applied the verified fix to a copy and re-ran the full check. "
                       "Your original file is untouched.")
    except ApiError:
        raise
    except ValueError as e:  # ProposalError and friends
        raise ApiError(409, "apply_failed", f"Could not apply the fix: {e}") from e
    return session_json(s)


def status() -> dict[str, Any]:
    return {"has_key": core.has_key(), "offline": core.offline(),
            "lean": lean_bridge.is_available()}


def samples() -> list[dict[str, str]]:
    return [{"file": n, "label": l} for n, l in core.SAMPLE_FILES.items()
            if (core.SAMPLES_DIR / n).exists()]


# ---- HTTP -------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Invariant"

    def log_message(self, fmt, *args):  # quiet; errors surface through responses
        if os.environ.get("PROOF_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ApiError(413, "too_large", "File is larger than 25 MB.")
        return self.rfile.read(n)

    def _dispatch(self, method: str):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if parts[:1] == ["api"]:
                return self._api(method, parts[1:], parse_qs(u.query))
            if method != "GET":
                raise ApiError(405, "method", "GET only")
            return self._static(u.path)
        except ApiError as e:
            self._json({"error": e.code, "message": e.message}, e.status)
        except Exception as e:  # noqa: BLE001 - never leak a traceback to the page
            self._json({"error": "server", "message": f"{type(e).__name__}: {e}"}, 500)

    do_GET = lambda self: self._dispatch("GET")    # noqa: E731
    do_POST = lambda self: self._dispatch("POST")  # noqa: E731

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            raise ApiError(404, "not_found", "Not found")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text")
                                                 or "javascript" in ctype else ""))

    def _json_body(self) -> dict[str, Any]:
        try:
            data = json.loads(self._body() or b"{}")
        except ValueError as e:
            raise ApiError(400, "bad_json", "Request body is not valid JSON") from e
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "Request body must be a JSON object")
        return data

    def _api(self, method: str, p: list[str], q: dict):
        if method == "GET" and p == ["status"]:
            return self._json(status())
        if method == "GET" and p == ["samples"]:
            return self._json(samples())
        if method == "GET" and p == ["proven"]:
            return self._json(core.lean_facts())
        if method == "POST" and p == ["key"]:
            key = str(self._json_body().get("key", "")).strip()
            if not re.fullmatch(r"[\w\-]{20,300}", key):
                raise ApiError(400, "bad_key", "That does not look like an Anthropic API key.")
            os.environ["ANTHROPIC_API_KEY"] = key
            return self._json(status())
        if method == "POST" and p == ["session"]:
            name = self._json_body().get("sample")
            if name not in core.SAMPLE_FILES or not (core.SAMPLES_DIR / name).exists():
                raise ApiError(404, "no_sample", "Unknown sample workbook.")
            return self._json(session_json(new_session(core.SAMPLES_DIR / name, name)))
        if method == "POST" and p == ["upload"]:
            data = self._body()
            name = Path((q.get("name") or ["upload.xlsx"])[0]).name
            if not name.lower().endswith(".xlsx") or data[:2] != b"PK":
                raise ApiError(400, "not_xlsx", "Please upload an .xlsx workbook.")
            tmp = _ROOT / f"upload_{uuid.uuid4().hex[:8]}.xlsx"
            tmp.write_bytes(data)
            try:
                return self._json(session_json(new_session(tmp, name)))
            finally:
                tmp.unlink(missing_ok=True)
        if p[:1] == ["session"] and len(p) >= 2:
            s = get_session(p[1])
            tail = p[2:]
            if method == "GET" and not tail:
                return self._json(session_json(s))
            if method == "POST" and tail == ["fixes"]:
                return self._json(find_fixes(s))
            if method == "POST" and tail == ["apply"]:
                return self._json(apply(s, self._json_body()))
            if method == "GET" and tail == ["download"]:
                if not s.version:
                    raise ApiError(409, "no_fix", "No fix has been applied yet.")
                stem = Path(s.name).stem
                return self._send(200, s.cur.read_bytes(), XLSX, {
                    "Content-Disposition": f'attachment; filename="{stem}-fixed.xlsx"'})
        raise ApiError(404, "not_found", "Unknown endpoint")


def make_server(port: int = 8501, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    core.setup_cache_env()
    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.server")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--offline", action="store_true",
                    help="never call the model; use only the bundled samples' caches")
    args = ap.parse_args(argv)
    if args.offline:
        os.environ["PROOF_OFFLINE"] = "1"
    srv = make_server(args.port, args.host)
    url = f"http://{args.host}:{args.port}"
    print(f"Invariant is running at {url}  (Ctrl+C to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
