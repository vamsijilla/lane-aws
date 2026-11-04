"""Personal data masking for API responses.

Only a driver's identifiers are personal data here: license number, date of birth and the
driver's own street address. Business data (the company's addresses, phones, emails, FEIN) is
never masked. Masking happens in the API, for every role, in three layers:

- fields and facts: by attribute (`Masker.value`), e.g. 'C44•••925', 'Sep 14, ••••';
- free text (quotes, row snippets, rule sensitivity, page words, worksheet cells): every
  identifier the database holds, in every form it is printed in, is replaced by its masked form
  (`pii_texts`, `Masker.text`);
- a last pass over every JSON response (`lane.api.main`).

An underwriter can reveal one field at a time, or a license photo; each reveal is an audit event.
"""

import copy
import re
from datetime import date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import Settings
from lane.db.models import Fact, ResolvedField
from lane.domain import normalize as nz
from lane.domain.ages import (
    application_date,
    difference,
    differing_parts,
    masked_candidate,
    to_date,
)

MASK = "•••"
YEAR = "••••"
_INDEX = re.compile(r"\[\d+\]")
# Field names that hold a driver's own street address (a license card's or an MVR's), never the
# company's mailing or garaging address
HOME_ADDRESS = frozenset({"address", "home_address", "driver_address", "residence_address"})
DOB_NAMES = frozenset({"dob", "driver_dob", "birth_date"})
# A Social Security number is masked for every role, always, even on reveal. A FEIN (12-3456789)
# is business data and stays visible.
SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-(\d{4})(?![\d-])")
SSN_LABEL = re.compile(r"\bssn\b|social_security", re.IGNORECASE)


def mask_ssn(text: str) -> str:
    return SSN.sub(lambda m: f"•••-••-{m.group(1)}", text)


def masked_license(text: str) -> str:
    return f"{text[:3]}{MASK}{text[-3:]}" if len(text) >= 8 else MASK


def masked_date(d: date) -> str:
    """A date of birth with its year hidden: 'Sep 14, ••••'."""
    return f"{d:%b} {d.day}, {YEAR}"


