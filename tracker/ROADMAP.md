# Maschine Tracker roadmap

This file separates requested future work from the current V0.45.5 release.

## Queued after V0.45.5 — not yet built

- Variation right display: show Pad 13's current Bias value **inside Pad 13's own on-screen tile** rather than off to the side. Keep the `RANDOM NOTE GENERATOR BIAS` label and arrow toward Pad 14. Pad 14 remains `BIAS PERCENTAGE`.

## Deferred / optional

- Arranger loop-point/range workflow for repeatedly auditioning a song section.
- Arranger range/block copy and duplicate workflow for larger song sections.
- Potential K8 extension from REST duration to optional Pattern-cell repeat/duration, only after explicit design work.

## Stability rules

Unless a reproducible bug requires otherwise:

- preserve the V0.44-derived master-tick playback model;
- preserve Pattern / REST / EMPTY timing semantics;
- preserve current MK3/Jam LED rules;
- preserve the V0.45.3 `VARIATION_MODE_GMEM = 15213` fix;
- preserve VARIATION as CC87 Trigger 127, latched in software.