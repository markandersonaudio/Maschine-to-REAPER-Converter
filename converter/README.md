# Maschine → REAPER Converter

Current public mainline: **v0.20.5**

This directory contains the experimental Python converter that reads Native Instruments Maschine `.mxprj` data and generates editable REAPER `.rpp` projects.

The project is intended for interoperability research and practical project migration. It is **not** an official Native Instruments or Cockos tool, and the MXPRJ format is undocumented and may change between Maschine versions.

## Current version

- Source: [v0.20.5/Maschine_Converter_v0205.py](v0.20.5/Maschine_Converter_v0205.py)
- Release notes: [v0.20.5/RELEASE_NOTES.txt](v0.20.5/RELEASE_NOTES.txt)
- Automation route profiles: [v0.20.5/Maschine_Automation_Route_Profiles.json](v0.20.5/Maschine_Automation_Route_Profiles.json)
- REAPER parameter probe: [v0.20.5/Probe_All_Instrument_Parameter_Identities_AB01.lua](v0.20.5/Probe_All_Instrument_Parameter_Identities_AB01.lua)
- Fallback automation carrier: [v0.20.5/Maschine_Automation_Carrier.jsfx](v0.20.5/Maschine_Automation_Carrier.jsfx)

v0.20.5 deliberately keeps the user-validated v0.20.4 conversion engine and adds clearer reporting of categories that were **not attempted**.

## What v0.20.5 currently converts

### Project structure and MIDI

The converter has working support for the serialization families used by the tested Maschine projects, including dynamic variants of:

- NoteEvent
- EventList
- Pattern
- Scene / Scene slot
- Section

It reconstructs decoded Pattern note data into REAPER MIDI items and has been tested with mixed EventLists containing both notes and automation events.

The converter also reconstructs the tested Scene/Section arrangement path and repeats Pattern-local content at its arranged occurrences. Current arrangement reporting is expressed in 4/4 bars; arbitrary time-signature-map reconstruction should not be assumed.

### Maschine Sampler — stable path

The stable v0.20.5 Sampler path is the earlier **one-zone transfer** route. It can detect multiple Maschine internal Sampler Sounds and transfer the currently proven fields:

- sample path
- sample start/end
- loop start/end
- loop enabled
- loop crossfade
- polyphony
- sampler engine mode
- reverse
- Tune
- amp-envelope Attack / Hold / Decay / Sustain / Release

The newer experiments that preserve a chopped/sliced Maschine Sampler inside Maschine VST are research work and are **not integrated into v0.20.5**.

### External VST3 instruments

For supported/tested projects the converter can:

- detect external VST3 instrument instances
- reconstruct native VST3 instances in REAPER
- restore captured VST3 component state
- use exact VST3 parameter identities rather than guessing from parameter names
- optionally reconstruct an instrument inside Komplete Kontrol when a matching user-supplied host shell is available

Validated research examples include Native Instruments Massive and Reaktor/Razor, plus Komplete Kontrol-hosted Massive.

### Automation

The converter can decode tested `FloatModulationEvent` automation and reproduce Pattern-local automation in REAPER.

Important behaviours already implemented include:

- repetition of Pattern-local automation at each arranged Pattern occurrence
- Pattern-start reset/hold behaviour
- end-of-Pattern hold handling to avoid unintended long interpolation ramps
- exact VST3 parameter-identity mapping where a compatible probe is available
- conservative handling when an automation destination cannot be proven
- optional verified alternate routing profiles, such as a known MIDI-CC destination
- a 64-lane JSFX automation carrier for unresolved/diagnostic lanes

The design intentionally avoids binding automation to a parameter merely because its label looks similar.

## What v0.20.5 does **not** attempt

The application shows these omissions after conversion in a scrollable **NOT ATTEMPTED BY THIS BUILD** report. The same list is written to the conversion log and the generated `.automation.json`.

Current exclusions include:

- external VST3 insert, Group and Master effect-chain reconstruction
- Maschine-native instruments other than the stable Sampler path, including Drum Synth and Bass Synth
- Maschine-native Sound / Group / Master insert effects
- Perform FX and touch-strip behaviour
- Audio Clips / audio-loop arrangement content as REAPER media items
- Clip automation
- general sliced/chopped and arbitrary multi-zone Sampler reconstruction
- comprehensive mixer state
- sends
- sidechains
- arbitrary routing outside the specifically validated routing behaviour/profile
- Maschine Host/Auto automation assignments
- non-VST3 external plug-ins such as VST2
- general arbitrary time-signature reconstruction

An omission in this list means **the converter deliberately did not try to reconstruct that category**. It is different from a conversion error.

## Important current research findings

These findings are useful to developers extending the project:

- Serialized MXPRJ automation target numbers are not, in general, native VST/VST3 parameter indices.
- Exact native VST3 parameter identities are substantially safer than name-based or ordinal guesses.
- Reaktor-hosted instruments require explicit mapping; a serialized Maschine control target cannot be assumed to equal a Reaktor parameter index.
- Komplete Kontrol adds its own host parameter layer. In the tested Massive case, the hosted Massive parameter block was dynamically identified rather than assumed globally.
- Maschine Sampler Tune uses a different internal scaling from the host-visible normalized parameter representation in the controlled sampler test.
- Removing Pattern/Scene/EventList objects from MXPRJ files by direct binary surgery caused invalid projects/crashes in testing. Preserving the serialization graph and neutralizing data was safer.
- Maschine Host Automation assignments proved unsuitable as a stable converter dependency in testing: the Tune Host assignment reverted to MIDI after a normal REAPER project save/reload.
- A possible future migration route is real-time MIDI capture from Maschine VST while REAPER records the sequencer output; this remains experimental and is not part of v0.20.5.

## Requirements

- Windows is the primary tested platform.
- Python 3 with Tkinter.
- REAPER.
- The relevant plug-ins installed locally if the generated project is expected to instantiate them.
- A suitable REAPER/Komplete Kontrol template for the stable Sampler path.
- Parameter-identity probe files when direct automation binding requires them.

The Python converter itself uses only the Python standard library.

## Running

Run:

```text
python Maschine_Converter_v0205.py
```

Then choose:

1. the source Maschine `.mxprj`;
2. the required REAPER/Komplete Kontrol template;
3. the output `.rpp` path.

The converter writes a REAPER project plus diagnostic information, including an automation report.

## Parameter probes

The included REAPER Lua probe writes instrument parameter identity reports to:

```text
Desktop\IndexProbes
```

These reports are used to establish exact REAPER/VST3 parameter identities for automation research.

Large machine-generated probe dumps and captured Native Instruments host-state shells are intentionally not committed here. Developers should generate those from their own installed plug-ins.

## Development principles

Contributions should distinguish clearly between:

- observed behaviour
- reproducible measurements
- confirmed mappings
- inferred mappings
- unverified hypotheses

Please do not add Native Instruments binaries, presets, artwork, firmware, or captured proprietary state to the repository.

## Licence

The original converter code and support scripts in this `converter/` directory are released under the [MIT License](LICENSE).

This licence applies to the project's own converter code. It does not grant rights to Native Instruments, Cockos, JUCE, or other third-party software, plug-ins, content, trademarks, presets, or captured proprietary state.
