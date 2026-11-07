"""Raw export tabs (Ramp, Bank, Payroll) -> Ledger (owner: A2).

Sign convention: positive = money in. Ramp/Bank amounts are taken as exported
(spend negative, refunds positive); a Payroll row becomes one negative
transaction of gross + employer taxes.

Duplicates: a row whose id was already seen with identical content (a pasted
duplicate row) is kept once, with a warning. The Ledger is the ground truth of
what happened, so a sheet-only duplicate must not be re-counted here; it then
surfaces as a traceability violation. Same id with different content is an
error.
"""

from __future__ import annotations

import warnings
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from proof.ingest.sheet import to_cents
from proof.schemas import Ledger, Transaction


def _iso(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    return date.fromisoformat(s[:10]).isoformat()


def _rows(ws, headers: list[str]):
    got = [c.value for c in ws[1]][: len(headers)]
    if got != headers:
        raise ValueError(f"{ws.title}: expected header {headers}, found {got}")
    for row in ws.iter_rows(min_row=2, max_col=len(headers), values_only=True):
        if row[0] is None:
            continue
        yield dict(zip(headers, row))


def _txn(id_, source, d, cents, who, memo, hint) -> Transaction:
    iso = _iso(d)
    return Transaction(
        id=str(id_), source=source, date=iso, month_idx=int(iso[5:7]) - 1,
        amount_cents=cents, counterparty=str(who or ""), memo=str(memo or ""),
        category_hint=hint,
    )


def read_ledger(path: str | Path) -> Ledger:
    wb = load_workbook(path, data_only=True)
    out: list[Transaction] = []
    seen: dict[str, Transaction] = {}

    def add(t: Transaction) -> None:
        prev = seen.get(t.id)
        if prev is None:
            seen[t.id] = t
            out.append(t)
        elif prev == t:
            warnings.warn(f"duplicate row for {t.id} ignored in ledger", stacklevel=2)
        else:
            raise ValueError(f"{t.id}: appears twice with different contents")

    for r in _rows(wb["Ramp"], ["id", "date", "merchant", "amount", "category", "memo"]):
        add(_txn(r["id"], "ramp", r["date"], to_cents(r["amount"]), r["merchant"],
                 r["memo"], r["category"]))
    for r in _rows(wb["Bank"], ["id", "date", "description", "amount"]):
        add(_txn(r["id"], "bank", r["date"], to_cents(r["amount"]),
                 r["description"], "", None))
    for r in _rows(wb["Payroll"], ["id", "pay_date", "employee", "gross", "employer_taxes"]):
        cents = -(to_cents(r["gross"]) + to_cents(r["employer_taxes"]))
        add(_txn(r["id"], "payroll", r["pay_date"], cents, r["employee"],
                 "gross + employer taxes", None))
    return Ledger(transactions=out)
