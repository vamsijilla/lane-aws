#!/usr/bin/env python3
"""Build a second, clean submission that Lane has never seen: a two-truck carrier in Wisconsin.

Every document uses a different layout from the sample packet, so this tests generalization,
not memory. It should come out with no conflicts, no potential decline, nothing missing, and a
refer to Small Fleet only because SAFER and premium cannot be checked at intake (assumption A07).
Run from backend/ (PyMuPDF and openpyxl):

    cd backend && uv run python ../scripts/make_synthetic_packet.py
"""

import io
import mimetypes
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

import openpyxl
import pymupdf

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "var" / "synthetic"
PACKET = OUT_DIR / "packet_north_star"

CARRIER = "North Star Haulage LLC"
DOT, MC, FEIN = "3812045", "1290481", "39-4471820"
APP_DATE, EFFECTIVE = "11/12/2026", "12/01/2026"

_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_TRANSLIT = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}


def vin(stem: str) -> str:
    """A 17-character VIN with a correct check digit (position 9)."""
    assert len(stem) == 17
    total = sum(_TRANSLIT[c] * w for c, w in zip(stem, _WEIGHTS, strict=True))
    digit = total % 11
    return stem[:8] + ("X" if digit == 10 else str(digit)) + stem[9:]


DRIVERS = [
    # name, license, state, class, dob, issued, hire, experience
    ("Erik Lindqvist", "L5238104729341", "WI", "A", "1978-04-12", "2004-06-01", "2019-03-04", "20+"),
    ("Maria Delgado", "D4410286731905", "WI", "A", "1988-09-23", "2012-02-15", "2021-08-16", "12+"),
]
VEHICLES = [
    # year, make, model, vin stem, cost new, stated value
    (2022, "Kenworth", "T680", vin("1XKYD49X0NJ482315"), "168,000", "142,000"),
    (2021, "Freightliner", "Cascadia", vin("3AKJHHDR0MSLM2208"), "156,000", "118,000"),
]
STATES = {"WI": 61240, "SD": 18310, "TN": 9820, "AL": 6110, "AR": 5240, "VA": 3980}
QUARTERS = [("2025-Q4", "10/01/2025", "12/31/2025"), ("2026-Q1", "01/01/2026", "03/31/2026"),
            ("2026-Q2", "04/01/2026", "06/30/2026"), ("2026-Q3", "07/01/2026", "09/30/2026")]
TERMS = ["01/01/2023", "01/01/2024", "01/01/2025", "01/01/2026"]


