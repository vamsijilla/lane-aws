# Assumptions

The brief says ambiguity is intentional and asks for reasonable assumptions, called out. Each
interpretive call Lane makes has an ID. The short form lives in the ruleset
(`backend/rules/knight_trucking.v1.yaml`, `assumptions:`), every rule lists the IDs it depends on, and
the Rules tab shows them next to the rule. Questions to confirm each one with Knight are in
[QUESTIONS.md](QUESTIONS.md).

## Guideline interpretation

**A01. The account's state is the garaging state, or the mailing state when no garaging address
is given.** The guideline lists available states without saying which address decides. The
sample application leaves the garaging address blank, so the mailing address (Lathrop, CA) is
used, and the garaging address is requested from the agent. Rules: ELIG-02, ELIG-03, ELIG-05.

**A02. Time windows count back from the application date.** "Loss runs valued within 60 days",
"MVRs dated within 90 days of the application" and "most recent 4 quarters of IFTA" need a
reference date. The application date is used, because the guideline names it for MVRs. Every rule
is also evaluated from the requested effective date, and any outcome that would change is noted
on the rule (for example, the loss runs will be 70 days old on the effective date). The anchor is
a setting (`anchor` in the ruleset). Rules: DOC-01, DOC-03, DOC-04, INS-01.

**A03. "Non-listed states" is read two ways.** Under prohibited exposures the guideline lists
"non-listed states", and routing says any prohibited exposure declines. It can mean:
- **where the account is based** (the primary reading, consistent with "Illinois-based accounts
  on selective basis" in the available-states section), or
- **where the trucks run**, measured by IFTA miles in states outside the program list.

Only a plausible reading can trigger a potential decline. The domicile reading decides; the
IFTA-miles reading is defensible but not plausible for a long-haul program (interstate trucks
run through many states), so when it would decline the rule flags it with the miles figure and
asks the agent to confirm where trucks are dispatched and garaged (`plausible: false` in the
ruleset).

Lane evaluates both. The tolerance for miles outside the list is a setting
(`non_listed_miles_max_share`, 5%). On the sample, the account is based in California (pass) and
runs 56% of its miles in 23 states outside the list, so ELIG-02 flags: the miles figure stays in
the reason and the agent is asked where trucks are dispatched and garaged. Rule: ELIG-02.

**A04. Appliances and household goods are flagged for lithium, not declined.** Lithium battery
cargo is prohibited. The applicant declares no hazardous materials, but hauls appliances and
household goods, which can contain lithium batteries. Lane flags this for the agent to confirm
rather than declining on an inference. Rule: ELIG-04.

**A05. CDL experience comes from stated experience or the CDL issue date.** The guideline asks
for 2 years of CDL experience "in the U.S. or Canada". Where the experience was gained is not in
any document, so it is not checked. Rule: DRV-02.

**A06. An owner on a driver schedule is treated as driving.** The application answers "owner
drives: no", while the driver list and quote form list the owner as a driver. Lane treats the
listing as evidence that the owner drives and shows the disagreement as a conflict. It does not
pick a side, which is why the CDL rule is a potential decline rather than a decline. Rule: DRV-01.

**A07. Checks that need outside data are checks at quote.** SAFER violations and the
premium-per-unit test cannot be evaluated from the submission. They are shown as unknown, never
as pass, and listed on the decision and the brief as checks for the underwriter at quote. They do
not by themselves make a clean submission refer; any other unknown still refers. Production looks
up SAFER by USDOT number before routing. Rules: ELIG-06, SEL-01 (`external: true`). Revised in
P11: the first reading made every submission refer, so "ready" could never be reached.

**A08. A loss history letter is not a loss run.** The cargo 2023-2024 letter states "no paid
losses". It does not show open claims or reserves, so it does not satisfy "losses must be reported
for every prior term". A complete loss run is requested. Rules: DOC-02, INS-02.

**A09. An IFTA quarter is "most recent" once its return is due.** Returns are due 31 days after
the quarter ends, so on October 22 the most recent four quarters run through Q2. Rule: DOC-03.

**A10. License validity comes from the MVR only.** The state's motor vehicle record decides
whether a license is valid; a license card past its expiration is superseded by a newer MVR
showing a valid CDL. Card designations are not read for underwriting (see docs/SECURITY.md,
fair treatment). Rule: DRV-01.

**A11. Driver points count from the violation date.** "6 points in 3 years" is measured from the
violation date, not the conviction date. Rule: DRV-05.

**A12. The newest dated record supersedes older copies.** When two records of the same kind
describe the same subject (two MVRs for one driver), the newer one is used and the older one is
shown as superseded rather than as a conflict. Rule: DOC-04.

## Product and data

**A13. The sample email is a wrapper built for the case study.** Knight provided 15 documents and
a zip of the same files, not an email. Lane needs an email to receive, so
`scripts/make_sample_eml.py` builds one around them: an agent's note, the 15 attachments, and the
zip. The note's wording is ours; every fact Lane extracts comes from Knight's documents.

**A14. Lane recommends; the underwriter decides.** Outcomes are a recommendation and a route.
Nothing is quoted, bound or declined automatically, and no email is sent.

**A15. Routing uses every power-unit count the documents give.** When counts disagree (4 on the
schedule, 5 on the quote form), the route is stable if every count falls on the same side of the
10-unit line, and goes to the larger team if they straddle it.

**A16. Model spend needs a person's approval.** The analyst and any re-read on a stronger model
run only after an underwriter approves the estimated cost, unless the result is already cached.

**A-DEMO-01. The agent's reply in the demo is written for the demo.** `scripts/send_reply.py`
answers Lane's request for the sample packet: the reference token in the subject, a FEIN in the
body, a signed application page and a named driver exclusion for the owner, Devraj Sandhu. The
FEIN (84-2917365), the signature page and the exclusion letter are ours, not Knight's; every other
value in them (insured, DOT, MC, application date, the agent and agency) is the sample's own.
Expected and observed: the reply attaches by its reference, APP-01 passes, DRV-01 is no longer a
potential decline once an underwriter confirms the change, the route becomes a plain refer, and,
measured against the request marked sent, 3 of 23 points are answered (before quote 2 of 21,
before binding 1 of 2) with no new questions.

**A17. An excluded driver is listed but does not drive.** A signed named driver exclusion takes
that person out of the driver rules (DRV-01 and the others that judge who drives); they stay on
the board, marked excluded, with the exclusion as evidence. Rules about the documents themselves
(an MVR for every listed driver) still count them.

