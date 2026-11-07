"""Deterministic baseline RuleSpec, no LLM (owner: A2).

Reads the SUMIFS criteria in each Runway flow row and turns them into a Rule.

Rule semantics (shared with `proof.assign`): a transaction matches a rule iff
its source is in `sources` AND (both filter lists are empty OR its counterparty
matches any `counterparty_in` pattern OR its category_hint matches any
`category_in` pattern). Patterns are Excel-style (case-insensitive, `*`, `?`,
`~` escape). Filters are OR-ed because one row can sum several SUMIFS over
different sources (e.g. Other = Ramp categories + Bank fees). A single SUMIFS
that ANDs a merchant and a category criterion cannot be expressed that way, so
it is rejected with UnsupportedFormula rather than silently loosened.
"""

from __future__ import annotations

import re
from collections import Counter

from proof.schemas import CellGraph, Rule, RuleSpec, SpecLine

SOURCE_BY_SHEET = {"ramp": "ramp", "bank": "bank", "payroll": "payroll"}
# column letter -> role, per export tab (layout fixed in CONTRACTS.md)
COLUMN_ROLES = {
    "ramp": {"A": "id", "B": "date", "C": "counterparty", "D": "amount",
             "E": "category", "F": "memo"},
    "bank": {"A": "id", "B": "date", "C": "counterparty", "D": "amount"},
    "payroll": {"A": "id", "B": "date", "C": "counterparty", "D": "amount",
                "E": "amount"},
}
KIND_BY_LABEL = {
    "starting cash": "start", "revenue": "inflow", "payroll": "outflow",
    "saas": "outflow", "contractors": "outflow", "rent": "outflow",
    "other": "outflow", "total outflows": "subtotal",
    "net cash flow": "net", "ending cash": "end",
}


class UnsupportedFormula(ValueError):
    pass


def split_args(s: str) -> list[str]:
    """Split a function argument string on top-level commas (quote/paren aware)."""
    args, depth, cur, i = [], 0, [], 0
    while i < len(s):
        ch = s[i]
        if ch == '"':
            j = i + 1
            while j < len(s) and not (s[j] == '"' and s[j + 1: j + 2] != '"'):
                j += 2 if s[j] == '"' else 1
            cur.append(s[i: j + 1])
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    args.append("".join(cur).strip())
    return args


def _sumifs_calls(formula: str) -> list[list[str]]:
    calls = []
    for m in re.finditer(r"SUMIFS\(", formula, re.IGNORECASE):
        i, depth, in_str = m.end(), 1, False
        while i < len(formula) and depth:
            ch = formula[i]
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                depth += (ch == "(") - (ch == ")")
            i += 1
        calls.append(split_args(formula[m.end(): i - 1]))
    return calls


_RANGE = re.compile(r"^(?:'([^']+)'|([A-Za-z_][\w.]*))!\$?([A-Z]{1,3})\$?\d*(?::\$?[A-Z]{1,3}\$?\d*)?$")


def _where(rng: str) -> tuple[str, str]:
    m = _RANGE.match(rng)
    if not m:
        raise UnsupportedFormula(f"cannot read SUMIFS range {rng!r}")
    return (m.group(1) or m.group(2)).lower(), m.group(3)


def extract_rule(formula: str) -> Rule | None:
    """Rule for one Runway cell formula, or None if it has no SUMIFS."""
    calls = _sumifs_calls(formula)
    if not calls:
        return None
    sources: list[str] = []
    cps: list[str] = []
    cats: list[str] = []
    for args in calls:
        if len(args) < 3 or len(args) % 2 == 0:
            raise UnsupportedFormula(f"malformed SUMIFS: {args}")
        sheet, _ = _where(args[0])
        if sheet not in SOURCE_BY_SHEET:
            raise UnsupportedFormula(f"SUMIFS over unknown tab {sheet!r}")
        src = SOURCE_BY_SHEET[sheet]
        term_cp: list[str] = []
        term_cat: list[str] = []
        for rng, crit in zip(args[1::2], args[2::2]):
            csheet, col = _where(rng)
            if csheet != sheet:
                raise UnsupportedFormula("SUMIFS criteria range on a different tab")
            role = COLUMN_ROLES[sheet].get(col)
            if role == "date":
                continue  # month selection is the column, not part of the rule
            if role not in ("counterparty", "category"):
                raise UnsupportedFormula(f"criteria on unsupported column {sheet}!{col}")
            lit = re.fullmatch(r'"((?:[^"]|"")*)"', crit)
            if not lit:
                raise UnsupportedFormula(f"non-literal criterion {crit} on {sheet}!{col}")
            text = lit.group(1).replace('""', '"')
            if re.match(r"^(<>|<=|>=|<|>|=)", text):
                raise UnsupportedFormula(f"operator criterion {text!r} not supported")
            (term_cp if role == "counterparty" else term_cat).append(text)
        if term_cp and term_cat:
            raise UnsupportedFormula(
                "SUMIFS combines counterparty and category criteria (AND); "
                "not representable in a Rule")
        if src not in sources:
            sources.append(src)
        cps += [x for x in term_cp if x not in cps]
        cats += [x for x in term_cat if x not in cats]
    return Rule(sources=sources, counterparty_in=cps, category_in=cats)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _rationale(rule: Rule, disagree: int) -> str:
    parts = [f"SUMIFS over {'/'.join(s.capitalize() for s in rule.sources)}"]
    if rule.counterparty_in:
        parts.append("counterparty in " + ", ".join(rule.counterparty_in))
    if rule.category_in:
        parts.append("category in " + ", ".join(rule.category_in))
    s = "; ".join(parts)
    if disagree:
        s += f" (majority rule; {disagree} month(s) differ)"
    return s


def build_rulespec(graph: CellGraph) -> RuleSpec:
    """Baseline RuleSpec from the Runway formulas (confidence 1.0)."""
    labels: list[str] = []
    for c in graph.cells:
        if c.row_label not in labels:
            labels.append(c.row_label)
    lines: list[SpecLine] = []
    for label in labels:
        kind = KIND_BY_LABEL.get(label.lower())
        row = [c for c in graph.cells if c.row_label == label]
        if kind is None:
            if any(c.formula and "SUMIFS" in c.formula.upper() for c in row):
                raise UnsupportedFormula(f"unknown Runway row {label!r} uses SUMIFS")
            raise UnsupportedFormula(f"unknown Runway row {label!r}")
        if kind in ("start", "net", "end"):
            line_id = kind
        else:
            line_id = _slug(label)
        if kind in ("inflow", "outflow"):
            rules = [extract_rule(c.formula) for c in row if c.formula]
            rules = [r for r in rules if r is not None]
            if not rules:
                raise UnsupportedFormula(f"row {label!r} has no SUMIFS formula in any month")
            keyed = Counter(r.model_dump_json() for r in rules)
            best, n = keyed.most_common(1)[0]
            rule = Rule.model_validate_json(best)
            lines.append(SpecLine(line_id=line_id, row_label=label, kind=kind,
                                  rule=rule, confidence=1.0,
                                  rationale=_rationale(rule, len(rules) - n)))
        else:
            lines.append(SpecLine(line_id=line_id, row_label=label, kind=kind,
                                  confidence=1.0,
                                  rationale=f"{kind} row derived from other rows"))
    return RuleSpec(lines=lines)
