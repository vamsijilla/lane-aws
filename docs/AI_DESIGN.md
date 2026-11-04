# AI design: extraction

How Lane turns documents into facts the rules can trust. The model reads; code checks every
value that can change a decision; deterministic rules decide. This page describes extraction v2,
which is the default since it passed acceptance (`EXTRACT_V2=0` runs v1), and what changed from
v1.

## Principles

- Extract only what something uses. A field exists because a rule, a conflict, a missing-info
  check or a screen reads it.
- A value that can change a decision is read twice: once by the model, once by code at the same
  place on the page. It decides only when both reads agree.
- Disagreement is never resolved silently. The stronger model re-reads; if it still disagrees,
  the document goes to review with both reads shown.
- Every fact carries its evidence (document, page, box or cell, verbatim quote, method,
  confidence) and how it was verified.
- Same inputs and versions give the same outputs. Model calls are cached.

## Field catalog

`config/field_catalog.yaml` is the single list of what is extracted, per document type and shape
(PDF or sheet). Each field has:

| Key | Meaning |
|---|---|
| `type` | identifier, number, money, date, name, enum, text, table |
| `tier` | decision (a rule or conflict reads it), crosscheck (validators and recall read it), context (screens only) |
| `role` | fact (stored as a fact), check (read for validators only), display |
| `labels` | the printed labels the value sits beside; anchors for the code read |
| `values` | for enums, the printed forms ("VIOL", "CONV") of each value |
| `validators` | checks on the value or its record (VIN check digit, issue before expiry, paid plus reserved equals incurred) |
| `used_by` | rule ids, `conflicts`, `match:`, `missing:`, `check:`, `screen:`; CI fails if a field has none |
| `derived` | set by code, never asked of the model (a record's `role` when a date column prints OWNER) |
| `clean` | named cleaners run before the value becomes a fact (`legal_presence` drops a license's limited-term or non-domiciled designation from value and quote) |

`lane/extract/catalog.py` loads it and generates, per document type and shape, the pydantic schema
the model fills (same field names the resolver reads) and the field list in the prompt. The
catalog's version and content hash are part of every prompt version, so editing the catalog
invalidates exactly the cached reads it affects.

v1 made every schema leaf a fact: 2,110 facts on the sample, of which 725 are catalog fields.
v2 keeps 718.

Before any value becomes a fact, code tidies the model's answer (`engine_v2.tidy`): a role
printed where a date belongs ("OWNER" in a hire date column) becomes the record's role and the
date is left empty; catalog cleaners run. A license card's legal-presence designation is never
an underwriting signal (docs/SECURITY.md), so it is neither extracted nor shown.

## The two reads

`lane/extract/verify.py` is the code read. Words on the page (text layer, or OCR words for
images) are grouped into rows by geometry. For each value the model returned, code looks for it
at its anchor, in this order:

1. **Quoted label**: the label the model quoted ("Owner drives: yes"), found on the page by code
   with the value beside it. A layout the catalog's labels do not know still verifies.
2. **Label**: the field's label on the same line, the line below, or just before it.
3. **Record block**: for a table row, the rows from this record's quote to the next record's.
4. **Column**: under the table's key column header, or the header named by the field.
5. **Own line**: the value alone on a line (long values only).
6. **Statement**: a sentence of three or more words (or two long ones) printed as given.
7. **Page**: anywhere on the page, only for long values. Short values never ground anywhere.

The compare is typed. Identifiers fold look-alike characters (O and 0, I and 1). Numbers and
money need the same digits. Dates are normalized before comparing. Names match by tokens, in any
order, allowing a typo in a long word. Enums match any printed form of the value; states match
by name or code.

Sheets need no second read: the model names the cell, code reads the cell, and the fact is the
cell's value (`resolve_sheet`, trusting numeric cells as displayed).

Results per fact: `verified` (with how: "Label 'Expiration' on the same line"), `mismatch` (code
read a different value at the anchor; the code read is stored), or not found (a single read).

## Escalation and review

After the first pass, a document is re-read by the stronger model (Sonnet 5.5), without asking,
when a decision field disagrees or does not verify, a validator fails on a decision field read
only once, or a recall check fails. The second answer is verified the same way. On an image, where OCR may be unable to read the
spot, the stronger model's read is itself the second read: a decision value both models read the
same verifies at medium confidence (never high), unless code read a different value at the anchor.
A text page always needs code's read. If anything is
still unsettled after it, the document is `needs_review` and keeps both reads (`Fact.reads`).
Arithmetic checks (loss run totals, IFTA crossfoot) still escalate only with a person's approval,
as in v1.
Escalation is limited to decision-tier problems, so its cost is bounded by the documents that
need it (one on the sample).

## OCR on scans and photos

On an image the code read is OCR (RapidOCR, PP-OCRv6 small detection and recognition models,
local on ONNX Runtime; `triage_ocr_models`). `lane/extract/ocr_check.py`:

- **Word confidence.** A value verifies only when OCR is sure of the words code matched:
  `triage_ocr_min_word_score` (0.85), every word for an identifier, their mean otherwise. Below
  it the spot is unreadable: neither a confirmation nor a disagreement, so a shaky OCR word never
  sends a document to review as a conflict. The score is in the fact's "How verified" line
  ("OCR confidence 0.97").
- **Re-read at the anchor.** A decision value that still does not verify is read again: the band
  of the page around its anchor (or its label, or its record's row) is cropped, enlarged
  `triage_ocr_reread_scale` (2.5) times and read by OCR again. A sure read there counts, and the
  fact's reasons say "Re-read at anchor".
- **Stronger model first** for hard reads: images of license cards
  (`extract_strong_first_types`) and any document with a page graded poor
  (`extract_strong_first_poor`). The cheaper model then gives the independent second read, and the
  stronger model's values stay the facts.
- **Photos** are flattened before OCR: the page's corners are found and the perspective is
  corrected (`prep.flatten`), then tilt and shadows are evened out.

Model choice, measured with `lane.evals.ocr_bench` on the sample's 13 scanned and photographed
pages and the photo perturbation (decision values the code read verifies):

| Models | Sample (13 pages) | Photo of the quote form | OCR time |
|---|---|---|---|
| PP-OCRv6 small (kept) | 251 of 252 | 0 of 51 | 1x |
| PP-OCRv6 medium | 252 of 252 | 1 of 51 | 3x to 6x |
| PP-OCRv5 server | 47 of 252 | not run | slower than medium |

Medium verifies one more value on each for over three times the OCR time, so small stays. On the photo,
preparation does not raise OCR's confidence (mean word score 0.678 as sent, 0.673 after): the
page is found and flattened, but the print is blurred, and no model reads it reliably.

The photo perturbation before and after these changes: the quote form's decision values verify
33 of 51 both times, every one by two agreeing model reads (Sonnet now reads first, the page
being poor); no spot is re-read into a sure read; the same rules are unknown and the route is
unchanged. On the sample, all 481 decision values verify, both license photos are read by Sonnet
first, and rule outcomes, route, conflicts, missing items and the email match v1.

## Validators and recall

`lane/extract/validators.py`:

- **Validators** check values on their own: VIN length and check digit, DOT and MC formats,
  phone, state, license number format for the state, issue date before expiry, driver age at the
  order date, loss run report date after its losses, paid plus reserved equals incurred, model
  year against the VIN.
- **Recall checks** count what is printed and compare it with what was read: claims on a loss
  run against its printed total, IFTA jurisdiction rows, MVR event lines, and rows per sheet
  table. A missing claim or row fails the document and triggers escalation.

## Confidence

Confidence is a band computed from signals, not a model's self-report: high when verified;
medium for a context or crosscheck value found on the page but not at its anchor; low otherwise.
A failed validator, a table whose recall check failed, a poor page image, or an ambiguous day and
month each cap the band and add the reason. Rules use only facts that decide (`resolve.service.decides`): for
v2, a decision fact must be verified.

## What rules do with a value in review

A decision value read once and never verified decides nothing. Its field is `unverified` and
rules read it as not known. A rule that would pass (or be not applicable) while a value it reads
is in review reports unknown instead, naming the values and the document
(`rules.engine._unverified`, using the catalog's `used_by`). A refer or decline found on verified
values stands. A driver's license number that is not verified never decides who the driver is:
the record matches by name, so a misread number cannot split one person in two.

## Hidden text

Triage drops text a reader cannot see before anything reads the page: invisible render mode,
zero opacity, text under 2 points, and white text on a white background
(`documents/pdf.py`, PyMuPDF text traces). Each dropped span is recorded as a security note
(`security.hidden_text`) and shown on the overview.

## Caching, cost and latency

The cache key is the model, effort, token limit, prompt hash (which includes the catalog version
and the extractor version), the schema, the content, and the document's hash. The request names
the document type, not its title, so renaming a document does not miss the cache. Extraction runs
documents in parallel. Each extraction stores its cost and latency, and the documents view shows
them.

## Screens

- **Documents**: per document, "Expected N fields: x found, y verified, z not on the document",
  cost and latency, the fields that did not verify, and a chip when the stronger model re-read it.
- **Evidence lens**: a "How verified" row: the anchor that verified the value, or both reads
  when they disagree.

## v1 against v2 on the sample

Full report: `evals/report/extraction_v1_vs_v2.md` (`uv run python -m lane.evals.extraction_report`).

| | v1 | v2 |
|---|---|---|
| Facts | 2,110 | 720 |
| Decision facts verified by a second read | none read twice (467 of 474 would verify offline) | 481 of 481 |
| Field accuracy, reviewed gold (gold covers 82 of 481 decision facts) | 82 / 82 | 82 / 82 |
| Decision facts against the pre-filled, unreviewed gold of all 481 | 457 agree | 481 agree (pre-filled from v2) |
| Recall checks passed | 7 of 7 (offline) | 10 of 10 |
| Re-read by the stronger model | 0 (needed approval) | 1 (a license photo whose expiry OCR cannot read; both models read the same date) |
| Extraction cost, uncached | $0.047 | $0.060 |
| Hallucinated (not printed and false) | 0 | 0 |
| Inferred (not printed, true; v2 derives it in code) | 5 | 0 |
| Statements in amount or date fields | 7 | 0 |

Rule outcomes, the route, conflicts, missing items and the agent email are identical. The only
resolved differences are three driver names now in reading order. v1's coverage "yes" values are
printed nowhere but true (the model inferred them; v2 leaves them to code). v1's "No Coverage"
read as an amount and "OWNER" as a hire date were grounded by v1's ladder and are wrong; none
changed a rule. v2 stores none of them.

Gold: the reviewed gold set has 82 field values. Every decision fact (481) is pre-filled in
`evals/gold/sample_packet_decision_facts.yaml` with `reviewed: false` for a person to confirm;
until then it measures agreement, not accuracy (`uv run python -m lane.evals.decision_gold`).

The claim-deletion perturbation (`evals/perturb/deleted_claim.yaml`) removes the last claim from
a loss run. v1 read the remaining claims without complaint; v2's recall check fails the document
because the printed claim count no longer matches.

## Perturbations under v2

All 10 perturbations and the synthetic packet pass (`evals/report/perturbations.md`). The claim
deletion is caught by the recall check. Instructions hidden in white text are dropped before
extraction and still recorded as an injection attempt. On the two image perturbations, values
neither code nor a second model read could confirm stay in review, and the rules that read them
are unknown rather than passing:

- Phone photo of the quote form (OCR mean word score 0.67): APP-02, INS-01 and ROUTE-01 unknown;
  route unchanged.
- Rotated IFTA return: one state's miles read 988 and 886 by the two models, OCR could not
  settle it; ELIG-02 unknown; route unchanged.

The perturbation check allows exactly this (`unknown_when_unverified`): a change to unknown whose
reason names values in review. Any other change fails.

## Classification hardening, before and after

Three tiers: deterministic signals first (form identifiers, worksheet headers that count only over
real data, strong key phrases, lookup sheets ignored), then layouts learned only from documents a
person confirmed, then the model. Any disagreement between tiers goes to review (D103 to D106).

| Measure (layout memory off) | Before | After |
|---|---|---|
| Classification, gold sample | 23 of 24 | 24 of 24 |
| Splitting, gold sample | 1 of 2 | 2 of 2 |
| Pages typed by tier 1 signals | 0 | 13 of 33 on the sample; 5 worksheet tabs need no model call |
| Layouts shipped | 13 (12 built from the eval packet) | 0 |

Both misses before were in the workbook: a drivers tab with a "vehicle list attached" block was
folded into the application, and a hidden lookup tab was typed as a document.

Layout memory is reported as speed only (`evals/report/layout_memory.md`): after the sample's
documents were confirmed in the eval database (27 layouts, with provenance), the other ten
submissions needed 138 classification calls instead of 294 (53% fewer, with 10% of layout-typed
pages still audited), and classification took 15.9 s instead of 34.5 s. Auditing every
layout-typed page, the model agreed on 144 of 144.

Perturbations with memory off: 11 of 11 pass, `renamed` and the synthetic packet among them.
`renamed` first failed on two reads the eval cache did not hold (re-cutting the workbook made the
application and driver schedule new documents); they were recorded with the production models
for about $0.06.

Memory is not yet safe for splitting. With learned layouts on, `renamed` kept every type right
but its MVR file split into 4 documents instead of 6: a continuation layout typed pages that start
a new driver's record. The speed figure above counts calls and time only, which is one more reason
accuracy is reported with memory off; the perturbation and gold runs now turn memory off
themselves, and the speed run learns its layouts on a scratch copy only.

## Follow-up emails

`evals/report/follow_ups.md`, against the sample on scratch copies (D107 to D109):

| Case | Result |
|---|---|
| Same email twice | No change |
| Same packet in a new email | Linked as same files, not processed |
| Reply with the reference token | Attached; the submission runs again |
| Reply without the token, same DOT, same domain | Attached |
| Reply matching only by the applicant's name | Suggested as a possible follow-up, not merged |

The demo reply (`scripts/send_reply.py`, A-DEMO-01) attaches by its reference; APP-01 passes on
the FEIN in its body, DRV-01 passes once the owner is excluded by name (no longer a potential
decline), the route becomes a plain refer to Small Fleet, and the request loses three items. A
resend of the packet triages in 0.3 s instead of 225 s: pages of a file read before are reused by
sha256, with OCR cached by file, page and engine underneath.

## Known limits

- Recall counts what is printed. A loss run that also deletes its printed total is not caught by
  the count; the totals check (paid plus reserved equals incurred) still applies.
- OCR on low-quality photos cannot anchor every value. Those stay single reads and do not decide
  until a human confirms them, unless both models read the same value.
- OCR's word confidence is the recognizer's own score, not a calibrated probability; the 0.85
  threshold was set on this sample.
