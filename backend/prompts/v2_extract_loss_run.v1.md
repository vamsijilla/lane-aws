Document type: an insurance carrier loss run for one policy term.
- line_of_business: the coverage line named in the report header (for example "Line: Motor truck cargo"), as printed; null when the header names none.
- coverage_totals: one record per coverage column in the summary (for example Auto Liability, Physical Damage, Cargo). When a column says "No Coverage", set covered to "No Coverage" and leave the amounts null; otherwise covered is null.
- total_claims: the printed claim count in the header (null when blank). no_claims_statement: a printed statement that there are no claims, if any.
- claims: one record per claim block, with its header fields and every claimant or expense line beneath it in lines. The claim total row goes into the claim's total_* fields, not into lines.