class Masker:
    def __init__(
        self, settings: Settings, on: date | None = None, texts: dict[str, str] | None = None
    ) -> None:
        self.pii = settings.pii_attributes
        self.on = on or date.today()
        self.texts = texts or {}

    def attribute(self, name: str) -> str | None:
        """The PII attribute a field name or fact path refers to, if any.
        'rows[0].date_of_birth' and 'driver_date_of_birth' both mean date_of_birth; 'address'
        on a license card is the driver's home address, 'mailing_address' is the company's."""
        last = _INDEX.sub("", name).split(".")[-1].lower()
        if SSN_LABEL.search(last.replace("_", " ")) or "ssn" in last.split("_"):
            return "ssn"
        if last in HOME_ADDRESS:
            return "home_address" if "home_address" in self.pii else None
        if last in DOB_NAMES:
            return "date_of_birth" if "date_of_birth" in self.pii else None
        return next(
            (a for a in self.pii if a != "home_address" and (last == a or last.endswith(f"_{a}"))),
            None,
        )

    def value(self, attribute: str | None, value: Any) -> Any:
        if attribute is None or value in (None, ""):
            return value
        text = str(value).strip()
        if attribute == "date_of_birth":
            dob = to_date(value) or nz.to_date(text)
            return masked_date(dob) if dob else "[date hidden]"
        if attribute == "license_number":
            return masked_license(text)
        if attribute == "home_address":
            return "[street withheld]"
        if attribute == "ssn":
            digits = re.sub(r"\D", "", text)
            return f"•••-••-{digits[-4:]}" if len(digits) >= 4 else MASK
        return value

    def text(self, value: Any) -> Any:
        """Free text with every known identifier replaced by its masked form."""
        return scrub(value, self.texts) if isinstance(value, str) else value

    def field_value(self, attribute: str, value: Any, display: str | None) -> tuple[Any, Any]:
        pii = self.attribute(attribute)
        if pii is None:
            return value, display
        masked = self.value(pii, value if value is not None else display)
        return masked, masked

    def candidates(self, attribute: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pii = self.attribute(attribute)
        if pii is None:
            return candidates
        out = copy.deepcopy(candidates)
        dobs = self._dobs(pii, candidates)
        parts = differing_parts(list(dobs.values())) if len(set(dobs.values())) > 1 else []
        for i, c in enumerate(out):
            if parts and i in dobs:
                c["value"] = c["display"] = masked_candidate(dobs[i], parts, self.on)
                continue
            masked = self.value(
                pii, c.get("value") if c.get("value") is not None else c.get("display")
            )
            c["value"] = masked
            c["display"] = masked
        return out

    @staticmethod
    def _dobs(pii: str | None, candidates: list[dict[str, Any]]) -> dict[int, date]:
        if pii != "date_of_birth":
            return {}
        out: dict[int, date] = {}
        for i, c in enumerate(candidates):
            d = to_date(c.get("value")) or to_date(c.get("display"))
            if d is not None:
                out[i] = d
        return out

    def difference(self, attribute: str, candidates: list[dict[str, Any]]) -> str | None:
        """For a conflicting date of birth: which parts differ, without revealing the date."""
        dobs = self._dobs(self.attribute(attribute), candidates)
        return difference(list(dobs.values()), self.on) if dobs else None

    def tree(self, node: Any) -> Any:
        """Mask every value stored under a PII key anywhere in a JSON tree."""
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in cast(dict[str, Any], node).items():
                pii = self.attribute(k)
                if pii is None:
                    out[k] = self.tree(v)
                elif isinstance(v, dict):
                    inner = cast(dict[str, Any], v)
                    out[k] = {
                        ik: self.value(pii, iv) if ik in ("value", "text", "quote", "raw") else iv
                        for ik, iv in inner.items()
                    }
                else:
                    out[k] = self.value(pii, v)
            return out
        if isinstance(node, list):
            return [self.tree(x) for x in cast(list[Any], node)]
        return self.text(node)

    def key(self, key: str) -> str:
        """Entity keys can embed a license number ('lic:C4471925')."""
        prefix, sep, rest = key.partition(":")
        if sep and prefix == "lic":
            return f"lic:{self.value('license_number', rest)}"
        return key


def known_values(session: Session, settings: Settings, submission_id: str) -> dict[str, str]:
    """Every personal value the submission holds (license numbers, dates of birth as shown),
    mapped to its masked form, for scrubbing free text such as the agent email."""
    mask = masker_for(session, settings, submission_id)
    out: dict[str, str] = {}
    for f in session.scalars(
        select(ResolvedField).where(ResolvedField.submission_id == submission_id)
    ):
        pii = mask.attribute(f.attribute)
        if pii is None:
            continue
        for c in f.candidates:
            raw = c.get("display")
            if isinstance(raw, str) and len(raw) >= 4:
                out[raw] = str(
                    mask.value(pii, c.get("value") if c.get("value") is not None else raw)
                )
    return out


def scrub(text: str, values: dict[str, str]) -> str:
    for raw in sorted(values, key=len, reverse=True):
        text = text.replace(raw, values[raw])
    return mask_ssn(text)


_DOB_KEYS = ("date_of_birth", "dob")


def _date_forms(d: date) -> dict[str, str]:
    """The ways a date of birth is printed or stored, each with its masked form."""
    y2, masked = f"{d.year % 100:02d}", masked_date(d)
    forms = {
        f"{d:%m/%d/%Y}": f"{d:%m/%d}/{YEAR}",
        f"{d.month}/{d.day}/{d.year}": f"{d.month}/{d.day}/{YEAR}",
        f"{d:%m/%d}/{y2}": f"{d:%m/%d}/••",
        f"{d.month}/{d.day}/{y2}": f"{d.month}/{d.day}/••",
        f"{d:%m-%d-%Y}": f"{d:%m-%d}-{YEAR}",
        f"{d:%Y-%m-%d}T00:00:00": masked,
        f"{d:%Y-%m-%d} 00:00:00": masked,
        f"{d:%Y-%m-%d}": masked,
        f"{d:%b} {d.day}, {d.year}": masked,
        f"{d:%B} {d.day}, {d.year}": masked,
    }
    return forms


def pii_texts(session: Session, settings: Settings) -> dict[str, str]:
    """Every driver identifier the database holds, in every form it may be printed in, mapped to
    its masked form: for scrubbing quotes, row snippets, page words and any other free text."""
    mask = Masker(settings)
    out: dict[str, str] = {}
    for path, raw, norm in session.execute(select(Fact.path, Fact.value_raw, Fact.value_norm)):
        pii = mask.attribute(path)
        if pii is None or not raw:
            continue
        text = str(raw).strip()
        if pii == "license_number" and len(text) >= 5:
            out[text] = masked_license(text)
            compact = re.sub(r"[\s-]", "", text)
            out.setdefault(compact, masked_license(compact))
        elif pii == "date_of_birth":
            d = to_date(norm) or to_date(text) or nz.to_date(text)
            if d is not None:
                out.update(_date_forms(d))
            if len(text) >= 6:
                out.setdefault(text, masked_date(d) if d else "[date hidden]")
        elif pii == "home_address" and len(text) >= 6:
            out[text] = "[street withheld]"
    for f in session.scalars(select(ResolvedField)):
        pii = mask.attribute(f.attribute)
        if pii != "license_number":
            continue
        for c in f.candidates:
            raw = c.get("display")
            if isinstance(raw, str) and len(raw) >= 5:
                out.setdefault(raw, masked_license(raw))
    return out


def masker_for(session: Session, settings: Settings, submission_id: str) -> Masker:
    """Ages are counted from the application date, as everywhere else."""
    return Masker(settings, application_date(session, submission_id), pii_texts(session, settings))
