# UX review notes

## Working at speed (UX phase)

What an underwriter or operations person does all day, made fast and calm:

- **Header that steps aside.** A submission's identity is read once; on scroll it moves into the
  app's top bar (name and route sign) and only the view tabs and sub-tabs stay pinned (79 px
  instead of 190 at 1280 x 800). Tabs never wrap.
- **Instant tabs.** Once a submission's first screen has its data, every other tab loads behind
  it, one request at a time; inbox rows warm on hover. Warm first Brief 292 ms, tab switches 27 to
  84 ms on the production build (`make demo`); responses are gzipped.
- **Go anywhere (Cmd+K).** Tabs, rules by id (DRV-01), conflicts, drivers, vehicles, submissions,
  pages and actions (copy link, theme, density). G then a letter jumps between tabs; ? lists every
  shortcut. A link with #item lands on the item, outlines it and selects a conflict.
- **Decisions confirm themselves.** Resolving a conflict or correcting a value shows a toast with
  what changed in the rules and Undo for 8 s. New and processed submissions are announced
  wherever the person is.
- **Inbox as a work queue.** Views with live counts (needs an underwriter, potential decline,
  ready to quote, processing, all), age against the turnaround target
  (`NEXT_PUBLIC_LANE_TURNAROUND_HOURS`, default 4) with overdue in red, search (/), sort, J and K
  and Enter. View, search and sort live in the link. Rows render off screen lazily
  (`content-visibility`), so hundreds stay smooth.
- **Operations, live.** What the system runs on (Postgres, S3, Mailpit) and whether each answers;
  workers by heartbeat (working, idle, no heartbeat); the job queue with each lease counting down
  and jobs another worker took over; spend and cache rate today. Refreshes every 2 s.
- **Density.** Comfortable or compact (palette), remembered per browser; compact scales the
  spacing unit, so every padding and gap tightens together.
- **Accessibility is tested.** axe runs on all 16 pages in light and dark (`make e2e`): no
  serious or critical violation. Fixes it drove: accessible names that contain the visible text,
  a text-on-blue token per theme, charts with focusable marks as groups, a valid description list,
  focusable scroll areas, full-contrast secondary text.

Every screen is captured in light and dark at 1440x900 by `make screens` (Playwright, `frontend/e2e/`)
and reviewed against the design rules in DESIGN.md. The live intake is replayed from a recorded run of the real pipeline
(`frontend/e2e/fixtures/live-events.json`), so it can be reviewed without reprocessing.

## Structure

Six views hold twelve tabs, each tab its own linkable route:

| View | Tabs |
|---|---|
| Overview | Brief, Applicant and operations, Coverage and losses |
| Documents | Documents, Audit |
| Drivers and vehicles | Drivers, Vehicles |
| Conflicts and missing | Conflicts, Missing |
| Rules | Rules |
| Findings and actions | AI findings, Next actions |

App pages: Inbox, Evals, Rules library, Operations. The evidence lens opens from any value in a
submission (click, or focus and press E).

## Review rounds

Round 1 (first capture)
- Brief: nine footnote numbers in a row read as noise. Runs are now compressed to one range ("1–9")
  that opens every source behind it.
- Brief: the potential-decline line on the route sign carried two sentences; trimmed to the first.
- Lens: pushing the page aside at 1440px squeezed the brief. The lens now overlays below 1600px and
  docks beside the content only on wider screens.
- Layout: an inner scroll container cut full-page captures and browser find; the document scrolls
  now, with a sticky rail and top bar.
- Next.js 16: client hooks that read the URL (usePathname) and the awaited route params moved inside
  Suspense boundaries with layout-matched fallbacks.

Round 2
- Lens (bug): opening a driver's date of birth showed the license-number cell. The resolver gave
  every driver attribute the row's identity facts as evidence. Each attribute now points at its own
  fact (backend `resolve/build.py`), and the lens prefers facts whose path matches the field.
- Coverage: one carrier drew in two colors because of name suffixes ("Insurance Company"); carriers
  are normalized for color and legend. Amber was removed from the carrier palette so it only ever
  means "warning". The gap label shortened to "308 days uncovered" so it fits the gap.
- Tables: "Not shown" slots and date ranges no longer wrap mid-phrase.
- Drivers: comparison tables use a fixed layout, so columns line up from card to card.
- Documents: the summary counts the email body separately from files; skipped lookup sheets no
  longer show "None" as a reading method.
- Inbox: the full route sign was too heavy for a table row; a compact sign variant is used there.
- Evals: the findings figure row was misaligned.

Round 3 (live intake)
- The small route sign in the submission header gave the decision away during the reveal; it is
  hidden while the submission is processing.
- The rules column showed outcome words ("Not applicable") 27 times; it now shows each rule id in its
  outcome color with an icon, which reads as a grid.
- The missing tray clipped its last line; the step bars gained a label for the running step.

## Checks that hold on every screen
- Every state has an icon and a word; MISSING is a dashed empty slot, never a color.
- UNKNOWN uses its own violet hue, never grey.
- Identifiers (VIN, license, policy, DOT, MC) use the monospace face with a slashed zero.
- Every number and value opens its evidence; personal data is masked until an underwriter reveals it.
- Sentence case throughout; no em dashes in UI copy; no uppercase eyebrow labels.
