"""LLM agent: CellGraph -> RuleSpec (owner: A5).

The model reads each Runway row (label + distinct formulas across months), the
export tabs' headers, row notes and a small ledger sample/vocabulary, and says
what each row SHOULD contain: a `kind` and, for inflow/outflow rows, a `Rule`
in the semantics of `proof.spec.rules`. The model never computes or compares
numbers; the checker does all arithmetic.

What the model does NOT control (done deterministically here):
  * `line_id` (same convention as the baseline, so checker messages are stable);
  * which rows exist (exactly the row labels in the graph, in order);
  * the cap on confidence when it disagrees with the baseline parser.

Config (env): PROOF_MODEL, PROOF_CONFIDENCE_MIN (default 0.8), PROOF_CACHE_DIR
(default `.proof_cache`; responses are cached on disk by input hash so a demo
runs offline and deterministically).

`build_rulespec_llm(...)` accepts any object with `client.messages.create(...)`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from proof.assign import matches
from proof.schemas import CellGraph, Ledger, Rule, RuleSpec, SpecLine
from proof.spec.rules import UnsupportedFormula, _slug, build_rulespec

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_CONFIDENCE_MIN = 0.8
SAMPLE_PER_SOURCE = 10  # 3 sources -> ~30 sample rows
VOCAB_CAP = 200
TOOL_NAME = "submit_rulespec"
# Confidence ceiling for a row where the model's rule differs from what the
# deterministic baseline read out of the formulas.
DISAGREE_CAP = 0.5
MAX_ATTEMPTS = 2  # first try + one retry with the validation error fed back

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #

_REL_REF = re.compile(r"(?<![!\w$':])(\$?)([A-Z]{1,3})(\$?)(\d+)")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _cell_col(ref: str) -> int | None:
    m = re.search(r"!\$?([A-Z]{1,3})\$?\d+$", ref)
    return _col_index(m.group(1)) if m else None


def normalize_formula(formula: str, ref: str) -> str:
    """Rewrite same-sheet relative column refs as offsets from the cell's column.

    `=SUMIFS(..., ">="&B$1)` in B5 and `...C$1` in C5 normalise to the same text,
    so a row with 12 identical-but-shifted formulas shows up as one variant.
    """
    here = _cell_col(ref)
    if here is None:
        return formula

    def sub(m: re.Match[str]) -> str:
        abs_col, letters, abs_row, row = m.groups()
        if abs_col:
            return m.group(0)
        off = _col_index(letters) - here
        return f"[col{off:+d}]{abs_row}{row}"

    return _REL_REF.sub(sub, formula)


def summarize_graph(graph: CellGraph) -> list[dict[str, Any]]:
    """One entry per row label (graph order): distinct formulas with their months."""
    rows: dict[str, dict[str, list[int | None]]] = {}
    for c in graph.cells:
        variants = rows.setdefault(c.row_label, {})
        key = "(hardcoded)" if c.formula is None else normalize_formula(c.formula, c.ref)
        variants.setdefault(key, []).append(c.month_idx)
    out = []
    for label, variants in rows.items():
        ordered = sorted(variants.items(), key=lambda kv: -len(kv[1]))
        out.append({
            "row_label": label,
            "variants": [{"formula": f, "months": ms} for f, ms in ordered],
        })
    return out


def ledger_context(ledger: Ledger) -> dict[str, Any]:
    """~30 sample rows (evenly spaced per source) + distinct counterparties/categories.

    Deterministic: sorted by id, no randomness, so the cache key is stable.
    """
    by_src: dict[str, list] = {}
    for t in sorted(ledger.transactions, key=lambda t: t.id):
        by_src.setdefault(t.source, []).append(t)
    sample, vocab = [], {}
    for src, txns in by_src.items():
        k = min(SAMPLE_PER_SOURCE, len(txns))
        idx = sorted({round(i * (len(txns) - 1) / max(k - 1, 1)) for i in range(k)})
        sample += [{"id": txns[i].id, "source": src, "date": txns[i].date,
                    "amount_cents": txns[i].amount_cents,
                    "counterparty": txns[i].counterparty,
                    "category": txns[i].category_hint, "memo": txns[i].memo}
                   for i in idx]
        vocab[src] = {
            "counterparties": sorted({t.counterparty for t in txns})[:VOCAB_CAP],
            "categories": sorted({t.category_hint for t in txns if t.category_hint})}
    return {"sample_rows": sample, "distinct_values": vocab}


def build_user_message(graph: CellGraph, *, ledger: Ledger | None = None,
                       headers: dict[str, list[str]] | None = None,
                       notes: dict[str, str] | None = None) -> str:
    parts = ["Runway rows:\n" + json.dumps(summarize_graph(graph), indent=1)]
    if notes:
        parts.append("Row notes:\n" + json.dumps(notes, indent=1))
    if headers:
        # column letter -> header, so formulas like Ramp!$E:$E can be read
        tabs = {tab: {chr(65 + i): h for i, h in enumerate(hs)}
                for tab, hs in headers.items()}
        parts.append("Export tabs (column letter: header):\n" + json.dumps(tabs, indent=1))
    if ledger is not None:
        parts.append("Ledger context (a sample, not the full ledger):\n"
                     + json.dumps(ledger_context(ledger), indent=1))
    parts.append("Return one entry per Runway row via the tool.")
    return "\n\n".join(parts)


def export_headers(path: str | Path) -> dict[str, list[str]]:
    """Row-1 headers of every tab except Runway."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    return {ws.title: [str(c) for c in next(ws.iter_rows(max_row=1, values_only=True))
                       if c is not None]
            for ws in wb.worksheets if ws.title != "Runway"}


