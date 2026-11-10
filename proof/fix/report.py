"""Data shapes for the fix loop (owner: A7). Not part of the A0 contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from proof.schemas import Violation


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proposal(_M):
    """One proposed change to a workbook copy.

    kind "edit_formula": write `new_formula` into `cell_ref` (a Runway month
    cell). scope "row" also writes the same formula, shifted column by column,
    into the other month cells of that row that currently hold a formula.
    kind "delete_duplicate_row": delete `row` of export tab `sheet`; only
    allowed when that row repeats an earlier row exactly (same id, same data).
    """

    kind: Literal["edit_formula", "delete_duplicate_row"] = "edit_formula"
    scope: Literal["cell", "row"] = "cell"
    cell_ref: str | None = None
    old_formula: str | None = None
    new_formula: str | None = None
    sheet: str | None = None
    row: int | None = None
    reason: str = ""


class Recheck(_M):
    ok: bool                       # fixed copy has no violations at all
    target_cleared: bool
    new_violations: list[Violation]
    remaining: int                 # violations left on the fixed copy
    before: int                    # violations on the copy the fix was applied to


class Attempt(_M):
    proposal: Proposal | None = None
    outcome: Literal["accepted", "rejected", "invalid"]
    detail: str = ""               # why it was rejected / invalid
    recheck: Recheck | None = None


class FixReport(_M):
    violation: Violation
    explanation: str
    context: dict[str, Any]
    # "verified": a fix was applied to a copy, re-run, and the re-check passed.
    status: Literal["verified", "needs_human"]
    fix: Proposal | None = None
    cell_ref: str | None = None    # Runway cell the before/after values refer to
    before_cents: int | None = None
    after_cents: int | None = None
    recheck: Recheck | None = None
    attempts: list[Attempt] = []
    resolved_by: int | None = None  # index of the report whose fix also cleared this one
    note: str = ""


class FixRun(_M):
    source: str
    initial_ok: bool
    final_ok: bool
    fixed_path: str | None
    final_violations: list[Violation]
    reports: list[FixReport]
    llm_calls: int = 0
