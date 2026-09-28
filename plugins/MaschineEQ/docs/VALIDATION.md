# Validation results

## Individual mid-band validation

For Low-Mid at roughly 1000.3 Hz, +12 dB, Width 2, the Level 2 JUCE implementation was compared against the measured Maschine render.

After accounting for the measured one-sample active-EQ delay, representative results were:

- centre frequency: effectively identical, about 1000.3 Hz
- peak gain: approximately +12 dB in both
- magnitude-response RMS difference over 20 Hz–20 kHz: about **0.00024 dB**
- phase RMS difference: about **0.0018 degrees**
- normalised impulse residual energy: about **-85 dB**

## Width consistency across frequency

At displayed Width 2, +12 dB gain:

- around 249.9 Hz: fitted effective BW ~2.0500 octaves
- around 1000.3 Hz: fitted effective BW ~2.0501 octaves
- around 7999.5 Hz: fitted effective BW ~2.0499 octaves

This supports a frequency-independent Width mapping combined with the expected digital bandwidth correction.

## High-Mid validation

A High-Mid test around 3999.5 Hz, +12 dB, Width 2 produced approximately the same ~2.050-octave effective bandwidth model, confirming that High-Mid shares the Low-Mid topology/mapping.

## Shelf validation

### Low shelf

At approximately 499.9 Hz, +6 dB, +12 dB and -12 dB measurements all fitted a standard RBJ-style low shelf with fixed `S = 0.5` extremely closely.

### High shelf

At approximately 8001.6 Hz, +6 dB, +12 dB and -12 dB measurements similarly fitted a standard RBJ-style high shelf with fixed `S = 0.5`.

Using the literal displayed settings with `S = 0.5`, measured magnitude-fit errors were generally around a few ten-thousandths of a decibel RMS.

## Full-chain validation

A deliberately complex combined setting with all four bands active was rendered through Maschine and through the Level 2 JUCE recreation.

After correcting the test MIDI velocity to 127, representative full-chain results were:

- overall level difference: only a few thousandths of a decibel
- magnitude-response shape difference over 20 Hz–20 kHz: about **0.013 dB RMS**
- largest spectral deviation in the combined test: about **0.028 dB**
- time-domain residual: about **-54 dB** relative to the signal
- impulse timing aligned after modelling the one-sample enabled-EQ delay

The larger residual compared with isolated-band tests is consistent with cumulative parameter/display rounding, coefficient precision and small implementation differences across several simultaneously active sections.

## Practical validation in REAPER

The plug-in was also tested for:

- loading as a VST3
- real-time audio processing
- automation of all exposed controls
- saving and reopening REAPER projects
- parameter-state restoration
- transparent/flat behaviour with EQ gain controls at 0 dB aside from the intentional one-sample active-path delay

## Scope

These measurements validate the tested 44.1 kHz behaviour and parameter regions. They do not prove bit-identical implementation, and the project deliberately describes itself as a close approximation rather than an exact clone.
