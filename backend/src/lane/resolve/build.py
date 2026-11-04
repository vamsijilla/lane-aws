"""From extracted documents to an evidence board of entities and candidate values.

Generic across carriers and forms: the mapping is by document type and field meaning, never by
anything specific to one submission. Every candidate keeps its source document and fact ids.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, cast

from lane.db.models import Document
from lane.domain import geo, vin
from lane.domain import normalize as nz
from lane.domain.taxonomy import QUESTION_LABELS, question_key
from lane.extract.schemas import INFERRED_FIELDS
from lane.resolve.board import Board, Entity, Source, derived_from
from lane.resolve.compare import Compared, compare, person_key, same_person

SCHEDULE_TYPES = {"driver_schedule", "vehicle_schedule", "application", "quote_form"}

Data = dict[str, Any]


def _v(node: Any) -> str | None:
    """Value of a scalar node ({"value": ...}) or a plain string."""
    if isinstance(node, dict):
        value = cast(Data, node).get("value")
        return None if value in (None, "") else str(value)
    if node in (None, ""):
        return None
    return str(node)


def _rows(data: Data, key: str) -> list[Data]:
    items = data.get(key)
    if not isinstance(items, list):
        return []
    return [cast(Data, r) for r in cast(list[Any], items) if isinstance(r, dict)]


def class_letter(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"\bCLASS\s*([ABCDM])\b|^\s*([ABCDM])\s*$", raw.upper())
    return (m.group(1) or m.group(2)) if m else None


# "NON-CDL", "Non CDL", "NON-COMMERCIAL": the record says the license is not commercial
_NOT_COMMERCIAL = re.compile(r"\bNON[\s-]*(CDL|COMMERCIAL)\b")
# A commercial learner's permit (CLP) or an instruction permit is not a CDL: its holder may
# drive a commercial vehicle only with a CDL holder beside them
_PERMIT = re.compile(r"\bCLP\b|\bPERMIT\b|\bLEARNER")


def commercial_flag(raw: str | None) -> bool | None:
    if not raw:
        return None
    t = raw.upper()
    if _NOT_COMMERCIAL.search(t) or _PERMIT.search(t):
        return False
    if "COMMERCIAL" in t or "CDL" in t:
        return True
    if "PERSONAL" in t:
        return False
    # The field asks whether the license is commercial; some forms print the answer itself
    # ("Commercial driver license: YES")
    return nz.yes_no(raw)


# "..., Green Bay, WI 54303": a state code just before the ZIP code at the end of an address
_ADDRESS_STATE = re.compile(r",\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\s*$")

# Where each identifying attribute can sit in an extraction, by field name.
LICENSE_PATHS = ("license_number", "license_no")
NAME_PATHS = ("full_name", "name", "driver_name", "first_name", "last_name")
DOB_PATHS = ("date_of_birth", "dob", "driver_date_of_birth")


@dataclass
class DriverObs:
    source: Source
    prefix: str
    license: str | None
    name: str | None
    dob: str | None
    values: list[tuple[str, Any, str, str, str]] = field(
        default_factory=lambda: list[tuple[str, Any, str, str, str]]()
    )
    owner_marker: bool = False
    owner_paths: tuple[str, ...] = ()  # the cells that mark this driver as the owner
    details: Data = field(default_factory=lambda: dict[str, Any]())


@dataclass
class VehicleObs:
    source: Source
    prefix: str
    vin: str | None
    values: list[tuple[str, Any, str, str, str]] = field(
        default_factory=lambda: list[tuple[str, Any, str, str, str]]()
    )


class Builder:
    def __init__(
        self,
        docs: list[Document],
        data: dict[str, Data],
        facts: dict[tuple[str, str], str],
        *,
        today: date,
        ungrounded: frozenset[str] = frozenset(),
    ) -> None:
        self.docs = docs
        self.data = data
        self.facts = facts
        self.ungrounded = ungrounded  # ids of facts not found on the page they came from
        self.today = today
        self.board = Board()
        self.drivers: list[DriverObs] = []
        self.vehicles: list[VehicleObs] = []
        self.exclusions: list[tuple[str, Source]] = []  # (name as printed, the exclusion)
        self.applicant = self.board.entity("applicant", "applicant", "Applicant")
        self.agency = self.board.entity("agency", "agency", "Agency")
        self.request = self.board.entity("coverage_request", "request", "Coverage requested")
        self.ops = self.board.entity("operations", "operations", "Operations")
        self.fleet = self.board.entity("fleet", "fleet", "Fleet")
        self.history = self.board.entity("coverage_history", "history", "Coverage history")

    # --- helpers ---------------------------------------------------------------------------

    def own(self, obs: "DriverObs", names: tuple[str, ...]) -> Source:
        """The observation's source, pointing at the facts for one attribute (the DOB cell for
        the date of birth), not every fact used to identify the driver."""
        src = obs.source
        ids = tuple(
            fid
            for n in names
            if (fid := self.facts.get((src.document_id, f"{obs.prefix}.{n}" if obs.prefix else n)))
        )
        # Verified by this attribute's own facts: a name on the page does not vouch for a
        # license number that is not
        verified = any(fid not in self.ungrounded for fid in ids) if ids else src.verified
        return Source(
            src.document_id,
            src.doc_type,
            src.title,
            ids or src.fact_ids,
            src.as_of,
            src.note,
            verified,
        )

    def _at(self, doc: Document, *paths: str) -> list[str]:
        """Fact ids of top-level values on a document."""
        return [fid for p in paths if (fid := self.facts.get((doc.id, p)))]

    def _under(self, doc: Document, prefix: str) -> list[str]:
        """Fact ids of every value under a path prefix (all rows of a table), in path order."""
        return [
            fid
            for (d, p), fid in sorted(self.facts.items())
            if d == doc.id and p.startswith(prefix)
        ]

    def _ids(self, doc: Document, prefix: str, *names: str) -> list[str]:
        """Fact ids of the cells under one row, for values worked out from that row later."""
        return [fid for n in names if (fid := self.facts.get((doc.id, f"{prefix}.{n}")))]

    def source(
        self, doc: Document, *paths: str, as_of: date | None = None, note: str | None = None
    ) -> Source:
        ids = tuple(fid for p in paths if (fid := self.facts.get((doc.id, p))))
        # Verified when any value it rests on is on the page; inferred fields are not looked up
        checked = [
            fid
            for p in paths
            if p.rsplit(".", 1)[-1].split("[")[0] not in INFERRED_FIELDS
            and (fid := self.facts.get((doc.id, p)))
        ]
        verified = not checked or any(fid not in self.ungrounded for fid in checked)
        return Source(doc.id, doc.doc_type, doc.title, ids, as_of, note, verified)

    def put(
        self,
        entity: Entity,
        attr: str,
        raw: Any,
        doc: Document,
        path: str,
        kind: str,
        label: str,
        *,
        as_of: date | None = None,
    ) -> None:
        if raw in (None, ""):
            return
        self.board.add(
            entity, attr, raw, self.source(doc, path, as_of=as_of), kind=kind, label=label
        )

    def scalar(
        self, entity: Entity, data: Data, key: str, attr: str, doc: Document, kind: str, label: str
    ) -> None:
        self.put(entity, attr, _v(data.get(key)), doc, key, kind, label)

    # --- run -------------------------------------------------------------------------------

    def build(self) -> Board:
        handlers = {
            "application": self.application,
            "quote_form": self.quote_form,
            "driver_schedule": self.driver_schedule,
            "vehicle_schedule": self.vehicle_schedule,
            "mvr": self.mvr,
            "drivers_license": self.license,
            "loss_run": self.loss_run,
            "loss_history_letter": self.loss_letter,
            "ifta_return": self.ifta,
            "email_body": self.email,
            "driver_exclusion": self.exclusion,
        }
        for doc in self.docs:
            data = self.data.get(doc.id)
            if doc.superseded_by_id or data is None:
                continue
            handler = handlers.get(doc.doc_type)
            if handler:
                handler(doc, data)
        self.cluster_drivers()
        self.apply_exclusions()
        self.cluster_vehicles()
        return self.board

    def exclusion(self, doc: Document, d: Data) -> None:
        name = _v(d.get("excluded_driver"))
        if name:
            self.exclusions.append((name, self.source(doc, "excluded_driver", "letter_date")))

    def apply_exclusions(self) -> None:
        """A driver excluded by name stays on the board (the documents list them) but is marked
        excluded, with the exclusion as evidence; driver rules then leave them out."""
        for name, src in self.exclusions:
            pk = person_key(name)
            for e in self.board.of_kind("driver"):
                names = e.fields.get("name")
                if (
                    pk
                    and names
                    and any(
                        same_person(person_key(str(c.compared.norm)), pk) for c in names.candidates
                    )
                ):
                    self.board.add(e, "excluded", True, src, kind="bool", label="Excluded by name")
                    e.details["excluded"] = {
                        "document_id": src.document_id,
                        "title": src.title,
                        "name": name,
                    }
                    if e.details.get("owner_marker"):
                        # The owner is excluded by name: settles whether the owner drives
                        self.ops.details["owner_excluded"] = e.details["excluded"]

    # --- applicant, agency, request, operations ----------------------------------------------

    def application(self, doc: Document, d: Data) -> None:
        a = self.applicant
        self.scalar(a, d, "business_name", "legal_name", doc, "company", "Legal name")
        self.scalar(a, d, "business_name", "entity_form", doc, "entity_form", "Legal entity form")
        self.scalar(a, d, "entity_type", "entity_form", doc, "entity_form", "Legal entity form")
        for key, attr, kind, label in (
            ("dba", "dba", "text", "DBA"),
            ("contact_name", "contact_name", "person", "Primary contact"),
            ("contact_phone", "phone", "phone", "Phone"),
            ("contact_email", "email", "text", "Email"),
            ("mailing_address", "mailing_address", "text", "Mailing address"),
            ("mailing_city", "mailing_city", "text", "Mailing city"),
            ("mailing_state", "mailing_state", "state", "Mailing state"),
            ("mailing_zip", "mailing_zip", "id", "Mailing ZIP"),
            ("garaging_address", "garaging_address", "text", "Garaging address"),
            ("garaging_state", "garaging_state", "state", "Garaging state"),
            ("fein", "fein", "id", "FEIN"),
            ("dot_number", "dot_number", "id", "USDOT number"),
            ("mc_number", "mc_number", "id", "MC number"),
            ("date_of_authority", "date_of_authority", "date", "Date of authority"),
            ("first_date_in_business", "business_start", "date", "First date in business"),
            ("description_of_operation", "operation", "text", "Description of operation"),
            ("application_date", "application_date", "date", "Application date"),
            ("effective_date", "effective_date", "date", "Requested effective date"),
            (
                "years_experience",
                "years_experience",
                "number",
                "Years operating a transportation company",
            ),
            (
                "years_prior_insurance",
                "years_prior_insurance",
                "number",
                "Years of prior insurance",
            ),
            ("signature", "signature", "text", "Applicant signature"),
            ("form_brand", "form_brand", "text", "Form brand"),
        ):
            self.scalar(a, d, key, attr, doc, kind, label)
        for place in ("mailing", "garaging"):
            # The state printed inside the address ("Green Bay, WI 54303") when the form's own
            # state field was not read
            address = _v(d.get(f"{place}_address"))
            if address and not _v(d.get(f"{place}_state")):
                m = _ADDRESS_STATE.search(address)
                if m and geo.to_code(m.group(1)):
                    label = f"{place.capitalize()} state"
                    self.put(
                        a, f"{place}_state", m.group(1), doc, f"{place}_address", "state", label
                    )
        g = self.agency
        self.scalar(g, d, "agency_name", "name", doc, "company", "Agency name")
        self.scalar(g, d, "agency_contact", "contact", doc, "person", "Agency contact")
        self.scalar(g, d, "agency_phone", "phone", doc, "phone", "Agency phone")
        self.scalar(g, d, "agency_email", "email", doc, "text", "Agency email")

        r = self.request
        self.scalar(
            r,
            d,
            "auto_liability_limit",
            "auto_liability_limit",
            doc,
            "money",
            "Auto liability limit",
        )
        self.scalar(
            r,
            d,
            "auto_liability_deductible",
            "auto_liability_deductible",
            doc,
            "text",
            "Auto liability deductible",
        )
        self.scalar(r, d, "um_limit", "um_limit", doc, "text", "Uninsured motorist limit")
        self.scalar(r, d, "cargo_limit", "cargo_limit", doc, "money", "Cargo limit")
        self.scalar(
            r,
            d,
            "physical_damage_requested",
            "physical_damage_requested",
            doc,
            "bool",
            "Physical damage requested",
        )
        tiv = _v(d.get("physical_damage_tiv"))
        if tiv and (nz.money(tiv) or 0) > 0:
            self.put(
                r,
                "physical_damage_requested",
                True,
                doc,
                "physical_damage_tiv",
                "bool",
                "Physical damage requested",
            )

        o = self.ops
        self.scalar(o, d, "reefer", "refrigerated", doc, "bool", "Refrigerated operations")
        self.scalar(
            o,
            d,
            "farthest_territory",
            "farthest_territory",
            doc,
            "state",
            "Farthest territory (stated)",
        )
        self.scalar(o, d, "new_venture", "new_venture", doc, "bool", "New venture")
        self.scalar(o, d, "owner_is_driver", "owner_drives", doc, "bool", "Owner drives")
        self.scalar(o, d, "fast_growth", "fast_growth", doc, "bool", "Fast growth")
        self.scalar(
            o, d, "telematics_share", "telematics_share", doc, "percent", "Vehicles with telematics"
        )
        self.shares(doc, d, "radius", "radius_application")
        self.shares(doc, d, "commodities", "commodities_application")
        history = _rows(d, "historical")
        if history:
            self.applicant.details["history_application"] = {
                "document_id": doc.id,
                "title": doc.title,
                "rows": [
                    {
                        "period": _v(r.get("period")),
                        "premium": _v(r.get("premium")),
                        "power_units": _v(r.get("power_units")),
                    }
                    for r in history
                ],
            }
        for i, q in enumerate(_rows(d, "questions")):
            self.question(doc, q.get("question"), q.get("answer"), f"questions[{i}].answer")

        for i, row in enumerate(_rows(d, "drivers_table")):
            self.driver_row(doc, row, f"drivers_table[{i}]")
        for i, row in enumerate(_rows(d, "vehicles_table")):
            self.vehicle_row(doc, row, f"vehicles_table[{i}]")
        # A PDF application lists drivers and prior insurance as rows, like the quote form
        for i, row in enumerate(_rows(d, "drivers")):
            self.driver_row(doc, row, f"drivers[{i}]")
        for i, term in enumerate(_rows(d, "prior_insurance")):
            self.prior_term(doc, term, f"prior_insurance[{i}]")

    def quote_form(self, doc: Document, d: Data) -> None:
        a = self.applicant
        self.scalar(a, d, "applicant_name", "legal_name", doc, "company", "Legal name")
        self.scalar(a, d, "applicant_name", "entity_form", doc, "entity_form", "Legal entity form")
        for key, attr, kind, label in (
            ("dba", "dba", "text", "DBA"),
            ("mailing_address", "mailing_address", "text", "Mailing address"),
            ("city", "mailing_city", "text", "Mailing city"),
            ("state", "mailing_state", "state", "Mailing state"),
            ("zip", "mailing_zip", "id", "Mailing ZIP"),
            ("phone", "phone", "phone", "Phone"),
            ("email", "email", "text", "Email"),
            (
                "years_in_business",
                "years_experience",
                "number",
                "Years operating a transportation company",
            ),
            (
                "years_prior_insurance",
                "years_prior_insurance",
                "number",
                "Years of prior insurance",
            ),
            ("dot_number", "dot_number", "id", "USDOT number"),
            ("mc_number", "mc_number", "id", "MC number"),
        ):
            self.scalar(a, d, key, attr, doc, kind, label)
        g = self.agency
        self.scalar(g, d, "agency_name", "name", doc, "company", "Agency name")
        self.scalar(g, d, "agency_contact", "contact", doc, "person", "Agency contact")
        self.scalar(g, d, "agency_phone", "phone", doc, "phone", "Agency phone")

        r = self.request
        self.scalar(
            r,
            d,
            "auto_liability_limit",
            "auto_liability_limit",
            doc,
            "money",
            "Auto liability limit",
        )
        self.scalar(r, d, "um_limit", "um_limit", doc, "text", "Uninsured motorist limit")
        self.scalar(
            r,
            d,
            "physical_damage_requested",
            "physical_damage_requested",
            doc,
            "bool",
            "Physical damage requested",
        )
        self.scalar(r, d, "cargo_requested", "cargo_requested", doc, "bool", "Cargo requested")
        self.scalar(r, d, "cargo_limit", "cargo_limit", doc, "money", "Cargo limit")
        self.scalar(
            r, d, "renewal_or_new", "renewal_or_new", doc, "text", "Agency renewal or new business"
        )
        reefer = _v(d.get("reefer_breakdown_requested"))
        if nz.yes_no(reefer):
            self.board.add(
                self.ops,
                "refrigerated",
                True,
                self.source(
                    doc, "reefer_breakdown_requested", note="Reefer breakdown cover was requested"
                ),
                kind="bool",
                label="Refrigerated operations",
            )
        self.shares(doc, d, "radius", "radius_quote")
        self.shares(doc, d, "commodities", "commodities_quote")
        for i, q in enumerate(_rows(d, "questions")):
            self.question(doc, q.get("question"), q.get("answer"), f"questions[{i}].answer")
        for i, row in enumerate(_rows(d, "drivers")):
            self.driver_row(doc, row, f"drivers[{i}]")
        for i, term in enumerate(_rows(d, "prior_insurance")):
            self.prior_term(doc, term, f"prior_insurance[{i}]")

    def email(self, doc: Document, d: Data) -> None:
        # An email names the applicant loosely ("Acme Hauling"): it is never evidence
        # for the legal entity form or the agency's name, only for the name it gives
        self.scalar(self.applicant, d, "applicant_name", "legal_name", doc, "company", "Legal name")
        self.scalar(
            self.applicant,
            d,
            "requested_effective_date",
            "effective_date",
            doc,
            "date",
            "Requested effective date",
        )
        self.scalar(self.applicant, d, "fein", "fein", doc, "id", "FEIN")
        self.scalar(self.applicant, d, "dot_number", "dot_number", doc, "id", "USDOT number")
        self.scalar(self.agency, d, "agent_name", "contact", doc, "person", "Agency contact")
        self.scalar(self.agency, d, "agency_phone", "phone", doc, "phone", "Agency phone")

    def shares(self, doc: Document, d: Data, key: str, detail: str) -> None:
        rows = _rows(d, key)
        items: list[Data] = []
        for i, row in enumerate(rows):
            label = _v(row.get("label"))
            if not label:
                continue
            items.append(
                {
                    "label": label,
                    "percent": str(p)
                    if (p := nz.percent(_v(row.get("percent")))) is not None
                    else None,
                    "value": _v(row.get("value") or row.get("max_value")),
                    "fact_ids": self._ids(doc, f"{key}[{i}]", "label", "percent"),
                }
            )
        if items:
            self.ops.details[detail] = {"document_id": doc.id, "title": doc.title, "items": items}

    def question(self, doc: Document, text: Any, answer: Any, path: str) -> None:
        if not text:
            return
        key = question_key(str(text))
        if key is None:
            return
        label = str(text).strip()
        if answer in (None, ""):
            self.ops.details.setdefault("unanswered_questions", []).append(
                {"key": key, "question": label, "document_id": doc.id, "title": doc.title}
            )
            return
        self.put(self.ops, f"q_{key}", answer, doc, path, "bool", QUESTION_LABELS[key])

    # --- drivers ---------------------------------------------------------------------------

    def driver_row(self, doc: Document, row: Data, prefix: str) -> None:
        name = _v(row.get("full_name")) or _v(row.get("name"))
        if not name:
            first, middle, last = (_v(row.get(k)) for k in ("first_name", "middle", "last_name"))
            name = " ".join(x for x in (first, middle, last) if x) or None
        lic = _v(row.get("license_number"))
        if not name and not lic:
            return
        obs = DriverObs(
            self.source(doc, f"{prefix}.license_number", f"{prefix}.last_name"),
            prefix,
            lic,
            name,
            _v(row.get("date_of_birth")),
        )
        hire = _v(row.get("hire_date"))
        markers = {
            "driver_type": _v(row.get("driver_type")),
            "role": _v(row.get("role")),
            "hire_date": hire,
            "experience": _v(row.get("experience")),
        }
        obs.owner_paths = tuple(
            f"{prefix}.{k}" for k, m in markers.items() if m and "OWNER" in m.upper()
        )
        obs.owner_marker = bool(obs.owner_paths)
        # A value read from a fallback column cites that column's cell, so it opens on it
        state_from = "license_state" if _v(row.get("license_state")) else "state"
        obs.values += [
            (
                "license_state",
                _v(row.get(state_from)),
                "state",
                "License state",
                f"{prefix}.{state_from}",
            ),
            (
                "hire_date",
                hire if nz.to_date(hire) else None,
                "date",
                "Hire date",
                f"{prefix}.hire_date",
            ),
            (
                "experience_stated",
                _v(row.get("experience")),
                "text",
                "Experience (stated)",
                f"{prefix}.experience",
            ),
        ]
        # The class is often only in the experience column ("Class C, 10 yrs"): it cites the
        # cell it was read from, so it opens on that cell
        read_from = "license_class" if _v(row.get("license_class")) else "experience"
        letter = class_letter(_v(row.get(read_from)))
        if letter:
            obs.values.append(
                ("license_class", letter, "id", "License class", f"{prefix}.{read_from}")
            )
        self.drivers.append(obs)

    def mvr(self, doc: Document, d: Data) -> None:
        order = nz.to_date(_v(d.get("order_date")))
        obs = DriverObs(
            self.source(doc, "license_number", "driver_name", as_of=order),
            "",
            _v(d.get("license_number")),
            _v(d.get("driver_name")),
            _v(d.get("date_of_birth")),
        )
        lt = _v(d.get("license_type"))
        obs.values += [
            (
                "license_state",
                _v(d.get("license_state")),
                "state",
                "License state",
                "license_state",
            ),
            (
                "license_status",
                _v(d.get("license_status")),
                "text",
                "License status",
                "license_status",
            ),
            (
                "license_expiration",
                _v(d.get("expiration_date")),
                "date",
                "License expiration",
                "expiration_date",
            ),
            ("license_issued", _v(d.get("issue_date")), "date", "License issued", "issue_date"),
            (
                "first_licensed",
                _v(d.get("first_licensed")),
                "year",
                "First licensed (year)",
                "first_licensed",
            ),
        ]
        if (letter := class_letter(_v(d.get("license_class")))) is not None:
            obs.values.append(("license_class", letter, "id", "License class", "license_class"))
        if (cdl := commercial_flag(lt)) is not None:
            obs.values.append(
                ("commercial_license", cdl, "bool", "Holds a commercial license", "license_type")
            )
        obs.details = {
            "mvr": {
                "document_id": doc.id,
                "title": doc.title,
                "order_date": order.isoformat() if order else None,
                "record_type": _v(d.get("record_type")),
                # Each event keeps its own facts, so a rule cites the events it counted
                "events": [
                    {**row, "fact_ids": self._under(doc, f"events[{i}].")}
                    for i, row in enumerate(_rows(d, "events"))
                ],
                "notes": _rows(d, "notes"),
                "fact_ids": self._at(doc, "order_date"),
            }
        }
        self.drivers.append(obs)

    def license(self, doc: Document, d: Data) -> None:
        issued = nz.to_date(_v(d.get("issue_date")))
        obs = DriverObs(
            self.source(doc, "license_number", "full_name", as_of=issued),
            "",
            _v(d.get("license_number")),
            _v(d.get("full_name")),
            _v(d.get("date_of_birth")),
        )
        lt = _v(d.get("license_type")) or ""
        obs.values += [
            ("license_state", _v(d.get("state")), "state", "License state", "state"),
            (
                "license_expiration",
                _v(d.get("expiration_date")),
                "date",
                "License expiration",
                "expiration_date",
            ),
            ("license_issued", _v(d.get("issue_date")), "date", "License issued", "issue_date"),
        ]
        if (letter := class_letter(_v(d.get("license_class")))) is not None:
            obs.values.append(("license_class", letter, "id", "License class", "license_class"))
        if (cdl := commercial_flag(lt)) is not None:
            obs.values.append(
                ("commercial_license", cdl, "bool", "Holds a commercial license", "license_type")
            )
        obs.details = {
            "license_card": {
                "document_id": doc.id,
                "title": doc.title,
                "expiration": _v(d.get("expiration_date")),
                "issued": _v(d.get("issue_date")),
            }
        }
        self.drivers.append(obs)

    def claim_driver(self, doc: Document, claim: Data, prefix: str, claim_key: str) -> None:
        name = _v(claim.get("driver_name"))
        if not name:
            return
        obs = DriverObs(
            self.source(doc, f"{prefix}.driver_name", note="Named in a claim"),
            prefix,
            None,
            name,
            _v(claim.get("driver_date_of_birth")),
        )
        obs.details = {"claims": [claim_key]}
        status = _v(claim.get("driver_status"))
        if status:
            obs.details["reported_to_prior_carrier"] = "UNREPORTED" not in status.upper()
        self.drivers.append(obs)

    def _license_verified(self, obs: DriverObs) -> bool:
        """Whether the license number was found on the page. One that was not never decides
        who the driver is: a misread number would split one person in two."""
        for p in LICENSE_PATHS:
            fid = self.facts.get((obs.source.document_id, f"{obs.prefix}.{p}" if obs.prefix else p))
            if fid:
                return fid not in self.ungrounded
        return True

    def cluster_drivers(self) -> None:
        """Same person across documents: license number first, then name plus date of birth.
        A license number not found on the page matches by name instead."""
        clusters: dict[str, list[DriverObs]] = {}
        known: dict[str, tuple[frozenset[str] | None, set[date]]] = {}
        keyed = {id(o): bool(o.license) and self._license_verified(o) for o in self.drivers}
        for obs in sorted(self.drivers, key=lambda o: not keyed[id(o)]):  # licensed first
            pk = person_key(obs.name) if obs.name else None
            dob = nz.to_date(obs.dob)
            if obs.license and keyed[id(obs)]:
                key = "lic:" + re.sub(r"[^A-Z0-9]", "", obs.license.upper())
            else:
                key = next(
                    (
                        k
                        for k, (name, dobs) in known.items()
                        if pk and same_person(name, pk) and (dob is None or not dobs or dob in dobs)
                    ),
                    f"name:{'-'.join(sorted(pk))}" if pk else f"name:{obs.name}",
                )
            clusters.setdefault(key, []).append(obs)
            name, dobs = known.get(key, (pk, set()))
            if dob:
                dobs.add(dob)
            if name is None or (pk is not None and len(pk) > len(name)):
                name = pk
            known[key] = (name, dobs)

        for key, group in clusters.items():
            label = (
                next(
                    (o.name for o in group if o.source.doc_type in ("driver_schedule", "mvr")),
                    group[0].name,
                )
                or key
            )
            e = self.board.entity("driver", key, _title_name(label))
            for obs in group:
                src = obs.source
                if obs.license:
                    self.board.add(
                        e,
                        "license_number",
                        obs.license,
                        self.own(obs, LICENSE_PATHS),
                        kind="id",
                        label="License number",
                    )
                if obs.name:
                    self.board.add(
                        e, "name", obs.name, self.own(obs, NAME_PATHS), kind="person", label="Name"
                    )
                if obs.dob:
                    self.board.add(
                        e,
                        "date_of_birth",
                        obs.dob,
                        self.own(obs, DOB_PATHS),
                        kind="date",
                        label="Date of birth",
                    )
                for attr, raw, kind, label, path in obs.values:
                    if raw in (None, ""):
                        continue
                    s = Source(
                        src.document_id,
                        src.doc_type,
                        src.title,
                        tuple(f for f in (self.facts.get((src.document_id, path)),) if f),
                        src.as_of,
                        src.note,
                    )
                    self.board.add(e, attr, raw, s, kind=kind, label=label)
                for k, v in obs.details.items():
                    if isinstance(v, list):
                        e.details.setdefault(k, []).extend(cast(list[Any], v))
                    elif k == "mvr":
                        e.details.setdefault("mvrs", []).append(v)
                    else:
                        e.details[k] = v
                if obs.owner_marker:
                    # The facts behind "this driver is the owner": the name and the owner cell
                    marked = self.own(obs, NAME_PATHS).fact_ids + tuple(
                        fid
                        for p in obs.owner_paths
                        if (fid := self.facts.get((src.document_id, p)))
                    )
                    mark = {
                        "document_id": src.document_id,
                        "title": src.title,
                        "fact_ids": list(dict.fromkeys(marked)),
                    }
                    e.details.setdefault("owner_marker", mark)  # the first; all are listed below
                    marks = cast(list[dict[str, Any]], e.details.setdefault("owner_markers", []))
                    if mark not in marks:
                        marks.append(mark)
            e.details["scheduled"] = bool(
                e.found_in & {"driver_schedule", "application", "quote_form"}
            )

    # --- vehicles --------------------------------------------------------------------------

    def driver_schedule(self, doc: Document, d: Data) -> None:
        for i, row in enumerate(_rows(d, "rows")):
            self.driver_row(doc, row, f"rows[{i}]")

    def vehicle_schedule(self, doc: Document, d: Data) -> None:
        counted = [
            f"rows[{i}].vin"
            for i, row in enumerate(_rows(d, "rows"))
            if self.vehicle_row(doc, row, f"rows[{i}]")
        ]
        units = len(counted)
        self.board.add(
            self.fleet,
            "power_units",
            str(units),
            # A count cites what it counted (the VIN of every unit on the schedule); whether
            # those VINs are on the page does not change the count, so it stays verified
            Source(
                doc.id,
                doc.doc_type,
                doc.title,
                tuple(fid for p in counted if (fid := self.facts.get((doc.id, p)))),
                note=f"{units} units on the vehicle schedule",
            ),
            kind="number",
            label="Power units",
        )
        for i, extra in enumerate(_rows(d, "extra_cells")):
            label = (_v(extra.get("value")) or "").strip()
            cell_value = _v(extra.get("cell_value"))
            if label and cell_value:
                self.fleet.details.setdefault("schedule_notes", []).append(
                    {
                        "label": label,
                        "value": cell_value,
                        "fact_ids": [
                            f for f in (self.facts.get((doc.id, f"extra_cells[{i}]")),) if f
                        ],
                    }
                )
        for i, row in enumerate(_rows(d, "vehicles")):
            self.vehicle_row(doc, row, f"vehicles[{i}]")

    def vehicle_row(self, doc: Document, row: Data, prefix: str) -> bool:
        raw_vin = _v(row.get("vin"))
        year, make = _v(row.get("year")), _v(row.get("make"))
        if raw_vin and not re.search(r"\d", raw_vin):
            # A placeholder such as "VIN & VEHICLE LIST ATTACHED", not a vehicle.
            self.fleet.details.setdefault("placeholder_rows", []).append(
                {"document_id": doc.id, "title": doc.title, "text": raw_vin}
            )
            return False
        if not raw_vin and not (year and make):
            return False
        obs = VehicleObs(self.source(doc, f"{prefix}.vin"), prefix, raw_vin)
        obs.values += [
            ("year", year, "year", "Model year", f"{prefix}.year"),
            ("make", make, "text", "Make", f"{prefix}.make"),
            ("model", _v(row.get("model")), "text", "Model", f"{prefix}.model"),
            ("vehicle_type", _v(row.get("vehicle_type")), "text", "Type", f"{prefix}.vehicle_type"),
            ("cost_new", _v(row.get("cost_new")), "money", "Cost new", f"{prefix}.cost_new"),
            (
                "stated_value",
                _v(row.get("stated_value")),
                "money",
                "Stated value",
                f"{prefix}.stated_value",
            ),
            ("gvw", _v(row.get("gvw")), "number", "Gross vehicle weight", f"{prefix}.gvw"),
        ]
        self.vehicles.append(obs)
        return True

    def cluster_vehicles(self) -> None:
        clusters: dict[str, list[VehicleObs]] = {}
        notes: dict[str, list[str]] = defaultdict(list)
        for obs in self.vehicles:
            if obs.vin:
                key = vin.clean(obs.vin)
                if key not in clusters:
                    near = next((k for k in clusters if vin.edit_distance_one(k, key)), None)
                    if near:
                        notes[near].append(
                            f"{obs.source.title} shows VIN {key}, one character different"
                        )
                        key = near
            else:
                key = f"unit:{len(clusters) + 1}"
            clusters.setdefault(key, []).append(obs)

        for key, group in clusters.items():
            e = self.board.entity("vehicle", key, key)
            for obs in group:
                src = obs.source
                if obs.vin:
                    self.board.add(e, "vin", obs.vin, src, kind="id", label="VIN")
                for attr, raw, kind, label, path in obs.values:
                    if raw in (None, ""):
                        continue
                    if kind == "money" and (nz.money(raw) or 0) == 0:
                        continue  # "$ -" means blank on a spreadsheet
                    s = Source(
                        src.document_id,
                        src.doc_type,
                        src.title,
                        tuple(f for f in (self.facts.get((src.document_id, path)),) if f),
                        src.as_of,
                        src.note,
                    )
                    self.board.add(e, attr, raw, s, kind=kind, label=label)
            e.details["notes"] = notes.get(key, [])
            e.details["scheduled"] = bool(
                e.found_in & {"vehicle_schedule", "application", "quote_form"}
            )
            if not key.startswith("unit:"):
                decoded = vin.check(key, current_year=self.today.year)
                e.details["vin_check"] = {
                    "valid": decoded.valid,
                    "maker": decoded.maker,
                    "model_year": decoded.model_year,
                    "problems": decoded.problems,
                }
                vin_field = e.fields.get("vin")
                decode_srcs = derived_from(
                    (src for c in vin_field.candidates for src in c.sources)
                    if vin_field
                    else e.sources.values(),
                    "vin_decode",
                    "Decoded from the VIN printed here",
                )
                for decode_src in decode_srcs:
                    if decoded.maker:
                        self.board.add_compared(
                            e,
                            "make",
                            Compared(
                                re.sub(r"[^A-Z0-9]", "", decoded.maker),
                                decoded.maker,
                                decoded.maker.title(),
                            ),
                            decode_src,
                            kind="text",
                            label="Make",
                        )
                    if decoded.model_year:
                        c = compare("year", str(decoded.model_year))
                        if c:
                            self.board.add_compared(
                                e, "year", c, decode_src, kind="year", label="Model year"
                            )
                label = " ".join(
                    x for x in (_display(e, "year"), _display(e, "make", prefer="vin_decode")) if x
                )
                e.label = f"{label.title()} ({key[-6:]})" if label else key

    # --- losses, coverage history ------------------------------------------------------------

    def line_of(self, data: Data) -> str | None:
        return loss_run_line(data)

    def loss_run(self, doc: Document, d: Data) -> None:
        line = self.line_of(d) or "unknown"
        start = nz.to_date(_v(d.get("period_start")))
        end = nz.to_date(_v(d.get("period_end")))
        run_date = nz.to_date(_v(d.get("run_date")))
        term = self.term(line, start, end, carrier_record=True, title=doc.title)
        self.board.add(
            term,
            "carrier",
            _v(d.get("carrier")),
            self.source(doc, "carrier", as_of=run_date),
            kind="company",
            label="Carrier",
        )
        self.board.add(
            term,
            "policy_number",
            _v(d.get("policy_number")),
            self.source(doc, "policy_number", as_of=run_date),
            kind="id",
            label="Policy number",
        )
        term.details.setdefault("loss_evidence", []).append(
            {
                "document_id": doc.id,
                "title": doc.title,
                "kind": "loss_run",
                "run_date": run_date.isoformat() if run_date else None,
                "complete": True,
                # What a rule on this loss run cites: its valuation date and the policy
                "fact_ids": self._at(doc, "run_date", "policy_number"),
            }
        )
        # Only a liability loss run says anything about physical damage on that policy; a cargo
        # run's empty physical damage row says nothing about the auto policy
        pd_rows = [
            (j, t)
            for j, t in enumerate(_rows(d, "coverage_totals"))
            if line == "auto_liability"
            and _line_name((_v(t.get("coverage")) or "").upper()) == "physical_damage"
        ]
        for j, t in pd_rows:
            carried = "NO COVERAGE" not in (_v(t.get("covered")) or "").upper()
            self.history.details.setdefault("pd_evidence", []).append(
                {
                    "document_id": doc.id,
                    "title": doc.title,
                    "carried": carried,
                    "term_start": start.isoformat() if start else None,
                    "fact_ids": self._ids(doc, f"coverage_totals[{j}]", "coverage", "covered"),
                }
            )
        for i, claim in enumerate(_rows(d, "claims")):
            number = _v(claim.get("claim_number")) or f"{doc.id[:6]}-{i + 1}"
            c = self.board.entity("claim", number, f"Claim {number}")
            prefix = f"claims[{i}]"
            for key, attr, kind, label in (
                ("status", "status", "text", "Status"),
                ("date_of_loss", "date_of_loss", "date", "Date of loss"),
                ("date_reported", "date_reported", "date", "Date reported"),
                ("driver_name", "driver", "person", "Driver"),
                ("vin", "vin", "id", "VIN"),
                ("vehicle_description", "vehicle", "text", "Vehicle"),
                ("accident_location", "location", "text", "Location"),
                ("description", "description", "text", "Description"),
                ("total_paid", "paid", "money", "Paid"),
                ("total_reserves", "reserved", "money", "Reserved"),
                ("total_incurred", "incurred", "money", "Incurred"),
            ):
                self.put(c, attr, _v(claim.get(key)), doc, f"{prefix}.{key}", kind, label)
            c.details.update(
                {
                    "line": line,
                    "term_start": start.isoformat() if start else None,
                    "late_reported": (_v(claim.get("late_reported")) or "").upper().startswith("Y"),
                    "driver_reported": "UNREPORTED"
                    not in (_v(claim.get("driver_status")) or "").upper(),
                    "unit_reported": not (_v(claim.get("unit_reported")) or "")
                    .upper()
                    .startswith("Y"),
                    "lines": _rows(claim, "lines"),
                }
            )
            self.claim_driver(doc, claim, prefix, number)
            raw_vin = _v(claim.get("vin"))
            if raw_vin:
                desc = _v(claim.get("vehicle_description")) or ""
                m = re.match(r"\s*(\d{4})\s*-\s*([A-Za-z ]+?)\s*(?:-|$)", desc)
                obs = VehicleObs(
                    self.source(doc, f"{prefix}.vin", note=f"Vehicle in claim {number}"),
                    prefix,
                    raw_vin,
                )
                if m:
                    obs.values += [
                        (
                            "year",
                            m.group(1),
                            "year",
                            "Model year",
                            f"{prefix}.vehicle_description",
                        ),
                        ("make", m.group(2), "text", "Make", f"{prefix}.vehicle_description"),
                    ]
                self.vehicles.append(obs)

    def loss_letter(self, doc: Document, d: Data) -> None:
        line = _line_name((_v(d.get("coverage")) or "").upper()) or "unknown"
        start, end = _period(_v(d.get("period")))
        term = self.term(line, start, end, carrier_record=True, title=doc.title)
        src = self.source(
            doc, "author", "policy_number", as_of=nz.to_date(_v(d.get("letter_date")))
        )
        self.board.add(term, "carrier", _v(d.get("author")), src, kind="company", label="Carrier")
        self.board.add(
            term, "policy_number", _v(d.get("policy_number")), src, kind="id", label="Policy number"
        )
        scope = _v(d.get("scope")) or ""
        term.details.setdefault("loss_evidence", []).append(
            {
                "document_id": doc.id,
                "title": doc.title,
                "kind": "letter",
                "statement": _v(d.get("statement")),
                "scope": scope,
                "complete": "PAID" not in scope.upper(),
                "fact_ids": self._at(doc, "statement", "scope", "period"),
            }
        )

    def prior_term(self, doc: Document, row: Data, prefix: str) -> None:
        start, end = nz.to_date(_v(row.get("start"))), nz.to_date(_v(row.get("end")))
        lines_raw = (_v(row.get("coverage")) or "").upper()
        lines = [
            ln for ln in (_line_name(p) for p in re.split(r"[/,&]| AND ", lines_raw)) if ln
        ] or ["auto_liability"]
        for line in lines:
            if line == "physical_damage":
                self.history.details.setdefault("pd_claimed", []).append(
                    {
                        "document_id": doc.id,
                        "title": doc.title,
                        "term_start": start.isoformat() if start else None,
                        "fact_ids": self._ids(doc, prefix, "coverage", "start", "end"),
                    }
                )
                continue
            term = self.term(line, start, end)
            src = self.source(doc, f"{prefix}.carrier", note="Listed as prior insurance")
            self.board.add(
                term, "carrier", _v(row.get("carrier")), src, kind="company", label="Carrier"
            )
            self.board.add(
                term,
                "policy_number",
                _v(row.get("policy_number")),
                src,
                kind="id",
                label="Policy number",
            )
            term.details["listed_on_application"] = True
            if line == "auto_liability":
                self.board.add(
                    term,
                    "units",
                    _v(row.get("units")),
                    self.source(doc, f"{prefix}.units"),
                    kind="number",
                    label="Units insured",
                )
                if start and end and start <= self.today < end:
                    self.board.add(
                        self.fleet,
                        "power_units",
                        _v(row.get("units")),
                        self.source(
                            doc, f"{prefix}.units", note="Units on the current policy term"
                        ),
                        kind="number",
                        label="Power units",
                    )

    def term(
        self,
        line: str,
        start: date | None,
        end: date | None,
        *,
        carrier_record: bool = False,
        title: str = "",
    ) -> Entity:
        """Terms of the same line that start within ten days of each other are one term. A
        carrier's own record (loss run, letter) sets the term's dates over a form's list of
        prior policies; a difference between them is kept as a note."""
        for e in self.board.of_kind("coverage_term"):
            s = e.details.get("start")
            if (
                e.details.get("line") == line
                and start
                and s
                and abs((date.fromisoformat(s) - start).days) <= 10
            ):
                if carrier_record and not e.details.get("dates_from_carrier"):
                    old_s, old_e = s, e.details.get("end")
                    new_s, new_e = start.isoformat(), end.isoformat() if end else old_e
                    if (old_s, old_e) != (new_s, new_e):
                        days = abs((date.fromisoformat(old_s) - start).days)
                        listed = date.fromisoformat(old_s)
                        side = "later" if start > listed else "earlier"
                        # Short: the carrier's dates are shown in the row; the note says how far
                        # off the form is ("1 day later than the form (Dec 15)")
                        e.details.setdefault("notes", []).append(
                            f"{days} day{'s' if days != 1 else ''} {side} than the form "
                            f"({listed:%b} {listed.day})"
                        )
                    e.details.update({"start": new_s, "end": new_e, "dates_from_carrier": True})
                elif not carrier_record and e.details.get("dates_from_carrier"):
                    days = abs((date.fromisoformat(s) - start).days)
                    if days:
                        e.details.setdefault("notes", []).append(
                            f"A form lists {nz.long_date(start.isoformat())} as the start, "
                            f"{days} day{'s' if days != 1 else ''} from the carrier's record"
                        )
                return e
        key = f"{line}:{start.isoformat() if start else 'unknown'}"
        label = f"{_LINE_LABELS.get(line, line)} {term_years(start, end)}"
        e = self.board.entity("coverage_term", key, label)
        e.details.update(
            {
                "line": line,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "dates_from_carrier": carrier_record,
            }
        )
        return e

    # --- IFTA ------------------------------------------------------------------------------

    def ifta(self, doc: Document, d: Data) -> None:
        end = nz.to_date(_v(d.get("period_end")))
        begin = nz.to_date(_v(d.get("period_begin")))
        quarter = f"{end.year}-Q{(end.month - 1) // 3 + 1}" if end else doc.id[:8]
        q = self.board.entity("ifta_quarter", quarter, f"IFTA {nz.quarter(quarter)}")
        # Each value cites only the fact it was read from: the legal form comes from the
        # taxpayer name ("INC"), so its evidence is that name, not the account number beside it.
        name_src = self.source(doc, "taxpayer_name", as_of=end)
        self.board.add(
            self.applicant,
            "legal_name",
            _v(d.get("taxpayer_name")),
            name_src,
            kind="company",
            label="Legal name",
        )
        self.board.add(
            self.applicant,
            "entity_form",
            _v(d.get("taxpayer_name")),
            name_src,
            kind="entity_form",
            label="Legal entity form",
        )
        self.board.add(
            q,
            "account_number",
            _v(d.get("account_number")),
            self.source(doc, "account_number", as_of=end),
            kind="id",
            label="IFTA account",
        )
        totals = cast(Data, d.get("totals") or {})
        miles = nz.number(_v(totals.get("total_miles")))
        gallons = nz.number(_v(totals.get("taxable_gallons")))
        by_state: dict[str, Decimal] = {}
        for i, row in enumerate(_rows(d, "rows")):
            if (_v(row.get("surcharge")) or "").strip():
                continue
            code = geo.to_code(_v(row.get("jurisdiction")))
            m = nz.number(_v(row.get("total_miles")))
            if code and m:
                by_state[code] = by_state.get(code, Decimal(0)) + m
                q.evidence.setdefault(f"miles:{code}", []).extend(
                    self._ids(doc, f"rows[{i}]", "jurisdiction", "total_miles")
                )
        q.details.update(
            {
                "document_id": doc.id,
                "title": doc.title,
                "begin": begin.isoformat() if begin else None,
                "end": end.isoformat() if end else None,
                "total_miles": str(miles) if miles is not None else None,
                "taxable_gallons": str(gallons) if gallons is not None else None,
                "penalty": str(nz.money(_v(d.get("penalty"))) or 0),
                "interest": str(nz.money(_v(d.get("interest"))) or 0),
                "total_due": _v(d.get("total_due")),
                "miles_by_state": {
                    k: str(v) for k, v in sorted(by_state.items(), key=lambda kv: -kv[1])
                },
            }
        )


_LINE_LABELS = {
    "auto_liability": "Auto liability",
    "cargo": "Cargo",
    "physical_damage": "Physical damage",
}


def term_years(start: date | None, end: date | None) -> str:
    """'2024-2025', or what is known: 'from 2024', 'to 2025', 'dates not shown'. Never '?-?'."""
    if start and end:
        return f"{start.year}-{end.year}"
    if start:
        return f"from {start.year}"
    if end:
        return f"to {end.year}"
    return "dates not shown"


def loss_run_line(data: Data) -> str | None:
    """A loss run's line: from the totals that carry amounts, else the line in the header."""
    for t in _rows(data, "coverage_totals"):
        covered = (_v(t.get("covered")) or "").upper()
        if "NO COVERAGE" in covered:
            continue
        name = (_v(t.get("coverage")) or "").upper()
        if any(_v(t.get(k)) for k in ("losses_paid", "reserved", "incurred")) or covered:
            return _line_name(name)
    return _line_name(_v(data.get("line_of_business")) or "")


