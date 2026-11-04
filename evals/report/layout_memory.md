# Layout memory: a speed figure, not accuracy

Classification accuracy is scored with layout memory off (`sample_packet.md`). This
measures what memory saves after people confirm documents: 27 layouts were learned
by confirming the sample's documents in the eval database (the Confirm type path, with
provenance), then every other submission was classified again with layouts on, from the
cache only.

| Submission | Model calls, memory off | Memory on | Pages typed by a layout | Classify time off | On |
|---|---|---|---|---|---|
| New business submission: Cobalt Mesa Transport | 28 | 12 | 16 | 3.1 s | 1.6 s |
| New business submission: Cobalt Mesa Transport | 28 | 12 | 16 | 4.6 s | 1.5 s |
| New business submission: Cobalt Mesa Transport | 28 | 11 | 17 | 3.3 s | 1.4 s |
| New business submission: Cobalt Mesa Transport | 28 | 11 | 17 | 2.7 s | 1.3 s |
| New business submission: Cobalt Mesa Transport | 28 | 10 | 18 | 3.1 s | 1.3 s |
| New business submission: Cobalt Mesa Transport | 28 | 12 | 16 | 3.1 s | 1.4 s |
| New business submission: Cobalt Mesa Transport | 27 | 11 | 16 | 3.7 s | 1.3 s |
| New business submission: Cobalt Mesa Transport | 28 | 15 | 13 | 3.0 s | 1.8 s |
| New business submission: Cobalt Mesa Transport | 28 | 13 | 15 | 2.6 s | 1.5 s |
| New business submission: Cobalt Mesa Transport | 28 | 16 | 12 | 4.2 s | 1.7 s |
| Submission: North Star Haulage LLC, eff 12/01/2026 | 15 | 15 | 0 | 1.0 s | 0.9 s |
| **All** | **294** | **138** | | **34.5 s** | **15.9 s** |

Model calls saved: 156 of 294 (53%), at the production audit rate (10% of
layout-typed pages still go to the model).

AI audit of every layout-typed page (rate 1.0): the model agreed on 144 of 144.
