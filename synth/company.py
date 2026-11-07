"""Deterministic synthetic company "Slope" (owner: A1).

Generates 12 months (Jan-Dec 2026) of Ramp, Bank and Payroll rows from a seed.
Amounts are integer cents; the Ledger uses the contract sign convention
(positive = in, negative = out).
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field
from datetime import date

from proof.schemas import Ledger, Transaction

YEAR = 2026
MONTHS = 12
MONTH_NAMES = [calendar.month_abbr[m] for m in range(1, 13)]

# Runway outflow lines in sheet order, with Ramp categories feeding each.
OTHER_RAMP_CATEGORIES = ["Travel", "Meals", "Office", "Marketing", "Legal"]
STARTING_CASH_CENTS = 240_000_000 + 0  # $2.4M

# (vendor, monthly cents or None for annual, annual cents, variable)
SAAS_MONTHLY = [
    ("Figma", 4500 * 3), ("Notion", 1600 * 4), ("GitHub", 2100 * 4),
    ("Slack", 8750 * 1), ("Linear", 800 * 8), ("Zoom", 1599 * 2),
    ("Google Workspace", 1200 * 9), ("Datadog", 31500), ("Sentry", 8900),
    ("Vercel", 4000), ("HubSpot", 80000), ("Intercom", 39000),
    ("Segment", 12000), ("OpenAI", 54000), ("1Password", 4800),
]
SAAS_ANNUAL = [  # (vendor, cents, month_idx)
    ("Loom", 9600, 1), ("Canva", 12000, 3), ("Mixpanel", 108000, 5),
    ("Zapier", 59988, 8),
]
LATE_VENDOR = "Retool"  # first appears in month 7 (Aug)
LATE_VENDOR_FIRST_MONTH = 7
CONTRACTORS = ["Upwork", "Toptal", "Fiverr Pro"]


@dataclass
class RampRow:
    id: str
    date: date
    merchant: str
    amount_cents: int  # negative = spend, positive = refund
    category: str
    memo: str


@dataclass
class BankRow:
    id: str
    date: date
    description: str
    amount_cents: int


@dataclass
class PayrollRow:
    id: str
    pay_date: date
    employee: str
    gross_cents: int
    employer_taxes_cents: int


@dataclass
class Company:
    seed: int
    ramp: list[RampRow] = field(default_factory=list)
    bank: list[BankRow] = field(default_factory=list)
    payroll: list[PayrollRow] = field(default_factory=list)

    # ---- ledger -------------------------------------------------------
    def ledger(self) -> Ledger:
        txns: list[Transaction] = []
        for r in self.ramp:
            txns.append(Transaction(
                id=r.id, source="ramp", date=r.date.isoformat(),
                month_idx=r.date.month - 1, amount_cents=r.amount_cents,
                counterparty=r.merchant, memo=r.memo, category_hint=r.category))
        for b in self.bank:
            txns.append(Transaction(
                id=b.id, source="bank", date=b.date.isoformat(),
                month_idx=b.date.month - 1, amount_cents=b.amount_cents,
                counterparty=b.description, memo="",
                category_hint=bank_line(b)))
        for p in self.payroll:
            txns.append(Transaction(
                id=p.id, source="payroll", date=p.pay_date.isoformat(),
                month_idx=p.pay_date.month - 1,
                amount_cents=-(p.gross_cents + p.employer_taxes_cents),
                counterparty=p.employee, memo="gross + employer taxes",
                category_hint="Payroll"))
        return Ledger(transactions=txns)

    # ---- independent expected values ----------------------------------
    def expected_runway(self) -> dict[str, list[int]]:
        """Runway rows in cents using sheet sign (outflows positive).

        Computed straight from the raw rows, without touching the workbook.
        """
        z = lambda: [0] * MONTHS  # noqa: E731
        rows = {k: z() for k in
                ["Revenue", "Payroll", "SaaS", "Contractors", "Rent", "Other"]}
        for r in self.ramp:
            m = r.date.month - 1
            rows[ramp_line(r)][m] += -r.amount_cents
        for b in self.bank:
            m = b.date.month - 1
            line = bank_line(b)
            rows[line][m] += b.amount_cents if line == "Revenue" else -b.amount_cents
        for p in self.payroll:
            rows["Payroll"][p.pay_date.month - 1] += (
                p.gross_cents + p.employer_taxes_cents)
        out = z()
        net = z()
        start = z()
        end = z()
        for m in range(MONTHS):
            out[m] = sum(rows[k][m] for k in
                         ["Payroll", "SaaS", "Contractors", "Rent", "Other"])
            net[m] = rows["Revenue"][m] - out[m]
            start[m] = STARTING_CASH_CENTS if m == 0 else end[m - 1]
            end[m] = start[m] + net[m]
        rows.update({"Starting Cash": start, "Total Outflows": out,
                     "Net Cash Flow": net, "Ending Cash": end})
        return rows


def ramp_line(r: RampRow) -> str:
    if r.category == "Software":
        return "SaaS"
    if r.category == "Contractors":
        return "Contractors"
    return "Other"


def bank_line(b: BankRow) -> str:
    if b.description.startswith("Stripe"):
        return "Revenue"
    if b.description.startswith("Rent"):
        return "Rent"
    return "Other"


def _d(month_idx: int, day: int) -> date:
    last = calendar.monthrange(YEAR, month_idx + 1)[1]
    return date(YEAR, month_idx + 1, min(day, last))


def generate(seed: int = 7) -> Company:
    rng = random.Random(seed)
    c = Company(seed=seed)

    # ---- employees: 8 at start-ish, 2 hires, 1 departure ----
    # (name, annual salary dollars, start month, end month inclusive)
    emps = [
        ("Ava Chen", 190000, 0, 11), ("Ben Okafor", 175000, 0, 11),
        ("Cara Diaz", 160000, 0, 11), ("Dev Patel", 150000, 0, 11),
        ("Elena Rossi", 140000, 0, 11), ("Farid Nasser", 135000, 0, 5),
        ("Grace Kim", 125000, 0, 11), ("Hugo Martin", 120000, 0, 11),
        ("Iris Wong", 145000, 4, 11), ("Jon Baker", 130000, 8, 11),
    ]
    pid = 0
    for m in range(MONTHS):
        for day in (15, 31):
            for name, sal, s, e in emps:
                if not (s <= m <= e):
                    continue
                pid += 1
                gross = round(sal * 1.15 * 100 / 24)
                taxes = round(gross * 0.0765 + 3500 * 0.5)
                c.payroll.append(PayrollRow(
                    f"payroll-{pid:04d}", _d(m, day), name, gross, taxes))

    # ---- ramp ----
    rid = 0

    def add_ramp(d, merchant, cents, category, memo=""):
        nonlocal rid
        rid += 1
        c.ramp.append(RampRow(f"ramp-{rid:04d}", d, merchant, cents,
                              category, memo))

    for m in range(MONTHS):
        for i, (v, cents) in enumerate(SAAS_MONTHLY):
            amt = cents
            memo = "monthly subscription"
            add_ramp(_d(m, 1 + (i * 2) % 27), v, -amt, "Software", memo)
        # AWS usage grows and varies
        aws = round((18000 + 1500 * m) * rng.uniform(0.9, 1.15))
        add_ramp(_d(m, 3), "AWS", -aws * 100 // 100, "Software", "usage-based")
        if m >= LATE_VENDOR_FIRST_MONTH:
            add_ramp(_d(m, 12), LATE_VENDOR, -25000, "Software",
                     "monthly subscription")
        for v, cents, mi in SAAS_ANNUAL:
            if mi == m:
                add_ramp(_d(m, 9), v, -cents, "Software", "annual plan")
        # contractors, irregular
        for v in CONTRACTORS:
            if rng.random() < 0.55:
                add_ramp(_d(m, rng.randint(5, 28)), v,
                         -rng.randrange(400_000, 1_400_000, 100),
                         "Contractors", "bill-pay invoice")
        # other opex
        add_ramp(_d(m, 10), "WeWork Supplies", -rng.randrange(8000, 30000, 100),
                 "Office", "")
        add_ramp(_d(m, 14), "Google Ads", -rng.randrange(1_200_000, 2_200_000, 100),
                 "Marketing", "")
        add_ramp(_d(m, 20), "DoorDash", -rng.randrange(20000, 60000, 100),
                 "Meals", "team lunch")
        if rng.random() < 0.6:
            add_ramp(_d(m, 22), "Delta", -rng.randrange(30000, 120000, 100),
                     "Travel", "")
        if m in (2, 9):
            add_ramp(_d(m, 25), "Cooley LLP", -rng.randrange(400000, 700000, 100),
                     "Legal", "")
    # refunds (positive Ramp amounts)
    add_ramp(_d(3, 18), "Figma", 13500, "Software", "refund: seat downgrade")
    add_ramp(_d(6, 20), "Delta", 42000, "Travel", "refund: cancelled flight")
    add_ramp(_d(9, 6), "Intercom", 39000, "Software", "refund: duplicate charge")
    c.ramp.sort(key=lambda r: (r.date, r.id))
    # renumber ids chronologically
    for i, r in enumerate(c.ramp, 1):
        r.id = f"ramp-{i:04d}"

    # ---- bank ----
    bid = 0

    def add_bank(d, desc, cents):
        nonlocal bid
        bid += 1
        c.bank.append(BankRow(f"bank-{bid:04d}", d, desc, cents))

    payout = 1_500_000
    for m in range(MONTHS):
        total = round(payout * rng.uniform(0.97, 1.03))
        # two payouts per month
        first = total // 2 // 100 * 100
        add_bank(_d(m, 8), "Stripe payout", first)
        add_bank(_d(m, 23), "Stripe payout", total - first)
        add_bank(_d(m, 1), "Rent - Landlord LLC", -1_450_000)
        add_bank(_d(m, 28), "Bank fee", -2500)
        payout = round(payout * 1.08)
    c.bank.sort(key=lambda b: (b.date, b.id))
    for i, b in enumerate(c.bank, 1):
        b.id = f"bank-{i:04d}"
    return c
