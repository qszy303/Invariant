"""Write a company runway workbook (Slope by default) with real formulas (owner: A1)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from synth.company import (
    MONTHS,
    OTHER_RAMP_CATEGORIES,
    YEAR,
    Company,
)

from datetime import date

LAST_ROW = 2000  # bounded ranges, human-style (not whole-column)
BUGS = ("dup_row", "missing_vendor", "broken_rollforward")

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
INPUT_FONT = Font(color="0000FF")  # hardcoded inputs in blue, finance convention
MONEY = '#,##0.00;[Red](#,##0.00)'

# Runway row numbers.
ROW = {
    "Starting Cash": 2, "Revenue": 3, "Payroll": 4, "SaaS": 5,
    "Contractors": 6, "Rent": 7, "Other": 8, "Total Outflows": 9,
    "Net Cash Flow": 10, "Ending Cash": 11,
}
NOTES = {
    "Starting Cash": "Jan is an input; later months link to prior Ending Cash",
    "Revenue": "Stripe payouts, Bank tab",
    "Payroll": "Gross + employer taxes, Payroll tab",
    "SaaS": "Ramp category = Software (refunds net out)",
    "Contractors": "Ramp bill-pay, category = Contractors",
    "Rent": "Bank tab, Landlord LLC",
    "Other": "Travel, meals, office, marketing, legal, bank fees",
    "Total Outflows": "Sum of outflow rows",
    "Net Cash Flow": "Revenue less total outflows",
    "Ending Cash": "Starting + net",
}


def _style_header(ws, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(1, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _dates(col: str, mcol: str) -> str:
    return (f'{col}$2:{col}${LAST_ROW},">="&{mcol}$1,'
            f'{col}$2:{col}${LAST_ROW},"<"&DATE(YEAR({mcol}$1),MONTH({mcol}$1)+1,1)')


def build_workbook(company: Company, bug: str | None = None) -> Workbook:
    if bug is not None and bug not in BUGS:
        raise ValueError(f"unknown bug {bug!r}")
    wb = Workbook()

    # ---- Runway first so it opens on the model ----
    ws = wb.active
    ws.title = "Runway"
    ramp = wb.create_sheet("Ramp")
    bank = wb.create_sheet("Bank")
    payroll = wb.create_sheet("Payroll")

    ramp.append(["id", "date", "merchant", "amount", "category", "memo"])
    ramp_rows = list(company.ramp)
    if bug == "dup_row":
        # A June AWS-sized SaaS row is pasted twice.
        victim = next(r for r in ramp_rows
                      if r.date.month == 6 and r.category == "Software"
                      and r.merchant == "AWS")
        ramp_rows.insert(ramp_rows.index(victim) + 1, victim)
    for r in ramp_rows:
        category = r.category
        if bug == "missing_vendor" and r.merchant == "Retool":
            category = "Tools"  # new vendor tagged with a category no row covers
        ramp.append([r.id, r.date, r.merchant, r.amount_cents / 100, category,
                     r.memo])
    bank.append(["id", "date", "description", "amount"])
    for b in company.bank:
        bank.append([b.id, b.date, b.description, b.amount_cents / 100])
    payroll.append(["id", "pay_date", "employee", "gross", "employer_taxes"])
    for p in company.payroll:
        payroll.append([p.id, p.pay_date, p.employee, p.gross_cents / 100,
                        p.employer_taxes_cents / 100])

    for sheet, ncols, money_cols, date_col, widths in [
        (ramp, 6, ["D"], "B", [11, 12, 20, 14, 14, 28]),
        (bank, 4, ["D"], "B", [11, 12, 24, 14]),
        (payroll, 5, ["D", "E"], "B", [14, 12, 18, 14, 16]),
    ]:
        _style_header(sheet, ncols)
        sheet.freeze_panes = "A2"
        for i, w in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = w
        for row in sheet.iter_rows(min_row=2):
            row[ord(date_col) - 65].number_format = "yyyy-mm-dd"
            for mc in money_cols:
                row[ord(mc) - 65].number_format = MONEY

    # ---- Runway ----
    ws["A1"] = f"{company.name} runway"
    for m in range(MONTHS):
        col = get_column_letter(2 + m)
        ws[f"{col}1"] = date(YEAR, m + 1, 1)
        ws[f"{col}1"].number_format = "mmm yyyy"
    ws["N1"] = "Notes"
    _style_header(ws, 14)
    ws["N1"].alignment = Alignment(horizontal="left")
    for label, r in ROW.items():
        ws.cell(r, 1, label).font = Font(bold=label in (
            "Total Outflows", "Net Cash Flow", "Ending Cash"))
        ws.cell(r, 14, NOTES[label]).font = Font(italic=True, color="595959")

    rd = lambda m: _dates("Ramp!$B", get_column_letter(2 + m))  # noqa: E731
    for m in range(MONTHS):
        c = get_column_letter(2 + m)
        prev = get_column_letter(1 + m)
        D = _dates
        ramp_dates = (f"Ramp!$B$2:$B${LAST_ROW},\">=\"&{c}$1,"
                      f"Ramp!$B$2:$B${LAST_ROW},\"<\"&DATE(YEAR({c}$1),MONTH({c}$1)+1,1)")
        bank_dates = ramp_dates.replace("Ramp!$B", "Bank!$B")
        pay_dates = ramp_dates.replace("Ramp!$B", "Payroll!$B")

        def ramp_cat(cat):
            return (f'SUMIFS(Ramp!$D$2:$D${LAST_ROW},'
                    f'Ramp!$E$2:$E${LAST_ROW},"{cat}",{ramp_dates})')

        def bank_desc(pat):
            return (f'SUMIFS(Bank!$D$2:$D${LAST_ROW},'
                    f'Bank!$C$2:$C${LAST_ROW},"{pat}",{bank_dates})')

        if m == 0:
            ws[f"{c}2"] = company.starting_cash_cents / 100
            ws[f"{c}2"].font = INPUT_FONT
        else:
            ws[f"{c}2"] = f"={prev}11"
        ws[f"{c}3"] = "=" + bank_desc("Stripe*")
        ws[f"{c}4"] = (f"=SUMIFS(Payroll!$D$2:$D${LAST_ROW},{pay_dates})"
                       f"+SUMIFS(Payroll!$E$2:$E${LAST_ROW},{pay_dates})")
        ws[f"{c}5"] = "=-" + ramp_cat("Software")
        ws[f"{c}6"] = "=-" + ramp_cat("Contractors")
        ws[f"{c}7"] = "=-" + bank_desc("Rent*")
        other = "+".join(ramp_cat(k) for k in OTHER_RAMP_CATEGORIES)
        ws[f"{c}8"] = f"=-({other}+{bank_desc('Bank fee*')})"
        ws[f"{c}9"] = f"=SUM({c}4:{c}8)"
        ws[f"{c}10"] = f"={c}3-{c}9"
        ws[f"{c}11"] = f"={c}2+{c}10"
        for r in range(2, 12):
            ws[f"{c}{r}"].number_format = MONEY
        for r in (9, 10, 11):
            ws[f"{c}{r}"].font = Font(bold=True)

    if bug == "broken_rollforward":
        # Feb Starting Cash typed over the link (stale: ~$12.5k off).
        end_jan = company.expected_runway()["Ending Cash"][0]
        ws["C2"] = (end_jan - 1_250_000) / 100
        ws["C2"].font = INPUT_FONT

    ws.column_dimensions["A"].width = 18
    for m in range(MONTHS):
        ws.column_dimensions[get_column_letter(2 + m)].width = 14
    ws.column_dimensions["N"].width = 52
    ws.freeze_panes = "B2"
    return wb


def write_workbook(company: Company, path: str | Path,
                   bug: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(company, bug).save(path)
    return path
