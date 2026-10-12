# Changelog

## Unreleased

### Maschine → REAPER Converter
- Published the current testable mainline, **v0.20.5**, under `converter/v0.20.5`.
- Added documentation of validated capabilities, known limitations, and current reverse-engineering findings.
- Added explicit post-conversion **NOT ATTEMPTED BY THIS BUILD** reporting for unsupported project categories.
- Added diagnostic detection/reporting of external VST3 FX without claiming to reconstruct their chains.
- Published automation route-profile, parameter-probe, and JSFX carrier support files.
- Licensed the original converter code and support scripts in `converter/` under MIT.
- Deliberately excluded captured Native Instruments host-state shells and large machine-generated probe dumps from the public repository.
### Repository
- Created the umbrella Maschine-to-REAPER Converter repository.
- Added the Maschine EQ Unofficial Remake as the first completed internal-FX subproject.

### Maschine EQ Unofficial Remake
- Frozen Level 2 release-candidate DSP and GUI.
- Four-band EQ recreation validated by controlled impulse-response measurements.
- Added independent-project, no-warranty, open-source and non-affiliation information to the plug-in UI.
