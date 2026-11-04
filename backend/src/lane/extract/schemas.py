"""What the model returns for each document type.

Two shapes:
  - PDF and image documents: every scalar is a `Val` (value as printed, a verbatim quote that
    contains it, and the page it is on). Table rows carry plain strings plus a page and a quote.
  - Worksheets: the model returns cell references, never values. Code reads the value from the
    cell, so a spreadsheet value cannot be misread or invented.

Values stay as printed text here. Normalization (numbers, money, dates) happens in code.
"""

from pydantic import BaseModel, Field


class Val(BaseModel):
    """One value. `value` is copied exactly as printed; `quote` is the shortest verbatim span
    from the page that contains the value or the label next to it; `page` is the page number
    shown in the page header of the input."""

    value: str | None = None
    quote: str | None = None
    page: int | None = None


class Row(BaseModel):
    """Common fields of a table row: where it is, and a verbatim snippet of it."""

    page: int | None = None
    quote: str | None = Field(default=None, description="Verbatim start of the row as printed")


# --- MVR -----------------------------------------------------------------------------------


class MvrEvent(Row):
    kind: str = Field(
        description="violation, conviction, accident, suspension or failure to appear"
    )
    event_date: str | None = None
    conviction_date: str | None = None
    description: str | None = None
    points: str | None = None
    location: str | None = None
    at_fault: str | None = None


class MvrNote(Row):
    note_date: str | None = None
    text: str


class MvrExtraction(BaseModel):
    driver_name: Val
    license_number: Val
    license_state: Val
    date_of_birth: Val
    license_class: Val
    license_type: Val = Field(description="e.g. commercial or personal/non-commercial")
    license_status: Val
    issue_date: Val
    expiration_date: Val
    first_licensed: Val = Field(description="Year or date first licensed, if printed")
    order_date: Val
    record_type: Val = Field(description="e.g. standard, three year, five year")
    events: list[MvrEvent] = Field(description="Every violation, conviction, accident, suspension")
    notes: list[MvrNote] = Field(
        description="Dated administrative entries that matter for a commercial driver: CDL "
        "downgrades, medical certificate actions, status or clearance notices, duplicates issued"
    )
    no_violations_statement: Val = Field(description="e.g. 'NONE TO REPORT', if printed")


# --- Driver's license image ----------------------------------------------------------------


class LicenseExtraction(BaseModel):
    full_name: Val
    license_number: Val
    state: Val
    license_class: Val
    license_type: Val = Field(
        description="Card wording such as COMMERCIAL DRIVER LICENSE, LIMITED-TERM, REAL ID"
    )
    date_of_birth: Val
    issue_date: Val
    expiration_date: Val
    address: Val
    endorsements: Val
    restrictions: Val
    sex: Val
    height: Val
    eyes: Val


# --- Loss runs and letters -----------------------------------------------------------------


class ClaimLine(Row):
    claimant: str | None = None
    coverage: str | None = None
    status: str | None = None
    paid: str | None = None
    reserves: str | None = None
    recovered: str | None = None
    incurred: str | None = None


class Claim(Row):
    claim_number: str | None = None
    status: str | None = None
    date_of_loss: str | None = None
    date_reported: str | None = None
    late_reported: str | None = None
    driver_name: str | None = None
    driver_date_of_birth: str | None = None
    driver_status: str | None = Field(default=None, description="e.g. Reported or Unreported")
    driver_hire_date: str | None = None
    vehicle_description: str | None = None
    vin: str | None = None
    unit_reported: str | None = Field(default=None, description="Unreported unit flag as printed")
    accident_location: str | None = None
    description: str | None = None
    total_paid: str | None = None
    total_reserves: str | None = None
    total_recovered: str | None = None
    total_incurred: str | None = None
    lines: list[ClaimLine] = Field(default_factory=lambda: list[ClaimLine]())


class CoverageTotals(Row):
    coverage: str = Field(description="e.g. Auto Liability, Physical Damage, Cargo")
    covered: str | None = Field(default=None, description="'No Coverage' if printed, else yes")
    losses_paid: str | None = None
    reserved: str | None = None
    recoveries: str | None = None
    incurred: str | None = None


class LossRunExtraction(BaseModel):
    carrier: Val
    insured_name: Val
    policy_number: Val
    period_start: Val
    period_end: Val
    run_date: Val
    deductible: Val
    total_claims: Val
    open_claims: Val
    closed_claims: Val
    late_reported_claims: Val
    claims_with_unreported_vehicles: Val
    claims_with_unreported_drivers: Val
    no_claims_statement: Val
    line_of_business: Val = Field(
        description="The coverage line the report is for, as printed in its header "
        "(for example Auto liability or Motor truck cargo); null when not printed"
    )
    coverage_totals: list[CoverageTotals]
    claims: list[Claim]


