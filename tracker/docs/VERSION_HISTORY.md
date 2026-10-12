# Version history and major milestones

This is a development-history summary, not a complete line-by-line changelog. The exact V0.45.5 build notes are preserved in `../v0.45.5/Documentation/`.

## Early tracker engine

- **V0.1**: initial REAPER Lua tracker engine.
- **V0.3-V0.6**: consolidation into a practical single Lua tracker plus JSFX playback engine.
- **V0.8.x**: fixed JSFX/Lua compile problems, then fixed stuck notes and note-length playback so tracker playback matched exported MIDI durations.

## Hardware integration

- MK3 pad/encoder mapping expanded through Controller Editor.
- direct two-screen MK3 display output was added via Python/USB.
- Maschine Jam added 8x8 step display/editing, track selection, mute/solo and touch-strip workflows.
- indexed LED feedback and stable pad-template rules were established through hardware tests.

## Musical editing

Later builds added:

- probability and command/value editing
- pattern lengths and note divisions
- scale lock
- note audition
- command extensions including octave bursts
- per-track pattern slots
- Undo/Redo
- settings/inheritance controls

## Arranger

The song arranger evolved to support:

- Pattern / REST / EMPTY cells
- horizontal and vertical views
- zoom and scrollbars
- copy/paste/duplicate/clear operations
- LOOP SONG vs TRUE SONG
- per-track progress
- mixed pattern lengths

V0.45 corrected Arranger editing so the edit position is based on musical time rather than copying a sequence-entry number between tracks.

## V0.44

Established the stable baseline for:

- song modes
- EMPTY cells
- Undo/Redo
- Arranger zoom/scrollbars
- clear semantics
- top-row Arranger controls

## V0.45 series

### V0.45

Added musical-time Arranger editing, Variation mode, FOLLOW+Top 6 Pattern-default reset and a larger right-screen pad layout.

### V0.45.1 / V0.45.2 diagnostic hotfixes

Explored a Variation transport regression by changing CC assignment and Gate/Trigger behaviour. The decisive result was that physical CC mapping was not the underlying playback-state bug.

### V0.45.3

Confirmed fix for VARIATION toggling tracker playback. Root cause: unassigned `VARIATION_MODE_GMEM` in JSFX evaluated to address 0 and overwrote the tracker playback flag. Fixed by defining address **15213** consistently.

### V0.45.4

Variation probabilities quantised to 10% steps.

### V0.45.5

Added Bias/Weight safety logic and expanded Variation display labels:

- Bias/Weight LEDs off when inactive
- selecting Bias from OFF automatically selects 25% Weight
- active Weight cycle is 25/50/75/100 only
- Bias OFF forces Weight OFF
- fuller right-screen descriptions for probability, velocity, density, Bias and percentage

## After V0.45.5

A later requested UI change is queued but not built: show the current Bias value inside Pad 13's Variation tile. See `../ROADMAP.md`.