# Maschine EQ Unofficial Remake

An independent JUCE/VST3 approximation of the EQ effect built into Native Instruments Maschine.

This plug-in is a subproject of the larger [Maschine to REAPER Converter](../../README.md). Its purpose is to provide a REAPER-side replacement for a Maschine-native effect when converting projects while preserving editable parameters and automation.

> [!IMPORTANT]
> This is an unofficial, independent black-box reimplementation. It is not affiliated with, endorsed by, sponsored by, or made with permission from Native Instruments.

## Status

**Level 2 release candidate — frozen for validation/documentation.**

The current build has been tested in REAPER for:

- audio processing
- automation
- project save/reload and parameter state restoration
- transparent response with EQ gain controls at 0 dB, apart from the intentionally modelled one-sample enabled-EQ delay
- combined multi-band response against Maschine measurements

## Parameters

The plug-in exposes 11 automatable parameters in the order used for the converter mapping:

1. Low Freq
2. Low Gain
3. Low-Mid Freq
4. Low-Mid Gain
5. High-Mid Freq
6. High-Mid Gain
7. High Freq
8. High Gain
9. Low-Mid Width
10. High-Mid Width
11. Output Gain

See [`docs/PARAMETER_MAP.md`](docs/PARAMETER_MAP.md) for ranges, defaults and stable IDs.

## DSP summary

- Low: RBJ-style low shelf with measured fixed shelf slope `S = 0.5`
- Low-Mid: peaking biquad using measured Maschine Width-to-octave-bandwidth mapping
- High-Mid: same peaking-biquad model and Width mapping
- High: RBJ-style high shelf with measured fixed shelf slope `S = 0.5`
- Output Gain: flat gain stage
- Enabled effect path: one-sample delay, based on measurement

## Documentation

- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — how the effect was measured and modelled
- [`docs/FINDINGS.md`](docs/FINDINGS.md) — interesting technical findings
- [`docs/VALIDATION.md`](docs/VALIDATION.md) — measured validation results
- [`docs/PARAMETER_MAP.md`](docs/PARAMETER_MAP.md) — exposed parameter definitions

## Build

The current project was developed with JUCE and built as a Windows VST3 using a Visual Studio toolchain.

The original development setup used the Visual Studio 2026 Projucer exporter retargeted to the v143 platform toolset. Other supported JUCE build configurations may work but have not yet been documented here.

`juce_dsp` is required.

## Legal

No Native Instruments source code, binaries, firmware, artwork, presets, or proprietary code are included in or copied into this project.

Maschine and Native Instruments are names and trademarks associated with Native Instruments. This independent project is not affiliated with Native Instruments.

## Licence

See [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