class LossLetterExtraction(BaseModel):
    author: Val = Field(description="Organization that wrote the letter")
    letter_date: Val
    insured_name: Val
    policy_number: Val
    coverage: Val = Field(description="Line of business if stated, e.g. MTC (motor truck cargo)")
    period: Val
    statement: Val = Field(description="The sentence that states the loss history, verbatim")
    scope: Val = Field(
        description="What the statement covers, as worded: e.g. 'paid losses' only, or all claims"
    )


# --- IFTA ----------------------------------------------------------------------------------


class IftaRow(Row):
    jurisdiction: str
    surcharge: str | None = None
    fuel_type: str | None = None
    total_miles: str | None = None
    taxable_miles: str | None = None
    mpg: str | None = None
    taxable_gallons: str | None = None
    tax_paid_gallons: str | None = None
    net_taxable_gallons: str | None = None
    rate: str | None = None
    tax: str | None = None
    interest: str | None = None
    total: str | None = None


class IftaTotals(Row):
    total_miles: str | None = None
    taxable_miles: str | None = None
    taxable_gallons: str | None = None
    tax_paid_gallons: str | None = None
    net_taxable_gallons: str | None = None
    tax: str | None = None
    interest: str | None = None
    total: str | None = None


class IftaExtraction(BaseModel):
    taxpayer_name: Val
    account_number: Val
    account_type: Val
    return_type: Val
    period_begin: Val
    period_end: Val
    due_date: Val
    subtotal_due: Val
    penalty: Val
    interest: Val
    total_due: Val
    rows: list[IftaRow] = Field(description="Every jurisdiction row on every page, in order")
    totals: IftaTotals


# --- Quote form (PDF) ----------------------------------------------------------------------


class Share(Row):
    label: str
    percent: str | None = None
    value: str | None = None


class Question(Row):
    number: str | None = None
    question: str
    answer: str | None = Field(default=None, description="Yes, No or blank, from the marked box")
    explanation: str | None = None


class PriorTerm(Row):
    start: str | None = None
    end: str | None = None
    carrier: str | None = None
    coverage: str | None = None
    policy_number: str | None = None
    units: str | None = None
    any_losses: str | None = None
    amount_paid: str | None = None
    notes: str | None = None


class ListedDriver(Row):
    name: str | None = None
    license_number: str | None = None
    state: str | None = None
    date_of_birth: str | None = None
    experience: str | None = None
    hire_date: str | None = None


class QuoteFormExtraction(BaseModel):
    agency_name: Val
    agency_contact: Val
    agency_phone: Val
    applicant_name: Val
    dba: Val
    mailing_address: Val
    city: Val
    state: Val
    zip: Val
    phone: Val
    email: Val
    years_in_business: Val
    years_prior_insurance: Val
    operation_type: Val
    dot_number: Val
    mc_number: Val
    auto_liability_limit: Val
    um_limit: Val
    physical_damage_requested: Val
    cargo_requested: Val
    cargo_limit: Val
    reefer_breakdown_requested: Val
    renewal_or_new: Val
    previously_submitted: Val
    radius: list[Share]
    commodities: list[Share]
    questions: list[Question]
    drivers: list[ListedDriver]
    vehicles_note: Val = Field(description="e.g. 'VEHICLE LIST ATTACHED' if no rows are filled")
    prior_insurance: list[PriorTerm]


class ApplicationPdfExtraction(BaseModel):
    """A full application printed as a PDF or image. Keys match ApplicationSheetExtraction so
    one resolver reads both."""

    form_brand: Val = Field(description="Name or logo text at the top of the form")
    application_date: Val
    effective_date: Val
    agency_name: Val
    agency_contact: Val
    agency_phone: Val
    agency_email: Val
    business_name: Val
    dba: Val
    contact_name: Val
    contact_phone: Val
    contact_email: Val
    garaging_address: Val
    garaging_state: Val
    mailing_address: Val
    mailing_city: Val
    mailing_state: Val
    mailing_zip: Val
    entity_type: Val
    fein: Val
    dot_number: Val
    mc_number: Val
    date_of_authority: Val
    first_date_in_business: Val
    description_of_operation: Val
    auto_liability_limit: Val
    auto_liability_deductible: Val
    um_limit: Val
    physical_damage_requested: Val
    cargo_limit: Val
    reefer: Val = Field(description="Refrigerated units or reefer cover: yes or no")
    farthest_territory: Val
    new_venture: Val
    owner_is_driver: Val
    years_experience: Val = Field(description="Years in business or operating trucks")
    years_prior_insurance: Val
    telematics_share: Val
    signature: Val = Field(description="The signer's name or signature mark; null if unsigned")
    signature_date: Val
    radius: list[Share]
    commodities: list[Share]
    questions: list[Question]
    drivers: list[ListedDriver]
    prior_insurance: list[PriorTerm]


# Fields whose value is the model's reading of the page, not text printed on it ("yes" for a
# coverage column that has amounts), so they cannot be looked up and are not held to that.
INFERRED_FIELDS = frozenset({"covered", "no_violations_statement"})


