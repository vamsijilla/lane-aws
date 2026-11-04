# Follow-up emails

Each case ran against the sample submission on its own scratch copy of the database (`lane.evals.follow_ups`).

| Case | Expected | Got | Pass |
|---|---|---|---|
| Same email twice | No change | No change | yes |
| Same packet in a new email | Linked as same files, not processed | same_files, processed: no | yes |
| Reply with the reference | Attached | follow_up by reference | yes |
| Reply without the reference, same DOT | Attached | follow_up by identity | yes |
| Reply matching only by name | Suggested, not merged | possible_follow_up, merged: no | yes |

5 of 5 pass.

## Resend time (triage, the whole packet as a new submission)

| Reuse | Triage time |
|---|---|
| None, every page read again (before) | 224.6 s |
| OCR cache only (file sha256, page, engine and version, settings) | 177.4 s |
| Pages of a file read before reused, by sha256 (after) | 0.3 s |

13 scanned pages went through OCR once. OCR is a minority of triage time; rendering and preparing scans is most of it, so the whole reading is reused by file.
