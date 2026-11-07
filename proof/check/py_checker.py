"""Pure-Python reference checker: CheckInput -> CheckResult (owner: A3).

Invariants (v = line values, T = txns; all integer cents, exact equality):
  roll_forward:    end[m] = start[m] + sum(inflow[m]) + sum(outflow[m]);  start[m] = end[m-1] for m >= 1
  arithmetic:      subtotal[m] = sum(outflow[m]);  net[m] = sum(inflow[m]) + sum(outflow[m])
  traceability:    v[L][m] = sum(t.amount for t in T if t.month == m and L in t.assigned)  (inflow/outflow L)
  no_double_count: len(t.assigned) <= 1 for every txn t
  coverage:        len(t.assigned) >= 1 for every txn t
"""

from __future__ import annotations

from proof.schemas import CheckInput, CheckResult, Violation

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _mon(m: int) -> str:
    return MONTHS[m] if 0 <= m < 12 else f"month {m}"


def _amt(cents: int) -> str:
    """Absolute value as 1,234.56 (integer math only)."""
    cents = abs(cents)
    return f"{cents // 100:,}.{cents % 100:02d}"


def _diff(actual: int, expected: int, by_magnitude: bool) -> str:
    """'<AMT> more|less' describing actual relative to expected."""
    if by_magnitude:
        word = "more" if abs(actual) > abs(expected) else "less"
    else:
        word = "more" if actual > expected else "less"
    return f"{_amt(actual - expected)} {word}"


def check(inp: CheckInput) -> CheckResult:
    out: list[Violation] = []
    by_kind: dict[str, list] = {}
    for line in inp.lines:
        by_kind.setdefault(line.kind, []).append(line)
    flows = by_kind.get("inflow", []) + by_kind.get("outflow", [])
    outflows = by_kind.get("outflow", [])
    inflows = by_kind.get("inflow", [])

    # roll_forward
    for start in by_kind.get("start", []):
        for end in by_kind.get("end", []):
            for m in range(inp.months):
                exp = start.values_cents[m] + sum(f.values_cents[m] for f in flows)
                act = end.values_cents[m]
                if act != exp:
                    out.append(Violation(
                        invariant="roll_forward", line_id=end.line_id, month_idx=m,
                        expected_cents=exp, actual_cents=act,
                        message=f"{end.line_id} in {_mon(m)} is {_diff(act, exp, False)} "
                                f"than {start.line_id} plus net flows"))
                if m >= 1 and start.values_cents[m] != end.values_cents[m - 1]:
                    exp, act = end.values_cents[m - 1], start.values_cents[m]
                    out.append(Violation(
                        invariant="roll_forward", line_id=start.line_id, month_idx=m,
                        expected_cents=exp, actual_cents=act,
                        message=f"{start.line_id} in {_mon(m)} is {_diff(act, exp, False)} "
                                f"than end of {_mon(m - 1)}"))

    # arithmetic
    for kind, parts, what in (("subtotal", outflows, "outflow lines"),
                              ("net", inflows + outflows, "inflow and outflow lines")):
        for line in by_kind.get(kind, []):
            for m in range(inp.months):
                exp = sum(p.values_cents[m] for p in parts)
                act = line.values_cents[m]
                if act != exp:
                    out.append(Violation(
                        invariant="arithmetic", line_id=line.line_id, month_idx=m,
                        expected_cents=exp, actual_cents=act,
                        message=f"{line.line_id} in {_mon(m)} is {_diff(act, exp, False)} "
                                f"than the sum of its {what}"))

    # traceability
    for line in flows:
        for m in range(inp.months):
            matched = sorted(
                (t for t in inp.txns if t.month_idx == m and line.line_id in t.assigned_line_ids),
                key=lambda t: t.id)
            exp = sum(t.amount_cents for t in matched)
            act = line.values_cents[m]
            if act != exp:
                out.append(Violation(
                    invariant="traceability", line_id=line.line_id, month_idx=m,
                    txn_ids=[t.id for t in matched], expected_cents=exp, actual_cents=act,
                    message=f"{line.line_id} in {_mon(m)} is {_diff(act, exp, True)} "
                            f"than its matched transactions"))

    # no_double_count and coverage (per txn, ascending id)
    for t in sorted(inp.txns, key=lambda t: t.id):
        n = len(t.assigned_line_ids)
        if n > 1:
            lines = ", ".join(sorted(t.assigned_line_ids))
            out.append(Violation(
                invariant="no_double_count", month_idx=t.month_idx, txn_ids=[t.id],
                message=f"transaction {t.id} ({_amt(t.amount_cents)}) in {_mon(t.month_idx)} "
                        f"is assigned to {n} lines: {lines}"))
        elif n == 0:
            out.append(Violation(
                invariant="coverage", month_idx=t.month_idx, txn_ids=[t.id],
                message=f"transaction {t.id} ({_amt(t.amount_cents)}) in {_mon(t.month_idx)} "
                        f"is not assigned to any line"))

    return CheckResult(ok=not out, violations=out)
