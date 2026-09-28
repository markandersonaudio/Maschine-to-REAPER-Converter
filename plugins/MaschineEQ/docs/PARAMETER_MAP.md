# Parameter map

The VST3 exposes parameters in a fixed order intended to remain stable for the Maschine-to-REAPER converter.

| # | Parameter ID | Name | Range | Default | Notes |
| ---: | --- | --- | --- | ---: | --- |
| 1 | `lowFreq` | Low Freq | 20–8000 Hz | 248 Hz | logarithmic-style control scaling |
| 2 | `lowGain` | Low Gain | -20 to +20 dB | 0 dB | low shelf gain |
| 3 | `lowMidFreq` | Low-Mid Freq | 40–16000 Hz | 630 Hz | logarithmic-style control scaling |
| 4 | `lowMidGain` | Low-Mid Gain | -20 to +20 dB | 0 dB | peaking gain |
| 5 | `highMidFreq` | High-Mid Freq | 40–16000 Hz | 1970 Hz | logarithmic-style control scaling |
| 6 | `highMidGain` | High-Mid Gain | -20 to +20 dB | 0 dB | peaking gain |
| 7 | `highFreq` | High Freq | 50–20000 Hz | 2610 Hz | logarithmic-style control scaling |
| 8 | `highGain` | High Gain | -20 to +20 dB | 0 dB | high shelf gain |
| 9 | `lowMidWidth` | Low-Mid Width | 0.1–4.0 | 2.0 | mapped to measured effective octave bandwidth |
| 10 | `highMidWidth` | High-Mid Width | 0.1–4.0 | 2.0 | same measured mapping |
| 11 | `outputGain` | Output Gain | -20 to +20 dB | 0 dB | flat post-EQ gain |

## Stable IDs

The string IDs above should be treated as stable public identifiers. Converter automation and saved project state may depend on them.

## DSP interpretation

- `lowFreq` / `lowGain`: RBJ-style low shelf, fixed `S = 0.5`
- Low-Mid: peaking biquad with measured Width mapping
- High-Mid: peaking biquad with the same measured Width mapping
- `highFreq` / `highGain`: RBJ-style high shelf, fixed `S = 0.5`
- `outputGain`: flat gain

## Width mapping

The current Level 2 code uses a smooth fitted mapping through the measured displayed-width points before converting octave bandwidth to Q with the RBJ bandwidth formula.

Measured anchor points:

| Displayed Width | Effective BW (octaves) |
| ---: | ---: |
| 0.1 | ~0.1000 |
| 0.5 | ~0.5290 |
| 1.0 | ~1.0360 |
| 2.0 | ~2.0501 |
| 4.0 | ~4.0000 |