def read_row_notes(path: str | Path) -> dict[str, str]:
    """Text beside Runway rows (cells right of the month columns), by row label."""
    from openpyxl import load_workbook
    ws = load_workbook(path)["Runway"]
    month_cols = [c for c in range(2, ws.max_column + 1)
                  if hasattr(ws.cell(1, c).value, "month")] or list(range(2, 14))
    out = {}
    for r in range(2, ws.max_row + 1):
        label = ws.cell(r, 1).value
        extra = [str(ws.cell(r, c).value) for c in range(max(month_cols) + 1, ws.max_column + 1)
                 if ws.cell(r, c).value not in (None, "")]
        if label and extra:
            out[str(label).strip()] = " ".join(extra)
    return out


def tool_schema() -> dict[str, Any]:
    rule = Rule.model_json_schema()
    return {
        "name": TOOL_NAME,
        "description": "Submit the RuleSpec: one entry per Runway row.",
        "input_schema": {
            "type": "object",
            "required": ["rows"],
            "additionalProperties": False,
            "properties": {"rows": {"type": "array", "items": {
                "type": "object",
                "required": ["row_label", "kind", "rule", "confidence", "rationale"],
                "additionalProperties": False,
                "properties": {
                    "row_label": {"type": "string"},
                    "kind": {"enum": ["start", "inflow", "outflow", "subtotal", "net", "end"]},
                    "rule": {"anyOf": [{k: v for k, v in rule.items() if k != "title"},
                                       {"type": "null"}]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            }}},
        },
    }


# --------------------------------------------------------------------------- #
# Response handling
# --------------------------------------------------------------------------- #

class AgentError(ValueError):
    """The model's answer could not be turned into a valid RuleSpec."""


def _tool_input(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return block.input
    raise AgentError(f"model did not call {TOOL_NAME}")


def _line_id(label: str, kind: str) -> str:
    return kind if kind in ("start", "net", "end") else _slug(label)


def rulespec_from_tool_input(data: dict[str, Any], graph: CellGraph) -> RuleSpec:
    """Validate the model's rows against the graph and build the RuleSpec."""
    expected = list(dict.fromkeys(c.row_label for c in graph.cells))
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise AgentError("`rows` must be a list")
    got = [r.get("row_label") for r in rows if isinstance(r, dict)]
    if sorted(got, key=str) != sorted(expected):
        missing = [x for x in expected if x not in got]
        extra = [x for x in got if x not in expected]
        dupes = sorted({x for x in got if got.count(x) > 1}, key=str)
        raise AgentError(f"row labels must match the sheet exactly "
                         f"(missing={missing}, unknown={extra}, duplicated={dupes})")
    by_label = {r["row_label"]: r for r in rows}
    lines: list[SpecLine] = []
    for label in expected:
        r = by_label[label]
        kind = r.get("kind")
        try:
            conf = float(r.get("confidence"))
        except (TypeError, ValueError):
            raise AgentError(f"{label!r}: confidence must be a number") from None
        if not 0.0 <= conf <= 1.0:
            raise AgentError(f"{label!r}: confidence {conf} outside [0, 1]")
        rule = r.get("rule")
        if kind in ("inflow", "outflow"):
            if rule is None:
                raise AgentError(f"{label!r}: {kind} row needs a rule")
        elif rule is not None:
            raise AgentError(f"{label!r}: rule must be null for kind {kind!r}")
        try:
            lines.append(SpecLine(
                line_id=_line_id(label, kind), row_label=label, kind=kind,
                rule=None if rule is None else Rule.model_validate(rule),
                confidence=conf, rationale=str(r.get("rationale", ""))))
        except ValidationError as e:
            raise AgentError(f"{label!r}: {e.errors()[0]['msg']}") from None
    ids = [ln.line_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise AgentError(f"duplicate line ids {sorted({i for i in ids if ids.count(i) > 1})}")
    for kind in ("start", "end"):
        n = sum(ln.kind == kind for ln in lines)
        if n != 1:
            raise AgentError(f"need exactly one {kind!r} row, got {n}")
    return RuleSpec(lines=lines)


def _canon(rule: Rule) -> tuple:
    return (tuple(sorted(rule.sources)),
            tuple(sorted(p.lower() for p in rule.counterparty_in)),
            tuple(sorted(p.lower() for p in rule.category_in)))


def reconcile_with_baseline(spec: RuleSpec, graph: CellGraph) -> RuleSpec:
    """Cap confidence on rows where the LLM disagrees with the formula parser.

    Rows the baseline cannot parse (UnsupportedFormula) are left to the model.
    """
    try:
        base = {ln.row_label: ln for ln in build_rulespec(graph).lines}
    except UnsupportedFormula:
        # The baseline gives up on the whole sheet at the first bad row; compare
        # per row by building it on single-row sub-graphs instead.
        base = {}
        for label in dict.fromkeys(c.row_label for c in graph.cells):
            sub = CellGraph(cells=[c for c in graph.cells if c.row_label == label])
            try:
                base[label] = build_rulespec(sub).lines[0]
            except UnsupportedFormula:
                pass
    out: list[SpecLine] = []
    for ln in spec.lines:
        b = base.get(ln.row_label)
        differs = b is not None and (
            b.kind != ln.kind
            or (ln.rule is not None and b.rule is not None
                and _canon(ln.rule) != _canon(b.rule)))
        if differs:
            note = (f" [LLM and formula parser disagree: parser read "
                    f"kind={b.kind}, rule={b.rule.model_dump() if b.rule else None}]")
            ln = ln.model_copy(update={
                "confidence": min(ln.confidence, DISAGREE_CAP),
                "rationale": ln.rationale + note})
        out.append(ln)
    return RuleSpec(lines=out)


# --------------------------------------------------------------------------- #
# Review flags and accuracy
# --------------------------------------------------------------------------- #

def confidence_min() -> float:
    return float(os.environ.get("PROOF_CONFIDENCE_MIN", DEFAULT_CONFIDENCE_MIN))


def needs_review(spec: RuleSpec, threshold: float | None = None) -> list[str]:
    """line_ids whose confidence is below PROOF_CONFIDENCE_MIN: surface, don't trust."""
    t = confidence_min() if threshold is None else threshold
    return [ln.line_id for ln in spec.lines if ln.confidence < t]


def _assignment(spec: RuleSpec, ledger: Ledger) -> dict[str, frozenset[str]]:
    return {ln.line_id: frozenset(t.id for t in ledger.transactions if matches(ln.rule, t))
            for ln in spec.lines if ln.rule is not None}


def spec_accuracy(pred: RuleSpec, gold: RuleSpec, ledger: Ledger,
                  gold_ledger: Ledger | None = None) -> dict[str, Any]:
    """Per-line exact match on kind + matched-transaction set.

    Lines are paired by content, not by id, so a renamed row still counts: a
    predicted line is correct for a gold line iff same kind and, for flow lines,
    the same set of matched txn ids. `gold_ledger` is for variants whose ledger
    columns were renamed (txn ids must be the same). `assignment_equal` is the
    A5 acceptance criterion (every gold line matched, no extra flow lines).
    """
    gl = gold_ledger or ledger
    pa, ga = _assignment(pred, ledger), _assignment(gold, gl)
    unused = list(pred.lines)
    per_line: dict[str, bool] = {}
    for g in gold.lines:
        hit = next((p for p in unused if p.kind == g.kind and (
            g.rule is None or pa[p.line_id] == ga[g.line_id])), None)
        per_line[g.line_id] = hit is not None
        if hit is not None:
            unused.remove(hit)
    correct = sum(per_line.values())
    extra = [p.line_id for p in unused]
    return {"lines": len(gold.lines), "correct": correct,
            "accuracy": correct / len(gold.lines) if gold.lines else 1.0,
            "per_line": per_line, "extra_pred_lines": extra,
            "assignment_equal": correct == len(gold.lines) and not extra}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def default_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AgentError("ANTHROPIC_API_KEY is not set (and no cached response for this input)")
    import anthropic
    return anthropic.Anthropic()


def _cache_path(key_material: str, cache_dir: str | Path | None) -> Path:
    d = Path(cache_dir or os.environ.get("PROOF_CACHE_DIR", ".proof_cache"))
    return d / (hashlib.sha256(key_material.encode()).hexdigest() + ".json")


def build_rulespec_llm(graph: CellGraph, ledger: Ledger | None = None, *,
                       headers: dict[str, list[str]] | None = None,
                       notes: dict[str, str] | None = None,
                       client: Any | None = None, model: str | None = None,
                       cache_dir: str | Path | None = None,
                       use_cache: bool = True) -> RuleSpec:
    """CellGraph (+ ledger context) -> RuleSpec via the LLM.

    Validated, reconciled with the baseline, and cached on disk by input hash.
    A cache hit needs neither a client nor an API key.
    """
    model = model or os.environ.get("PROOF_MODEL", DEFAULT_MODEL)
    user = build_user_message(graph, ledger=ledger, headers=headers, notes=notes)
    key = json.dumps([model, SYSTEM_PROMPT, tool_schema(), user], sort_keys=True)
    path = _cache_path(key, cache_dir)
    if use_cache and path.exists():
        try:
            return reconcile_with_baseline(
                rulespec_from_tool_input(json.loads(path.read_text()), graph), graph)
        except (AgentError, ValueError):
            pass  # corrupt or stale entry: fall through and ask again
    client = client or default_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    last: AgentError | None = None
    for _ in range(MAX_ATTEMPTS):
        response = client.messages.create(
            model=model, max_tokens=4096, system=SYSTEM_PROMPT, messages=messages,
            tools=[tool_schema()], tool_choice={"type": "tool", "name": TOOL_NAME})
        try:
            data = _tool_input(response)
            spec = rulespec_from_tool_input(data, graph)
        except AgentError as e:
            last = e
            tool_id = next((b.id for b in response.content
                            if getattr(b, "type", None) == "tool_use"), None)
            fix = f"Invalid: {e}. Call {TOOL_NAME} again with a corrected answer."
            if tool_id is None:  # no tool call to answer, so just nudge
                messages = messages + [{"role": "user", "content": fix}]
            else:
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": tool_id,
                        "is_error": True, "content": fix}]}]
            continue
        if use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, sort_keys=True, indent=1))
        return reconcile_with_baseline(spec, graph)
    raise AgentError(f"model output invalid after {MAX_ATTEMPTS} attempts: {last}")
