# Architecture

## Overview

Maschine Tracker is deliberately split into independent components because REAPER Lua, JSFX and the MK3 display have different timing and I/O requirements.

### 1. Main Lua application

`Maschine_Tracker_V0455.lua` owns the editable musical model and most UI state:

- tracks, patterns and cells
- step values
- pattern length/division
- project persistence
- Undo/Redo history
- Arranger cells and musical-time edit cursor
- scale state
- settings
- Variation-mode generation logic
- REAPER GUI
- hardware LED desired-state tables
- display state export

Lua is suitable for the editor and project-management layer but is not used as the sample-accurate playback engine.

### 2. MIDI Engine JSFX

`Maschine_Tracker_MIDI_Engine_V0455.jsfx` performs timing-critical work inside REAPER's audio/MIDI processing graph:

- tracker clock/playback
- note-on/note-off scheduling
- note lengths
- probability decisions
- command playback
- live MK3 MIDI-event decoding
- audition events
- transport-independent tracker playback
- queueing hardware events for Lua

This design is why tracker playback can run independently of the REAPER transport.

### 3. MK3 LED Bridge JSFX

`Maschine_Tracker_MK3_LED_Bridge_V0455.jsfx` reads desired LED state from shared memory and sends MIDI feedback to the MK3 Controller Editor port. It keeps LED traffic separate from the sequencer engine.

### 4. Jam Bridge JSFX

`Maschine_Tracker_Jam_Bridge_V0455.jsfx` is both an input translator and feedback bridge. It uses a dedicated gmem event queue to avoid write collisions with the MK3 event queue.

### 5. Python display companion

`Maschine_Tracker_Display_V0455.py` does not use MIDI for the screens. It reads the tracker-generated state file, renders 480x272 images with Pillow and writes full-screen RGB565 packets directly to the MK3 USB display endpoint.

## Shared-memory protocol

All REAPER-side components use:

```text
options:gmem=MaschineTrackerV06
```

The namespace name is historical and intentionally retained for compatibility.

Selected address regions in V0.45.5:

| Region | Purpose |
|---:|---|
| `0...` | core playback state; notably `gmem[0]` is internal tracker playing state |
| `10000+` | active voice bookkeeping |
| `12000+` | MK3 hardware event ring buffer |
| `13000+` | Jam hardware event ring buffer |
| `14000+` | Jam LED state |
| `15000+` | mixer/mode/current-track state |
| `15200+` | newer mode flags |
| `15213` | Variation mode flag |
| `15300+` | audition/restart requests |
| `15400+` | MK3 action LED pulse counters |
| `15600+` | MK3 pad LED state / note map |
| `20000+` | tracker cell data |
| `88200+` | pattern/Arranger metadata |
| `90000+` | multi-pattern cell storage |
| `951000+` | Arranger sequence data |

### Event queues

MK3 and Jam use separate ring buffers. Each event occupies four numeric slots, allowing a compact event type plus three values. Keeping the queues separate prevents two independently running JSFX instances from racing on the same write pointer.

## Cell representation

A tracker step uses seven shared-memory slots. The exact layout is defined in source and kept stable for compatibility. Panning was removed from the feature set; its old slot is retained only as a reserved compatibility field so existing project/state layouts do not shift.

## Arranger timing

V0.45 introduced a musical-time edit anchor measured in 1/128-note master ticks. The key design rule is that **timeline position is independent of sequence-entry index**.

A track containing 8-step patterns and another containing 16-step patterns can therefore select different cell numbers while still referring to the same musical time. When the edit cursor moves vertically between tracks, the musical-time tick is preserved and the destination cell is resolved from that tick.

This fixed the earlier behaviour where changing track at "cell 2" could jump backward or forward in musical time when the two tracks used different pattern lengths.

## Pattern / REST / EMPTY semantics

Arranger cells are not all equivalent:

- **Pattern**: references one of the track's pattern slots and has timing from that pattern's length/division.
- **REST**: explicit silence with its own duration/division.
- **EMPTY**: no explicit material; the editor can present virtual empty time and materialise timing when a Pattern is inserted.

Clear semantics are intentionally staged:

```text
Pattern -> REST of equal duration -> EMPTY
```

## Variation mode

VARIATION is a hardware Trigger, not a Gate. Lua latches the mode on each press. This avoids relying on a release event and preserves the known-working V0.44 Controller Editor behaviour.

The Variation state flag is shared at address `15213`. A previous build omitted that assignment in the MIDI Engine; uninitialised JSFX variables evaluate to zero, so Variation writes landed in `gmem[0]` and accidentally toggled tracker playback. V0.45.3 fixed this by explicitly assigning `VARIATION_MODE_GMEM = 15213` everywhere.

## Display state transport

Lua periodically writes a compact text state file. The Python display process polls it, computes state signatures, rerenders only when required, and transmits full-screen images to the MK3.

This is intentionally decoupled from REAPER's audio callback.