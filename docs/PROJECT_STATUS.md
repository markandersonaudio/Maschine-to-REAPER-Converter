# Project status

## Overall

The project is in active reverse-engineering and prototype-development stages.

The long-term goal is a practical Maschine `.mxprj` to REAPER `.rpp` converter that preserves editable MIDI, automation, routing and plug-in state wherever technically possible.

## Confirmed areas of progress

### REAPER project generation

Prototype work has demonstrated automated REAPER project generation with instrument/effect instances and automation envelopes.

### VST3 automation identity mapping

Controlled tests have validated translation between Maschine automation targets and REAPER/VST3 parameters for multiple Native Instruments plug-ins.

### Komplete Kontrol hosting

Research has shown that hosted plug-in parameter mappings can differ from native plug-in parameter numbering and require a host-aware translation layer.

### Reaktor/Razor mapping

Controlled automation tests have validated specific Maschine-to-Reaktor parameter mappings, demonstrating that serialized Maschine automation targets can be resolved to REAPER-visible parameters with measurement and controlled comparison.

### Maschine sampler strategy

Experiments showed that physically pruning/removing referenced Pattern/Scene/Event structures from MXPRJ data can make projects unloadable or unstable. The safer current direction is to preserve load-safe object structure and neutralise unwanted arrangement/event content rather than breaking references.

### Internal FX recreation

The first completed internal-FX replacement is **Maschine EQ Unofficial Remake**, a JUCE/VST3 Level 2 measured approximation documented under `plugins/MaschineEQ`.

## Current priorities

1. Continue documenting MXPRJ structure and automation identities.
2. Improve repeatable plug-in state reconstruction.
3. Preserve Maschine-native sampler playback with minimal dependence on Maschine arrangement data.
4. Recreate additional Maschine-native effects where useful for conversion.
5. Integrate the replacement plug-ins and mapping data into the converter pipeline.

## Status labels

- **Measured/validated** — confirmed through controlled test.
- **Prototype** — working in at least one controlled case but not yet generalised.
- **Research** — behaviour observed but implementation not yet stable.
- **Hypothesis** — plausible interpretation requiring further testing.
