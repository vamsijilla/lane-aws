# Gold set changes

The gold set (`evals/gold/sample_packet.yaml`) was written by hand from the source documents.
This file logs every change made after the fact, with the file evidence for it. A change is made
only when the documents show the gold was incomplete or counted differently from the system, never
to match a system output the files do not support.

## Nov 1, 2026: reconcile counts (13 conflicts, 15 missing items, 29 rules)

### Conflicts: 12 entries, now 13

| Change | Evidence in the files |
|---|---|
| Added C13 "Years of prior insurance" (was in `conflicts_also_valid`) | The application states 5 (`COBALT MESA TRANSPORT INC - KNIGHT.xlsx`, sheet "Application Info", cell K84). The quick quote form states "5 YRS" (`COBALT MESA TRANSPORT INC - QUOTE FORM.pdf`, page 1, "Years of Prior Insurance"). The liability terms the loss runs and the quote form list (`AL 23-24.pdf`, `AL 24-25.pdf`, `AL 25-26.pdf`, and the quote form's prior policy table) cover about 3.8 years, with a 308-day gap from Feb 10, 2023 to Dec 15, 2023. |
| Added C14 "Physical damage carried before" (was in `conflicts_also_valid`) | The quick quote form's prior policy table lists "LIAB/PHD/MTC" for all 4 terms (`COBALT MESA TRANSPORT INC - QUOTE FORM.pdf`, page 1). The 5 carrier loss runs (`AL 23-24.pdf`, `AL 24-25.pdf`, `AL 25-26.pdf`, `MTC 24-25.pdf`, `MTC 25-26.pdf`) show no physical damage coverage. |
| Removed C12 "Unlisted drivers" (`or_missing: DRV_UNLISTED`) | This is not a disagreement between documents. No document lists Kevin Brandt as a driver; he appears only as the driver in claim GA1093318 (`AL 24-25.pdf`, page 1). The system reports it as the missing item `DRV_UNLISTED`, which gold already counts as M-BRANDT. Counting it as a conflict too counted one fact twice. |

`conflicts_also_valid` is now empty. All 13 disagreements are gold conflicts.

### Missing items: 14 entries, now 15

| Change | Evidence in the files |
|---|---|
| Split M-LR2223 (`LOSS_TERM`) into M-LR2223-AL (`LOSS_TERM:auto_liability`) and M-LR2223-CARGO (`LOSS_TERM:cargo`) | The quick quote form lists the Lakeshore Mutual 2022-23 term as "LIAB/PHD/MTC" (`COBALT MESA TRANSPORT INC - QUOTE FORM.pdf`, page 1, prior policy table, row 4). No loss run or letter for that term was sent, for either line. These are two missing documents, and the agent request asks for both ("Loss runs from Lakeshore Mutual for 2022-23, liability and cargo"). |

### Rules: 29, unchanged

Gold lists 29 rule outcomes. ROUTE-02 is still `refer`. Its potential decline now belongs to
DRV-01 only (the decision's potential decline is unchanged).

### Expert findings: 18, unchanged

The 18 expert findings were reviewed against the run's analyst output. Each is supported by the
files as written, so the list is unchanged.

## Nov 1, 2026: drivers and units found only in loss history

### Rules: DRV-02, DRV-05 and DRV-06, pass to unknown

| Change | Evidence in the files |
|---|---|
| DRV-05 (driver points) `pass` to `unknown` | Kevin Brandt is the driver in claim GA1093318 (`AL 24-25.pdf`, page 1). He is on no driver schedule (`DRIVER LIST - COBALT MESA TRANSPORT INC.xlsx`, the workbook's drivers tab) and has no MVR in `MVR REPORT - COBALT MESA TRANSPORT INC.pdf`. Whether he still drives, and his points, cannot be read from the files, so the rule cannot pass. |
| DRV-06 (unacceptable history) `pass` to `unknown` | Same evidence: no MVR or license for Kevin Brandt, so his driving history is unknown. |
| DRV-02 (two years of CDL experience) `pass` to `unknown` | Same evidence: no license card, MVR or stated experience for Kevin Brandt, so his CDL experience is unknown. DRV-01 stays `refer` (Devraj Sandhu, class C) and its reason now names Kevin Brandt too. |

ELIG-01 already allowed `unknown` (the 2021 Peterbilt in claim GA1093318 has no unit type in any file).

### Expert findings: 18 of 18 covered by the Opus review

The review was rerun on Opus (`claude-opus-5-5`) on the current sample, after the rule changes
above and with the final route left out of its input. Its 17 findings cover all 18 gold
findings; the gold list is unchanged. (An earlier Opus run today, on the input before these
changes, covered 16 of 18.)

### Fields: the 2020 Volvo's make, conflict to Volvo

| Change | Evidence in the files |
|---|---|
| `vehicle[4V4NC9EH9LN248615].make` from `state: conflict` to `Volvo` | Claim GA1092764 names the unit a Freightliner (`AL 24-25.pdf`, page 1); the vehicle schedule says Volvo (`VEHICLE LIST - COBALT MESA TRANSPORT INC.xlsx`). The VIN 4V4NC9EH9LN248615 decodes to Volvo (positions 1-3, `4V4` = Volvo Trucks North America), a deterministic reference, so the field now reads Volvo, marked "settled by VIN decode"; the disagreement stays listed under resolved conflicts and gold still counts it (C-count 13). |

### Conflicts: physical damage carried before, evidence narrowed

C14 still holds. Its "No" side now cites only the three auto liability loss runs (`AL 23-24.pdf`,
`AL 24-25.pdf`, `AL 25-26.pdf`); the cargo runs (`MTC 24-25.pdf`, `MTC 25-26.pdf`) say nothing
about physical damage on the auto policy and are no longer counted.

