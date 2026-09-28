# Maschine to REAPER Converter

An open-source research and development project aimed at converting Native Instruments Maschine projects into editable REAPER projects while preserving as much MIDI, automation, routing, plug-in state, and arrangement information as possible.

The long-term goal is to make it practical to begin a song in Maschine and continue or finish it in REAPER without manually rebuilding the project or relying heavily on rendered audio.

> [!IMPORTANT]
> This is an independent, unofficial project. It is not affiliated with, endorsed by, sponsored by, or made with permission from Native Instruments or Cockos.

## Project goals

The converter is intended to reconstruct a Maschine project in REAPER with a preference for editable data over rendered audio.

Target areas include:

- arrangement and song structure
- MIDI and note events
- automation
- VST/VST3 instrument and effect instances
- plug-in state and preset reconstruction where technically possible
- routing
- Maschine-native samplers and effects
- translation of Maschine-native devices into suitable REAPER-side replacements where required

The ideal flow is:

```text
Maschine .mxprj
        |
        v
Project parser
        |
        +-- Arrangement
        +-- MIDI / note events
        +-- Automation
        +-- Plug-in states
        +-- Routing
        +-- Maschine-native devices
                 |
                 v
        Translation / replacement layer
                 |
                 +-- Native VST/VST3 recreation
                 +-- Maschine VST container where useful
                 +-- Other conversion strategies
        |
        v
Editable REAPER .rpp
```

## Current status

This repository is under active development.

### Internal FX recreation

- [x] **Maschine EQ Unofficial Remake** — Level 2 measured approximation
- [ ] Additional Maschine internal effects

The EQ is the first completed replacement plug-in and lives in [`plugins/MaschineEQ`](plugins/MaschineEQ).

### Converter research

Work is also being carried out on:

- MXPRJ structure and safe modification
- automation-target mapping
- VST3 parameter identity mapping
- Native Instruments plug-in state restoration
- Komplete Kontrol hosting
- Reaktor-hosted instrument mapping
- Maschine sampler preservation and translation
- REAPER project generation

## Repository layout

```text
Maschine-to-REAPER-Converter/
|
+-- converter/                  Main converter work
+-- plugins/                    Replacement plug-ins for Maschine-native devices
|   +-- MaschineEQ/             First completed internal-FX recreation
+-- research/                   Reverse-engineering notes and experiments
+-- docs/                       Project-wide architecture/status documentation
+-- LICENSES/                   Licensing notes for repository components
+-- README.md
+-- CHANGELOG.md
```

## Maschine EQ Unofficial Remake

The EQ subproject is an independently written approximation of Maschine's built-in EQ. It was developed using black-box signal measurement and comparison rather than copied Native Instruments code.

The current Level 2 model uses:

- measured low-shelf behaviour
- measured low-mid peaking behaviour
- measured high-mid peaking behaviour
- measured high-shelf behaviour
- measured width-to-bandwidth behaviour
- measured one-sample enabled-EQ delay
- a compact Maschine-inspired interface
- 11 automatable parameters with state recall

See [`plugins/MaschineEQ/README.md`](plugins/MaschineEQ/README.md) and its [`docs`](plugins/MaschineEQ/docs) directory for the full methodology and validation notes.

## Research philosophy

Where possible, this project documents not only working code but also how conclusions were reached.

Reverse-engineering notes should distinguish between:

- directly observed behaviour
- measured results
- fitted models
- implementation assumptions
- unverified hypotheses

The aim is to make results reproducible and useful to other developers rather than treating the implementation as a black box.

## Contributing

Contributions, independent measurements, corrections, documentation improvements, and alternative implementations are welcome.

Please keep compatibility research reproducible where possible and avoid submitting proprietary Native Instruments code, binaries, artwork, presets, firmware, or other copyrighted assets.

## Legal and trademarks

This repository is an independent interoperability and research project.

No Native Instruments source code, binaries, firmware, artwork, presets, or proprietary code are intended to be included unless their redistribution is explicitly permitted.

Maschine and Native Instruments are names and trademarks associated with Native Instruments. REAPER is associated with Cockos Incorporated. All trademarks remain the property of their respective owners.

## Licensing

There is intentionally no single blanket licence covering every future component of this repository.

Individual components may have their own licence depending on their dependencies and implementation. See [`LICENSES/README.md`](LICENSES/README.md) and the licence file inside each component directory.

The JUCE-based Maschine EQ subproject is intended to be distributed under terms compatible with the JUCE open-source licence used to build it.
