# Development guide

## Golden rule: preserve a known-working baseline

The project has repeatedly exposed hardware regressions caused by changes that looked harmless in code. Make targeted changes against a confirmed working release and keep Controller Editor mappings stable unless the hardware behaviour itself is being deliberately changed.

## Source of truth

For V0.45.5 the complete exact build lives in `tracker/v0.45.5/`. Do not edit only one cooperating component when changing a shared-memory address or hardware mapping.

## Shared-memory changes

Before adding a new gmem address:

1. search every Lua/JSFX file for the surrounding address block;
2. choose an unused explicit numeric address;
3. define it in every component that reads or writes it;
4. document it in `ARCHITECTURE.md`;
5. add a regression assertion/checklist item.

Never rely on an uninitialised JSFX variable as an address. The V0.45.2 Variation bug demonstrated that this becomes address 0 and can overwrite tracker playback state.

## Hardware mapping changes

Treat the `.ncm3` / `.ncmj` files as data that must be tested on hardware.

For MK3 Pads 1-16 preserve:

- Gate behaviour
- LED For MIDI In
- Indexed colour mode
- no `onIfDown="on"`

For VARIATION preserve CC87 Trigger 127 unless a future hardware test demonstrates a better mapping.

## Playback vs editor code

Timing-sensitive note lifecycle logic belongs in JSFX. Lua should manage edits/state and communicate requests through gmem/event queues.

This separation fixed early stuck-note problems and allows tracker playback to operate without REAPER transport.

## Display changes

The Python companion sends full 480x272 RGB565 frames. UI changes should not alter the USB packet format unless the change is explicitly about display transport.

Render-test new screens without hardware where possible, then test both physical screens.

## Arranger changes

Use musical-time ticks as the invariant, not sequence-entry indices. Any navigation/edit feature that moves between tracks must preserve the shared `arranger_edit_tick` and resolve the destination cell from time.

## Versioning

The historical project has used incremental versions and hotfixes. For future public work, prefer:

- one release directory per tested version;
- a short changelog entry;
- exact hardware regression notes;
- package checksums;
- a tagged GitHub release once a build is confirmed on hardware.

## Pending UI change after V0.45.5

Do not silently alter V0.45.5. The queued change is recorded in `../ROADMAP.md`: move Pad 13's current Bias value into Pad 13's on-screen Variation tile.