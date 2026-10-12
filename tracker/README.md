# Maschine Tracker for REAPER

An unofficial, open-source tracker workflow for **Native Instruments Maschine MK3 + Maschine Jam + REAPER**.

The project turns the MK3 into a hardware tracker/sequencer inspired by dedicated tracker hardware while keeping the sound engine inside REAPER. The current public build is **V0.45.5**.

> [!IMPORTANT]
> This is an independent interoperability/research project. It is not affiliated with, endorsed by, sponsored by, or made with permission from Native Instruments or Cockos.

## What it does

Maschine Tracker provides:

- 16 tracker tracks routed as MIDI channels 1-16
- 16 patterns per track
- up to 512 tracker steps per pattern
- variable pattern length and step division
- per-step note, velocity, length, probability, command and value data
- real-time tracker playback without requiring REAPER transport to run
- note audition and live pad entry from Maschine MK3
- Maschine Jam step editing, track selection, mute/solo and touch-strip editing
- two-screen MK3 UI rendered by a Python USB display companion
- MK3/Jam LED feedback through JSFX MIDI bridges
- scale lock and scale-aware note generation
- multi-pattern song arranger with Pattern / REST / EMPTY cells
- horizontal and vertical Arranger views
- LOOP SONG and TRUE SONG modes
- Undo/Redo for tracker and Arranger edits
- Variation mode for probability, velocity, note-density, pitch-bias and octave-range generation
- pattern-to-MIDI writing into REAPER

## Current release

The exact current build is stored in [`v0.45.5/`](v0.45.5/).


V0.45.5 preserves the important V0.45.3 playback fix (`VARIATION_MODE_GMEM = 15213`), V0.45.4 probability quantisation to 10% increments, and V0.45.5 Bias/Weight safety behaviour.

## Project files

The release contains five cooperating runtime components plus two Controller Editor templates:

| File | Purpose |
|---|---|
| `Maschine_Tracker_V0455.lua` | Main REAPER tracker/editor, project state, Arranger, GUI and hardware logic |
| `Maschine_Tracker_MIDI_Engine_V0455.jsfx` | Real-time tracker playback, MIDI input handling, note lifetime and MK3 event bridge |
| `Maschine_Tracker_MK3_LED_Bridge_V0455.jsfx` | MK3 LED feedback |
| `Maschine_Tracker_Jam_Bridge_V0455.jsfx` | Jam input + LED/touch-strip feedback |
| `Maschine_Tracker_Display_V0455.py` | Direct USB renderer for the two MK3 colour displays |
| `Maschine_Tracker_V0455_MK3.ncm3` | Controller Editor template for MK3 |
| `Maschine_Tracker_V0455_Jam.ncmj` | Controller Editor template for Maschine Jam |

The original V0.45.5 setup, validation, Controller Editor and test documents are retained unchanged inside [`v0.45.5/Documentation/`](v0.45.5/Documentation/).

## Quick start

See [`docs/SETUP.md`](docs/SETUP.md) for the full setup.

At a high level:

1. Import the included MK3 Controller Editor template.
2. Import the Jam template if using Maschine Jam.
3. Put the MIDI Engine JSFX on a REAPER tracker-source track that receives the MK3 Controller Editor MIDI port.
4. Route tracker MIDI channels 1-16 to your instrument tracks.
5. Add the MK3 LED Bridge to a track whose MIDI hardware output is the MK3 Controller Editor port.
6. Add the Jam Bridge similarly if using Jam.
7. Run `Maschine_Tracker_V0455.lua` in REAPER.
8. Run `Maschine_Tracker_Display_V0455.py` to drive the MK3 screens.

## Architecture

The tracker intentionally separates hard-real-time MIDI work from editing/display work:

```text
Maschine MK3 / Jam
        |
        | MIDI
        v
   JSFX bridges  <------>  REAPER gmem  <------>  Lua tracker/editor
        |                                           |
        | tracker MIDI                              | state-file snapshot
        v                                           v
 REAPER instrument tracks                    Python display process
                                                    |
                                                    | USB bulk image packets
                                                    v
                                             MK3 left/right screens
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing shared-memory addresses or event formats.

## Reverse engineering

Some hardware interoperability work was required, principally for:

- direct MK3 colour-display output over USB
- Controller Editor MIDI/LED behaviour
- indexed pad/Jam LED feedback
- physical control mapping and mode interaction
- avoiding conflicts between the JSFX/Lua shared-memory protocol and tracker playback state

The findings, what was actually observed, and what remains implementation-specific are documented in [`docs/REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md).

No Native Instruments source code, firmware, binaries, artwork, presets or proprietary DSP code are included.

## Hardware mapping

See [`docs/HARDWARE_MAPPING.md`](docs/HARDWARE_MAPPING.md). The `.ncm3` and `.ncmj` files remain the canonical executable mappings; the Markdown documentation is intended to make them understandable and reproducible.

## Development

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) and [`docs/TESTING.md`](docs/TESTING.md).

Important compatibility rules include:

- MK3 Pad 1-16 controls must stay **Gate** with **LED On: For MIDI In** and **Color Mode: Indexed**.
- `VARIATION` is **CC87 / Trigger / 127** and is latched in software.
- `VARIATION_MODE_GMEM` is **15213**. Do not reuse address 0; `gmem[0]` is the internal tracker playback flag.
- Jam D-pad is Up=CC21, Left=CC22, Right=CC23, Down=CC24.
- Do not casually change shared-memory addresses; the Lua and all JSFX components must agree.

## Status / roadmap

V0.45.5 is the latest built release represented here. One later UI request is deliberately documented but **not silently applied** to this build: show the current Pad 13 Bias value inside Pad 13's on-screen Variation tile rather than off to the side. See [`ROADMAP.md`](ROADMAP.md).

## Contributing

Contributions are welcome. Useful areas include:

- cleaner installation and REAPER template automation
- Linux/macOS display backend testing
- additional hardware testing/documentation
- Arranger workflows
- UI improvements
- protocol cleanup and stronger automated tests
- alternative display implementations

Please keep reverse-engineering work reproducible and clearly distinguish observed facts from assumptions.

## Licence

Project-authored source code and documentation in this `tracker/` component are released under the **MIT License**. See [`LICENSE`](LICENSE).

Native Instruments software, firmware, trademarks, Controller Editor itself and other third-party material are not covered by this licence. The included `.ncm3`/`.ncmj` files are user-created Controller Editor configuration templates and require Native Instruments software/hardware to use.