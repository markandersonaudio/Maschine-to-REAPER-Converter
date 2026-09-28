# Measurement and modelling methodology

## Objective

The Level 2 goal was not merely to build a four-band EQ with similar controls. The goal was to measure the behaviour of Maschine's internal EQ and fit an independent implementation closely enough for use as a project-conversion replacement.

## Test environment

- Sample rate: **44.1 kHz**
- Test stimulus: stereo 24-bit PCM impulse
- Impulse level: **-24 dBFS**
- Impulse position: **100 ms** into the test file
- Wet and dry paths kept identical except for the EQ under test
- Normalisation disabled
- Maschine sampler/MIDI playback used to trigger the impulse

An important practical lesson was that the MIDI note must be present and should use velocity 127 for level-comparable renders. Several silent files during testing were caused by forgetting to trigger the sample; a later apparent level mismatch was traced to velocity 100 versus 127 rather than EQ behaviour.

## Baseline

A dry render was captured with the EQ bypassed. Wet renders were compared against the dry response so that sampler/playback-path behaviour could be separated from the EQ response.

## Mid-band characterisation

The Low-Mid band was first measured around 1 kHz at +12 dB with multiple displayed Width values:

- 0.1
- 0.5
- 1.0
- 2.0
- 4.0

The measured magnitude responses were fitted against conventional peaking-biquad models.

Width was then tested at widely separated centre frequencies (approximately 250 Hz, 1 kHz and 8 kHz) to determine whether the mapping changed with frequency.

The High-Mid band was independently checked around 4 kHz to determine whether it shared the same topology and Width mapping.

## Shelf characterisation

The Low shelf was measured around 500 Hz at +6 dB, +12 dB and -12 dB. The fits were compared using the standard RBJ shelf formulation.

The High shelf was similarly tested around 8 kHz at +6 dB, +12 dB and -12 dB.

## Delay measurement

A render with all EQ gains at 0 dB but the effect enabled was compared with the bypassed dry render. The enabled EQ path consistently introduced a one-sample delay.

## Combined validation

After modelling each section independently, a deliberately complex four-band setting was rendered through both Maschine and the JUCE implementation. The full-chain frequency response, timing and residual were compared.

This combined test checks parameter mappings and cumulative behaviour rather than only isolated filters.

## Interpretation discipline

The documentation distinguishes measured behaviour, model fits, implementation choices and hypotheses.

The implementation is intentionally independent. No Native Instruments code was extracted or copied.
