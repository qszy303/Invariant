"""Pydantic v2 models for every JSON contract in CONTRACTS.md (owner: A0).

All money is integer cents. Sign convention in Ledger and CheckInput:
positive = money in, negative = money out.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

Source = Literal["ramp", "bank", "payroll"]
LineKind = Literal["start", "inflow", "outflow", "subtotal", "net", "end"]
InvariantName = Literal[
    "roll_forward", "traceability", "no_double_count", "coverage", "arithmetic"
]

_RULELESS_KINDS = {"start", "subtotal", "net", "end"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transaction(_Model):
    id: str
    source: Source
    date: str
    month_idx: StrictInt
    amount_cents: StrictInt
    counterparty: str
    memo: str = ""
    category_hint: str | None = None


class Ledger(_Model):
    transactions: list[Transaction]


class Cell(_Model):
    ref: str
    sheet: str
    row_label: str
    # None for cells that are not tied to a month column.
    month_idx: StrictInt | None = None
    value_cents: StrictInt
    # None for hardcoded inputs.
    formula: str | None = None
    deps: list[str] = []


class CellGraph(_Model):
    cells: list[Cell]


class Rule(_Model):
    sources: list[Source] = []
    counterparty_in: list[str] = []
    category_in: list[str] = []


class SpecLine(_Model):
    line_id: str
    row_label: str
    kind: LineKind
    rule: Rule | None = None
    confidence: float
    rationale: str = ""

    @model_validator(mode="after")
    def _rule_matches_kind(self) -> SpecLine:
        if self.kind in _RULELESS_KINDS and self.rule is not None:
            raise ValueError(f"rule must be null for kind {self.kind!r}")
        return self


class RuleSpec(_Model):
    lines: list[SpecLine]


class CheckLine(_Model):
    line_id: str
    kind: LineKind
    # Outflow lines are negative (converted from the sheet).
    values_cents: list[StrictInt]


class CheckTxn(_Model):
    id: str
    month_idx: StrictInt
    amount_cents: StrictInt
    assigned_line_ids: list[str]


class CheckInput(_Model):
    months: StrictInt
    lines: list[CheckLine]
    txns: list[CheckTxn]

    @model_validator(mode="after")
    def _values_cover_months(self) -> CheckInput:
        for line in self.lines:
            if len(line.values_cents) != self.months:
                raise ValueError(
                    f"line {line.line_id!r} has {len(line.values_cents)} values, "
                    f"expected {self.months}"
                )
        return self


class Violation(_Model):
    invariant: InvariantName
    line_id: str | None = None
    month_idx: StrictInt | None = None
    txn_ids: list[str] = []
    expected_cents: StrictInt | None = None
    actual_cents: StrictInt | None = None
    message: str


class CheckResult(_Model):
    ok: bool
    violations: list[Violation]

    def to_canonical_json(self) -> str:
        """Serialization both checkers must emit byte-for-byte.

        Violations are sorted by (invariant, line_id or "", month_idx or -1).
        Keys are sorted and there is no whitespace. The order of txn_ids within
        a violation is preserved, so checkers must emit it deterministically
        (ascending by txn id).
        """
        ordered = sorted(
            self.violations,
            key=lambda v: (
                v.invariant,
                v.line_id or "",
                v.month_idx if v.month_idx is not None else -1,
            ),
        )
        payload = {
            "ok": self.ok,
            "violations": [v.model_dump(mode="json") for v in ordered],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
