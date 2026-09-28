# Technical findings

## 1. Mid bands behave like conventional peaking biquads

Both Low-Mid and High-Mid measurements match a standard peaking-biquad response extremely closely when Maschine's Width control is treated as an octave-bandwidth-style parameter rather than as Q directly.

At a displayed Low-Mid frequency of about 1000.3 Hz, +12 dB gain, the fitted peak landed at approximately 1000.33 Hz and +12.000 dB.

## 2. Width is not Q

The original Level 1 implementation treated Width directly as Q. Measurements showed that this was substantially wrong.

Representative measured effective octave bandwidth / equivalent Q values at about 1 kHz were:

| Displayed Width | Effective BW (oct) | Equivalent Q |
| ---: | ---: | ---: |
| 0.1 | ~0.1000 | ~14.3748 |
| 0.5 | ~0.5290 | ~2.7026 |
| 1.0 | ~1.0360 | ~1.3583 |
| 2.0 | ~2.0501 | ~0.6453 |
| 4.0 | ~4.0001 | ~0.2652 |

The effective bandwidth remained essentially constant for a displayed Width of 2 when the centre frequency was moved from roughly 250 Hz to 1 kHz to 8 kHz. This indicates that the displayed-width mapping itself is frequency-independent; the corresponding digital Q changes with frequency as expected from the bandwidth-to-Q conversion.

## 3. Both shelves match a fixed RBJ shelf slope

The Low shelf measurements at approximately 500 Hz fit a standard RBJ-style shelf with fixed `S = 0.5` across +6 dB, +12 dB and -12 dB tests.

The High shelf at approximately 8 kHz showed the same result across +6 dB, +12 dB and -12 dB.

Representative fitted shelf slopes were within a few ten-thousandths of 0.5.

## 4. The enabled EQ path adds one sample of delay

With all EQ gains at 0 dB, the active Maschine EQ path was effectively transparent in magnitude but its impulse was shifted by one sample relative to bypass.

The JUCE recreation therefore intentionally includes a one-sample delay so that direct comparisons and converted timing match the measured behaviour.

## 5. A 6.23 dB mystery was MIDI velocity, not EQ gain

During full-chain validation, the Maschine render initially appeared roughly 6.23 dB lower than the JUCE render while retaining nearly the same spectral shape.

The Maschine trigger note had velocity 100. Re-rendering at velocity 127 removed the level discrepancy. Comparing the velocity-100 and velocity-127 Maschine renders accounted for essentially the whole offset.

This was a useful reminder that the measurement chain must be controlled as carefully as the DSP being measured.

## 6. Conventional building blocks can reproduce the measured response very closely

The interesting result of the project is not that Maschine's EQ required an exotic algorithm. The individual sections were modelled extremely closely using standard filter structures once the control mappings and shelf slope were identified.

The main reverse-engineering work was therefore discovering parameter semantics and small behavioural details rather than inventing unusual DSP.
