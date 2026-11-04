"""Synthetic PDFs, images and workbooks for triage tests."""

import io
from datetime import date

import pymupdf
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFilter

LINES = [f"Line {i}: Jurisdiction summary total miles {1000 + i * 37}" for i in range(28)]


def text_image(
    *, color: bool = False, blur: float = 0, angle: float = 0, lines: list[str] | None = None
) -> Image.Image:
    """A document-like page image: dark text lines on white (or on a colored card)."""
    bg = (180, 40, 60) if color else (255, 255, 255)
    img = Image.new("RGB", (1240, 1754), bg)
    draw = ImageDraw.Draw(img)
    if color:
        draw.rectangle((100, 300, 1140, 1100), fill=(240, 235, 220))
        draw.rectangle((140, 360, 420, 700), fill=(90, 150, 220))
    for i, line in enumerate(lines or LINES):
        draw.text((120, 120 + i * 52), line, fill=(20, 20, 20), font_size=30)
    if angle:
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=bg)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


IFTA_LINES = [
    "IFTA Carrier Tax filing record",
    "Jurisdiction Summary",
    "Jurisdiction  Total Miles  Taxable Miles  Taxable Gal  Tax Paid Gal  Net Taxable Gal",
    "Nevada  1,000  1,000  160  120  40",
]


def text_pdf(pages: int = 1, lines: list[str] | None = None) -> bytes:
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        y = 72
        for line in lines or LINES[:12]:
            page.insert_text((72, y), f"{line} p{n + 1}", fontsize=11)
            y += 18
    return doc.tobytes()


def image_pdf(img: Image.Image) -> bytes:
    """A PDF page that is a single full-page image, like a scanner produces."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=png_bytes(img))
    return doc.tobytes()


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")


def workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Drivers"
    ws.append(["Name", "DOB", "License", "Years"])
    ws.append(["Pat Example", date(1980, 1, 2), "X1234567", 6])
    ws.merge_cells("F1:G1")
    ws["F1"] = "Merged header"
    ws.column_dimensions["H"].hidden = True
    ws["H2"] = "hidden note"
    hidden = wb.create_sheet("Lookups")
    hidden["A1"] = "Tractor"
    hidden.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
