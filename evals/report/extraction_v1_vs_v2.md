# Extraction v1 against v2

The sample submission, same documents. v1: one model read per document; every schema field a fact; grounding by a ladder of looser matches. v2: field catalog (2.42b67249); only catalog fields are facts; decision fields read twice (cell, label-anchored code read, or OCR at the same anchor); validators and recall checks; the stronger model re-reads, without asking, when a decision field does not verify.

Produced by `uv run python -m lane.evals.extraction_report`.

## Summary

| | v1 | v2 |
|---|---|---|
| Facts | 2,110 | 718 |
| Facts the catalog keeps | 725 | 718 |
| Decision facts verified by a second read | none read twice (467 of 474 would verify offline) | 481 of 481 (100.0%) |
| Field accuracy, reviewed gold (82 gold fields; gold covers 82 of 481 decision facts) | 82 / 82 (100%) | 82 / 82 (100%) |
| Decision facts against the pre-filled gold (`evals/gold/sample_packet_decision_facts.yaml`, 481 entries, not yet reviewed: agreement, not accuracy) | 457 of 481 | 481 of 481 |
| Recall checks passed | 7 of 7 (offline) | 10 of 10 |
| Documents re-read by the stronger model | 0 (needed approval) | 1: Driver's license, Aleksandr Pavel Volkov, NJ Class A CDL |
| Documents in review | 0 | 0: none |
| Extraction model cost, uncached | $0.0471 | $0.0000 |

## Field accuracy against the gold set

### By tier

| Tier | v1 | v2 |
|---|---|---|
| context | 12 / 12 (100%) | 12 / 12 (100%) |
| crosscheck | 22 / 22 (100%) | 22 / 22 (100%) |
| decision | 46 / 46 (100%) | 46 / 46 (100%) |
| derived | 2 / 2 (100%) | 2 / 2 (100%) |

### By document type

| Document type | v1 | v2 |
|---|---|---|
| application | 20 / 20 (100%) | 20 / 20 (100%) |
| driver_schedule | 22 / 22 (100%) | 22 / 22 (100%) |
| drivers_license | 10 / 10 (100%) | 10 / 10 (100%) |
| email_body | 3 / 3 (100%) | 3 / 3 (100%) |
| ifta_return | 3 / 3 (100%) | 3 / 3 (100%) |
| loss_run | 20 / 20 (100%) | 20 / 20 (100%) |
| mvr | 23 / 23 (100%) | 23 / 23 (100%) |
| quote_form | 20 / 20 (100%) | 20 / 20 (100%) |
| vehicle_schedule | 14 / 14 (100%) | 14 / 14 (100%) |

### By method

| Method | v1 | v2 |
|---|---|---|
| sheet | 52 / 52 (100%) | 52 / 52 (100%) |
| text | 58 / 58 (100%) | 58 / 58 (100%) |
| vision | 13 / 13 (100%) | 13 / 13 (100%) |

## Recall per table

| Document | v1 (offline) | v2 |
|---|---|---|
| Auto liability loss run, Granite Peak Specialty, 2024-25 | pass: All 2 claims in the header were read | pass: All 2 claims in the header were read |
| Commercial auto application, drivers and vehicles | n/a | pass: All 1 filled rows of the rows were read |
| Driver list | n/a | pass: All 5 filled rows of the rows were read |
| IFTA return, Q1 2026 | pass: 32 jurisdiction records for 32 printed lines | pass: 32 jurisdiction records for 32 printed lines |
| IFTA return, Q2 2026 | pass: 22 jurisdiction records for 22 printed lines | pass: 22 jurisdiction records for 22 printed lines |
| IFTA return, Q3 2025 | pass: 32 jurisdiction records for 32 printed lines | pass: 32 jurisdiction records for 32 printed lines |
| IFTA return, Q4 2025 | pass: 30 jurisdiction records for 30 printed lines | pass: 30 jurisdiction records for 30 printed lines |
| MVR, California driver record, Morales, Luis E | pass: 1 events for 1 printed event lines | pass: 1 events for 1 printed event lines |
| MVR, New Jersey driver record, Whitfield, Marcus J, Oct 9, 2026 | pass: 2 events for 2 printed event lines | pass: 2 events for 2 printed event lines |
| Vehicle schedule | n/a | pass: All 4 filled rows of the rows were read |

## Error taxonomy

| Error | v1 | v2 |
|---|---|---|
| wrong slot | 7 | 0 |
| wrong value | 0 | 0 |
| missed | 0 | 0 |
| hallucinated | 0 | 0 |
| inferred | 5 | 0 |
| unverified | 9 | 0 |

Wrong slot: a statement where the field wants an amount or a date, not verified there. Wrong value: code read a different value at the field's anchor. Missed: rows printed but not read (recall checks). Hallucinated: printed nowhere in the document and false (the field it feeds resolves differently from v2). Inferred: printed nowhere but true (the field resolves the same as v2, where code derives it). Unverified: a single read code could not anchor, usually an image OCR could not read at that spot; not shown to be wrong.

v1, inferred:
- Auto liability loss run, Granite Peak Specialty, 2023-24: coverage_totals[0].covered = 'yes', inferred by the model (true); v2 derives it in code
- Auto liability loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].covered = 'Yes', inferred by the model (true); v2 derives it in code
- Auto liability loss run, Granite Peak Specialty, 2025-26: coverage_totals[0].covered = 'yes', inferred by the model (true); v2 derives it in code
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[2].covered = 'Yes', inferred by the model (true); v2 derives it in code
- Cargo loss run, Granite Peak Specialty, 2025-26: coverage_totals[2].covered = 'Yes', inferred by the model (true); v2 derives it in code

v1, unverified:
- Driver's license, Aleksandr Pavel Volkov, NJ Class A CDL: full_name = 'ALEKSANDR PAVEL VOLKOV'
- Driver's license, Aleksandr Pavel Volkov, NJ Class A CDL: state = 'NJ'
- Driver's license, Aleksandr Pavel Volkov, NJ Class A CDL: expiration_date = '06-02-2026'
- California commercial driver license, Nathaniel Osei: state = 'California'
- California commercial driver license, Nathaniel Osei: license_type = 'COMMERCIAL DRIVER LICENSE'
- IFTA return, Q1 2026: totals.total_miles = '142,087'
- IFTA return, Q2 2026: totals.total_miles = '103,773'
- IFTA return, Q3 2025: totals.total_miles = '137,594'
- IFTA return, Q4 2025: totals.total_miles = '130,981'

v1, wrong slot:
- Quick quote form, Cobalt Mesa Transport Inc: drivers[0].hire_date = 'OWNER'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].losses_paid = 'No Coverage'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].reserved = 'No Coverage'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].incurred = 'No Coverage'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].losses_paid = 'No Coverage'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].reserved = 'No Coverage'
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].incurred = 'No Coverage'

## v1 facts that were grounded but wrong

Grounded by v1's ladder (a match anywhere on the page was enough), and wrong: not printed and false, a statement in an amount, or a different value at the field's label. None changes a rule outcome.

- Quick quote form, Cobalt Mesa Transport Inc: drivers[0].hire_date = 'OWNER' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].losses_paid = 'No Coverage' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].reserved = 'No Coverage' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[0].incurred = 'No Coverage' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].losses_paid = 'No Coverage' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].reserved = 'No Coverage' (v1 grounding exact; wrong slot)
- Cargo loss run, Granite Peak Specialty, 2024-25: coverage_totals[1].incurred = 'No Coverage' (v1 grounding exact; wrong slot)
