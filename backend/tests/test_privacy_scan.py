"""No driver identifier reaches a viewer: every GET endpoint, on the sample submission, is
scanned for every license number and date of birth the documents hold, in every printed form."""

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.auth import issue_token
from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import Document, Fact, ResolvedField
from lane.domain.ages import to_date
from tests.slice import load_slice, process

DOB = re.compile(r"(date_of_birth|dob)$")
LICENSE = re.compile(r"license_number$")


def _personal(db: Session) -> set[str]:
    out: set[str] = set()
    for path, raw, norm in db.execute(select(Fact.path, Fact.value_raw, Fact.value_norm)):
        last = re.sub(r"\[\d+\]", "", path).split(".")[-1]
        if not raw:
            continue
        if LICENSE.search(last) and len(raw.strip()) >= 5:
            out.add(raw.strip())
        elif DOB.search(last):
            d: date | None = to_date(norm) or to_date(raw)
            if d:
                out |= {
                    f"{d:%m/%d/%Y}",
                    f"{d:%Y-%m-%d}",
                    f"{d:%b} {d.day}, {d.year}",
                    f"{d:%m/%d}/{d.year % 100:02d}",
                }
    return out


@pytest.fixture
def scanned(db: Session) -> Iterator[tuple[TestClient, str, set[str]]]:
    sid = load_slice(db)
    process(db, sid)
    with TestClient(create_app()) as client:
        # Tests sign in as an underwriter by default; this scan reads as the viewer role
        client.headers["Authorization"] = f"Bearer {issue_token('viewer', get_settings())}"
        yield client, sid, _personal(db)


def _paths(db: Session, sid: str) -> list[str]:
    base = f"/api/v1/submissions/{sid}"
    paths = ["/api/v1/submissions", "/api/v1/facts"]
    paths += [
        f"{base}{p}"
        for p in (
            "",
            "/pages",
            "/runs",
            "/documents",
            "/extractions",
            "/escalations",
            "/entities",
            "/conflicts",
            "/missing",
            "/rules",
            "/analysis",
            "/findings",
            "/actions",
            "/audit",
        )
    ]
    docs = db.scalars(select(Document.id).where(Document.submission_id == sid)).all()
    paths += [f"/api/v1/documents/{d}/facts" for d in docs]
    fields = db.scalars(select(ResolvedField.id).where(ResolvedField.submission_id == sid)).all()
    paths += [f"/api/v1/fields/{f}" for f in fields]
    return paths  # page layouts: the slice has no blob files; test_layouts_are_masked covers them


def test_no_unmasked_license_number_or_date_of_birth_for_a_viewer(
    db: Session, scanned: tuple[TestClient, str, set[str]]
) -> None:
    client, sid, personal = scanned
    assert len(personal) >= 10  # the sample has license numbers and dates of birth to find
    leaks: list[str] = []
    for path in _paths(db, sid):
        r = client.get(path)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            continue
        body = r.text
        leaks += [f"{path}: {v}" for v in personal if v in body]
    assert not leaks, leaks[:10]


def test_business_data_is_never_masked(scanned: tuple[TestClient, str, set[str]]) -> None:
    client, sid, _ = scanned
    body: list[dict[str, Any]] = client.get(f"/api/v1/submissions/{sid}/entities").json()
    applicant = next(e for e in body if e["kind"] == "applicant")
    fields = {f["attribute"]: f["value_display"] for f in applicant["fields"]}
    assert fields.get("mailing_address") and "withheld" not in str(fields["mailing_address"])


def test_layouts_are_masked_and_sheet_dates_read_as_dates() -> None:
    from lane.api.pages import present_layout

    texts = {"C4471925": "C44•••925", "1979-09-14T00:00:00": "Sep 14, ••••"}
    out = present_layout(
        {
            "cells": [
                {"ref": "D2", "t": "d", "v": "1979-09-14T00:00:00", "fmt": "mm/dd/yyyy"},
                {"ref": "F2", "t": "s", "v": "C4471925"},
                {"ref": "H3", "t": "d", "v": "2024-05-13T00:00:00", "fmt": "mm/dd/yyyy"},
                {"ref": "H4", "t": "n", "v": 45425, "fmt": "mm/dd/yyyy"},
                {"ref": "D4", "t": "n", "v": 29112, "fmt": "m/d/yy"},
            ]
        },
        texts,
    )
    cells = {c["ref"]: c["v"] for c in out["cells"]}
    assert cells == {
        "D2": "Sep 14, ••••",
        "F2": "C44•••925",
        "H3": "May 13, 2024",
        "H4": "May 13, 2024",  # a serial date reads as a date
        "D4": "Sep 14, ••••",  # a serial date of birth is masked too
    }
    words = present_layout({"words": [[0, 0, 1, 1, "C4471925", 0, 0, 0]]}, texts)["words"]
    assert words[0][4] == "C44•••925"


def test_an_underwriter_sees_no_full_date_of_birth_even_in_the_agent_email(
    db: Session, scanned: tuple[TestClient, str, set[str]]
) -> None:
    client, sid, personal = scanned
    client.headers["Authorization"] = f"Bearer {issue_token('underwriter', get_settings())}"
    actions = client.get(f"/api/v1/submissions/{sid}/actions").json()
    lines = [i["text"] for i in actions["items"]] + (
        actions["draft"]["body"].split("\n") if actions.get("draft") else []
    )
    assert not [ln for ln in lines if any(v in ln for v in personal)]
    # A disputed date of birth is still asked, by month and day
    asked = [ln for ln in lines if "dates of birth" in ln]
    assert asked and not [ln for ln in asked if re.search(r"\b(19|20)\d\d\b", ln)]
    for path in (f"/api/v1/submissions/{sid}/entities", f"/api/v1/submissions/{sid}/conflicts"):
        body = client.get(path).text
        assert not [v for v in personal if v in body], path
