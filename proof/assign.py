"""RuleSpec + Ledger + CellGraph -> CheckInput (owner: A2).

Assignment encodes what each line SHOULD contain (every txn goes to every
inflow/outflow line whose rule matches, so overlaps give len > 1 and gaps give
len == 0); the CellGraph values are what each line ACTUALLY contains.
Sheet outflow rows (and the subtotal) are positive; they are negated here.
"""

from __future__ import annotations

import re
from functools import lru_cache

from proof.schemas import (
    CellGraph, CheckInput, CheckLine, CheckTxn, Ledger, Rule, RuleSpec, Transaction,
)

_NEGATED = {"outflow", "subtotal"}


@lru_cache(maxsize=None)
def _pattern(p: str) -> re.Pattern[str]:
    """Excel wildcard (`*`, `?`, `~` escape), case-insensitive, whole string."""
    out, i = [], 0
    while i < len(p):
        ch = p[i]
        if ch == "~" and i + 1 < len(p):
            out.append(re.escape(p[i + 1]))
            i += 1
        elif ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def matches(rule: Rule, t: Transaction) -> bool:
    """Source must match; then any counterparty/category pattern (or no filters)."""
    if t.source not in rule.sources:
        return False
    if not rule.counterparty_in and not rule.category_in:
        return True
    if any(_pattern(p).fullmatch(t.counterparty) for p in rule.counterparty_in):
        return True
    hint = t.category_hint
    return hint is not None and any(
        _pattern(p).fullmatch(hint) for p in rule.category_in
    )


def build_checkinput(spec: RuleSpec, ledger: Ledger, graph: CellGraph) -> CheckInput:
    months = 1 + max((c.month_idx for c in graph.cells if c.month_idx is not None),
                     default=-1)
    by_row: dict[str, dict[int, int]] = {}
    for c in graph.cells:
        if c.month_idx is not None:
            by_row.setdefault(c.row_label, {})[c.month_idx] = c.value_cents

    lines: list[CheckLine] = []
    for sl in spec.lines:
        row = by_row.get(sl.row_label)
        if row is None or any(m not in row for m in range(months)):
            raise ValueError(f"Runway row {sl.row_label!r} is missing month values")
        sign = -1 if sl.kind in _NEGATED else 1
        lines.append(CheckLine(line_id=sl.line_id, kind=sl.kind,
                               values_cents=[sign * row[m] for m in range(months)]))

    flow = [sl for sl in spec.lines if sl.kind in ("inflow", "outflow")]
    for sl in flow:
        if sl.rule is None:
            raise ValueError(f"flow line {sl.line_id!r} has no rule")
    txns = [
        CheckTxn(
            id=t.id, month_idx=t.month_idx, amount_cents=t.amount_cents,
            assigned_line_ids=sorted(
                sl.line_id for sl in flow if matches(sl.rule, t)),
        )
        for t in ledger.transactions
    ]
    return CheckInput(months=months, lines=lines, txns=txns)