def pdf(lines: list[str], title: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 54
    page.insert_text((54, y), title, fontsize=14, fontname="hebo")
    y += 28
    for line in lines:
        if y > 760:
            page = doc.new_page(width=612, height=792)
            y = 54
        page.insert_text((54, y), line, fontsize=9.5)
        y += 14
    return doc.tobytes()


def application() -> bytes:
    lines = [
        "SECTION 1. APPLICANT",
        f"Named insured: {CARRIER}                Entity: Limited liability company",
        "Mailing address: 2207 Hillcrest Road, Green Bay, WI 54303",
        "Garaging address: 2207 Hillcrest Road, Green Bay, WI 54303",
        f"FEIN: {FEIN}       USDOT: {DOT}       MC: {MC}",
        "Contact: Erik Lindqvist, owner       Phone: 920-555-0137       Email: dispatch@northstarhaulage.example",
        "Years in business: 9       Years of continuous prior insurance: 4",
        "Date of operating authority: 03/14/2017       First date in business: 02/01/2017",
        f"Application date: {APP_DATE}       Requested effective date: {EFFECTIVE}",
        "",
        "SECTION 2. COVERAGE REQUESTED",
        "Auto liability: $1,000,000 combined single limit       Deductible: none",
        "Uninsured motorist: state minimum       Motor truck cargo: $100,000       Physical damage: not requested",
        "",
        "SECTION 3. OPERATIONS",
        "Operation: for-hire trucking, general freight (dry van)",
        "Radius: 0-100 miles 20%   101-300 miles 45%   301-500 miles 35%   over 500 miles 0%",
        "Commodities: paper products 40% (max value $60,000); packaged food 35% ($70,000); building materials 25% ($55,000)",
        "Farthest state operated: VA       Power units: 2       Telematics on all units: yes",
        "Owner drives: yes       New venture: no       Team driving: no       Refrigerated units: no",
        "Hazardous materials: no   Intermodal containers: no   Towing: no   Waste: no   Passengers: no",
        "Any vehicles not listed on this application: no   Change in ownership, management or name: no",
        "Workers compensation carried: yes   Drivers meet English language proficiency rules: yes",
        "",
        "SECTION 4. PRIOR INSURANCE",
        "Line              Term                      Carrier                     Policy         Units",
    ]
    for i, start in enumerate(TERMS):
        end = TERMS[i + 1] if i + 1 < len(TERMS) else "01/01/2027"
        lines.append(f"Auto liability    {start} - {end}   Lakeview Casualty Company   LCA-{2023 + i}-4471   2")
        lines.append(f"Cargo             {start} - {end}   Lakeview Casualty Company   LCC-{2023 + i}-4471   2")
    lines += ["", "SECTION 5. DRIVERS"]
    for n, (name, lic, st, cls, dob, issued, hire, exp) in enumerate(DRIVERS, 1):
        lines.append(f"{n}. {name}   License {lic} ({st}, class {cls} CDL)   DOB {dob}   CDL issued {issued}   Hired {hire}   Experience {exp} years")
    lines += ["", "SECTION 6. VEHICLES"]
    for n, (yr, make, model, v, cost, stated) in enumerate(VEHICLES, 1):
        lines.append(f"{n}. {yr} {make} {model} tractor   VIN {v}   Cost new ${cost}   Stated value ${stated}")
    lines += ["", "Signed: Erik Lindqvist, Owner       Date signed: " + APP_DATE]
    return pdf(lines, "Commercial Trucking Application")


def loss_run(line: str, start: str, policy: str) -> bytes:
    year = int(start[-4:])
    return pdf(
        [
            "Lakeview Casualty Company - Loss History Report",
            f"Insured: {CARRIER}       Policy: {policy}       Line: {line}",
            f"Policy period: {start} to 01/01/{year + 1}       Valued as of: 11/02/2026",
            "",
            "Claims reported during this period: NONE",
            "Total paid: $0.00   Total reserved: $0.00   Total incurred: $0.00",
        ],
        "Loss Run",
    )


def ifta(quarter: str, begin: str, end: str, share: float) -> bytes:
    rows = ["Jurisdiction   Total miles   Taxable gallons"]
    total = 0
    gallons = 0
    for st, annual in STATES.items():
        m = round(annual * share)
        g = round(m / 6.4)
        total += m
        gallons += g
        rows.append(f"{st:<14} {m:>11,}   {g:>15,}")
    return pdf(
        [
            f"Licensee: {CARRIER}       IFTA account: WI-44718203       Quarter: {quarter} ({begin} - {end})",
            "",
            *rows,
            f"TOTALS         {total:>11,}   {gallons:>15,}",
            "Penalty: $0.00   Interest: $0.00",
        ],
        "IFTA Quarterly Fuel Tax Return",
    )


def mvr(driver: tuple[str, ...]) -> bytes:
    name, lic, st, cls, dob, issued, _hire, _exp = driver
    return pdf(
        [
            "Wisconsin Department of Transportation - Driver Record Abstract",
            f"Name: {name.upper()}       License number: {lic}       State: {st}",
            f"Date of birth: {dob}       License class: {cls}       Commercial driver license: YES",
            f"License status: VALID       Issued: {issued}       Expires: 04/12/2029",
            "Record type: 3 YEAR       Order date: 11/05/2026",
            "",
            "Violations and convictions: NONE",
            "Accidents: NONE",
            "Suspensions or revocations: NONE",
        ],
        "Motor Vehicle Record",
    )


def sheet(headers: list[str], rows: list[list[object]], title: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = title
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def files() -> dict[str, bytes]:
    out: dict[str, bytes] = {"North Star - application.pdf": application()}
    for i, start in enumerate(TERMS):
        out[f"Lakeview AL {start[-4:]}.pdf"] = loss_run("Auto liability", start, f"LCA-{2023 + i}-4471")
        out[f"Lakeview cargo {start[-4:]}.pdf"] = loss_run("Motor truck cargo", start, f"LCC-{2023 + i}-4471")
    for (q, b, e), share in zip(QUARTERS, (0.26, 0.24, 0.23, 0.27), strict=True):
        out[f"IFTA {q}.pdf"] = ifta(q, b, e, share)
    for d in DRIVERS:
        out[f"MVR {d[0].split()[-1]}.pdf"] = mvr(d)
    out["drivers.xlsx"] = sheet(
        ["Driver name", "License number", "State", "Class", "Date of birth", "Date hired", "Years CDL experience"],
        [[d[0], d[1], d[2], d[3], d[4], d[6], d[7]] for d in DRIVERS],
        "Drivers",
    )
    out["units.xlsx"] = sheet(
        ["Unit", "Year", "Make", "Model", "Type", "VIN", "Cost new", "Stated value"],
        [[n, v[0], v[1], v[2], "Tractor", v[3], v[4], v[5]] for n, v in enumerate(VEHICLES, 1)],
        "Power units",
    )
    return out


BODY = f"""\
Hello,

New submission for {CARRIER} (USDOT {DOT}), effective {EFFECTIVE}. Two tractors, general freight,
mostly Wisconsin and the upper Midwest. Auto liability $1M and cargo $100k.

Attached: signed application, four years of Lakeview loss runs for both lines, the last four IFTA
returns, current MVRs and the driver and unit lists.

Thanks,
Priya Raman
Harborline Insurance Agency
(414) 555-0119
"""


def email(attachments: dict[str, bytes]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Priya Raman <praman@harborline.example>"
    msg["To"] = "Knight Submissions <submissions@knight-casestudy.example>"
    msg["Subject"] = f"Submission: {CARRIER}, eff {EFFECTIVE}"
    msg["Date"] = "Thu, 12 Nov 2026 09:12:00 -0600"
    msg["Message-ID"] = "<synthetic-north-star-2026-11-12@harborline.example>"
    msg.set_content(BODY)
    for name, data in attachments.items():
        guessed, _ = mimetypes.guess_type(name)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes(policy=SMTP)


def main() -> None:
    PACKET.mkdir(parents=True, exist_ok=True)
    attachments = files()
    for name, data in attachments.items():
        (PACKET / name).write_bytes(data)
    out = OUT_DIR / "north_star.eml"
    out.write_bytes(email(attachments))
    print(f"Wrote {len(attachments)} files to {PACKET} and {out}")


if __name__ == "__main__":
    main()
