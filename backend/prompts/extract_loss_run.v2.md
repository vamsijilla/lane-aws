Document type: an insurance carrier loss run for one policy term.
- line_of_business: the coverage line named in the report header (for example "Line: Motor truck cargo"), as printed; null when the header names none.
- coverage_totals: one entry per coverage column in the summary (for example Auto Liability, Physical Damage, Cargo). When a column says "No Coverage", set covered to "No Coverage" and leave the amounts null.
- claims: every claim block with its header fields and every claimant or expense line beneath it. The claim total row goes into the claim's total_* fields, not into lines.
- Count fields at the top (total, open, closed, late reported, unreported vehicles or drivers) are null when blank.