# --- Email body ----------------------------------------------------------------------------


class EmailExtraction(BaseModel):
    applicant_name: Val
    requested_effective_date: Val
    coverages_requested: Val
    fein: Val
    dot_number: Val
    agent_name: Val
    agency_name: Val
    agency_phone: Val
    attachments_described: Val
    other_facts: list[Val] = Field(
        description="Any other underwriting fact stated in the email (units, states, losses)"
    )


# --- Worksheets: cell references -----------------------------------------------------------


class CellRef(BaseModel):
    """`cell` is an A1 reference, prefixed with the sheet when there are several
    ('Drivers and Vehicles'!B3). `value` is only for answers expressed by position, such as an
    X under a checkbox column: then `value` names the option and `cell` points at the mark."""

    cell: str | None = None
    value: str | None = None


class ColumnMap(BaseModel):
    """Where a table sits: header row, first and last data rows, and one column letter per
    field (null when the table has no such column)."""

    sheet: str | None = None
    header_row: int | None = None
    first_data_row: int | None = None
    last_data_row: int | None = None


class DriverColumns(ColumnMap):
    number: str | None = None
    first_name: str | None = None
    middle: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    date_of_birth: str | None = None
    license_state: str | None = None
    license_number: str | None = None
    license_class: str | None = None
    experience: str | None = None
    hire_date: str | None = None
    driver_type: str | None = None
    accidents: str | None = None
    violations: str | None = None


class VehicleColumns(ColumnMap):
    number: str | None = None
    year: str | None = None
    make: str | None = None
    model: str | None = None
    vin: str | None = None
    vehicle_type: str | None = None
    gvw: str | None = None
    plate: str | None = None
    registered_state: str | None = None
    cost_new: str | None = None
    stated_value: str | None = None
    ownership: str | None = None
    garaging_zip: str | None = None


class DriverSheetExtraction(BaseModel):
    table: DriverColumns


class VehicleSheetExtraction(BaseModel):
    table: VehicleColumns
    extra_cells: list[CellRef] = Field(
        description="Labelled values outside the table, e.g. a trailer interchange limit"
    )


class ShareCells(BaseModel):
    label: CellRef
    percent: CellRef
    average_value: CellRef
    max_value: CellRef


class QuestionCells(BaseModel):
    question: CellRef
    answer: CellRef


class HistoryCells(BaseModel):
    period: CellRef = Field(description="Row label, e.g. 1st prior year")
    premium: CellRef
    power_units: CellRef


class ApplicationSheetExtraction(BaseModel):
    form_brand: CellRef = Field(description="Name or logo text at the top of the form")
    application_date: CellRef
    effective_date: CellRef
    agency_name: CellRef
    agency_contact: CellRef
    agency_phone: CellRef
    agency_email: CellRef
    agency_address: CellRef
    business_name: CellRef
    dba: CellRef
    contact_name: CellRef
    contact_phone: CellRef
    contact_email: CellRef
    garaging_address: CellRef
    garaging_city: CellRef
    garaging_state: CellRef
    garaging_zip: CellRef
    mailing_address: CellRef
    mailing_city: CellRef
    mailing_state: CellRef
    mailing_zip: CellRef
    entity_type: CellRef
    fein: CellRef
    dot_number: CellRef
    mc_number: CellRef
    date_of_authority: CellRef
    first_date_in_business: CellRef
    description_of_operation: CellRef
    filings_needed: CellRef
    auto_liability_limit: CellRef
    auto_liability_deductible: CellRef
    pip_limit: CellRef
    um_limit: CellRef
    physical_damage_tiv: CellRef
    physical_damage_deductible: CellRef
    cargo_limit: CellRef
    reefer: CellRef
    farthest_territory: CellRef
    new_venture: CellRef
    owner_is_driver: CellRef
    fast_growth: CellRef
    years_experience: CellRef
    years_prior_insurance: CellRef
    telematics_share: CellRef
    signature: CellRef
    signature_date: CellRef
    radius: list[ShareCells]
    commodities: list[ShareCells]
    questions: list[QuestionCells]
    historical: list[HistoryCells] = Field(
        description="Historical premium and power units table: one entry per prior-year row, "
        "with the cells where its values belong even when they are empty"
    )
    drivers_table: DriverColumns
    vehicles_table: VehicleColumns


# --- Schedules and applications that arrive as PDFs ----------------------------------------


class VehicleRow(Row):
    number: str | None = None
    year: str | None = None
    make: str | None = None
    model: str | None = None
    vin: str | None = None
    vehicle_type: str | None = None
    gvw: str | None = None
    cost_new: str | None = None
    stated_value: str | None = None


class DriverListExtraction(BaseModel):
    drivers: list[ListedDriver]


class VehicleListExtraction(BaseModel):
    vehicles: list[VehicleRow]
