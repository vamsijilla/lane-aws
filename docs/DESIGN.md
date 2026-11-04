# Lane design rules

Lane should read like a well-set underwriting file, not a dashboard. It's calm, and the evidence is one click away.

## Voice
- **Fonts:**
  - Newsreader (serif) for page titles, the verdict sentence on the Brief and the summary prose.
  - Inter for the interface.
  - JetBrains Mono for identifiers and source text.
  - Overpass only for the large figures and mile markers on How Lane works.
- **Verdict:** one sentence. The headline is in ink and the rest in graphite. Code writes it from the blockers and the request counts (`verdictSentence` in `submission/overview.tsx`); a model never writes it.

## Colour
- Warm paper (`--paper`) and ink (`--ink`), with graphite for secondary text and hairlines for structure.
- **One accent, ink-blue (`--interstate`).** It is used for links, focus, the selected tab and the one primary button.
- **Status colours are marks, not fills.** Guide, warning, stop and unknown appear as a 7px dot, a coloured word or a thin rule. A word always goes with the colour.
- **One colour per status, everywhere:**
  - `--stop` for decline and potential decline;
  - `--warning` for refer, missing and conflict;
  - `--unknown` for can't check yet;
  - `--guide` for pass, verified and the mile markers;
  - `--note` for note, not applicable and at quote.
  
  Only the Brief's decision chip is filled. Every status text passes WCAG AA on both paper and surface.
- **`--highlight`** is used only for something that just changed.

## Structure
- Sections sit under a hairline divider (`Panel`). A box (`Panel boxed`) is kept for one object to act on, such as the decision, the lens or a conflict to resolve.
- Tables are quiet:
  - graphite header labels with no fill and no capitals;
  - hairline rows;
  - tabular figures;
  - outer columns aligned with the divider.
- Tabs are text with an accent underline. Counts are plain numbers.

## Hints
- A hint explains what is already on screen: what a rule checks, what a status word means, how an assumption reads.
- It opens on hover and on keyboard focus, after a short pause.
- It holds text only, never an action.
- What a person needs to decide is always on the page. A hint adds the why.
- Plain text that has a hint (a status word, an assumption id) gets a dotted underline. Rule names and ids are links that also explain the rule (`RuleRef`, `RuleHint`).

## Motion
Motion is used only where something changed, and nothing loops or pulses:
- the lens slides in and outlines the source once;
- a rule or reason the last rerun changed gets a wash that fades over 1.2s.

Reduced motion turns all of it off.

## Avoid
Never use any of the following:
- gradients, glows, glass or blur;
- sparkle or "AI" icons;
- uppercase tracked labels;
- a filled pill for every status;
- cards inside cards;
- an icon on every row next to text that already says it;
- "and N more";
- middle-dot meta strings;
- em dashes;
- more than one primary button per view;
- alarming wording where plain dates do: write "Was due 10 days ago", not "10 days over".