def _line_name(text: str) -> str | None:
    t = text.upper()
    if re.search(r"\bPHD\b|\bAPD\b|PHYSICAL", t):
        return "physical_damage"
    if re.search(r"\bMTC\b|CARGO", t):
        return "cargo"
    if re.search(r"\bLIAB|\bAL\b|AUTO LIAB", t):
        return "auto_liability"
    return None


def _period(text: str | None) -> tuple[date | None, date | None]:
    if not text:
        return None, None
    parts = re.split(r"\s*(?:-|to|through)\s*", text.strip(), maxsplit=1)
    if len(parts) != 2:
        return None, None
    return nz.to_date(parts[0]), nz.to_date(parts[1])


def _title_name(name: str) -> str:
    """'GARCIA, ANA M' -> 'Ana Garcia'; 'JORDAN LEE' -> 'Jordan Lee'."""
    t = re.sub(r"\s+", " ", name).strip()
    if "," in t:
        last, _, rest = t.partition(",")
        first = rest.split()[0] if rest.split() else ""
        return f"{first.title()} {last.strip().title()}".strip()
    words = [w for w in t.split() if len(w.strip(".")) > 1]
    return " ".join(w.title() for w in (words[:1] + words[-1:] if len(words) > 2 else words))


def _display(e: Entity, attr: str, *, prefer: str | None = None) -> str | None:
    f = e.fields.get(attr)
    if f is None or not f.candidates:
        return None
    if prefer:
        for c in f.candidates:
            if any(s.doc_type == prefer for s in c.sources):
                return c.compared.display
    return f.candidates[0].compared.display
