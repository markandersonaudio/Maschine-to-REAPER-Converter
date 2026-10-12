# Hardware mapping

The `.ncm3` and `.ncmj` files in `v0.45.5/` are the canonical Controller Editor mappings. This document records the important assignments so contributors can understand and reproduce them.

## Maschine MK3

### Required LED rule for Pads 1-16

On all eight pad pages:

- behaviour: Gate
- LED On: For MIDI In
- Color Mode: Indexed
- do not add `onIfDown="on"`

### Important controls

| Control | MIDI assignment | Behaviour / use |
|---|---:|---|
| VARIATION | CC87 | Trigger 127; software-latched Variation mode |
| EVENTS | CC85 | reserved/consumed; no tracker action in V0.45.5 |
| FOLLOW | CC56 | momentary modifier |
| PLAY | CC57 | tracker play |
| STOP | CC59 | tracker stop |
| 4-D Up | CC30 | navigation |
| 4-D Right | CC31 | navigation |
| 4-D Down | CC32 | navigation |
| 4-D Left | CC33 | navigation |
| Group A-H | CC98-105 | explicit pad-bank selection |
| small encoders 1-8 | CC70-77 | tracker parameter editing |

The exact controller template contains the rest of the mapping and should be treated as authoritative.

### Normal top buttons

| Button | Normal tracker | Arranger |
|---:|---|---|
| 1 | Previous 8 | previous musical-time cell |
| 2 | Next 8 | next musical-time cell |
| 3 | CMD previous | copy cell |
| 4 | CMD next | paste cell |
| 5 | unused | duplicate cell |
| 6 | default current step | insert REST |
| 7 | clear step | clear cell |
| 8 | clear pattern | clear track arrangement |

Modifier actions:

- FOLLOW + Top 1: Arranger zoom out
- FOLLOW + Top 2: Arranger zoom in
- FOLLOW + Top 6: default all non-note values in the active Pattern

### Variation pads

| Pad | V0.45.5 function |
|---:|---|
| 1 | random current-step probability 0-100, quantised to 10% |
| 2 | random current-step probability 50-100, quantised to 10% |
| 3 | random active-pattern probability 0-100, note-bearing steps only, quantised to 10% |
| 4 | random active-pattern probability 50-100, note-bearing steps only, quantised to 10% |
| 5 | random current-step velocity 60-127 |
| 6 | random current-step velocity 1-59 |
| 7 | random active-pattern velocity 60-127, note-bearing steps only |
| 8 | random active-pattern velocity 1-59, note-bearing steps only |
| 9 | regenerate active Pattern at 25% note density |
| 10 | regenerate active Pattern at 50% note density |
| 11 | regenerate active Pattern at 75% note density |
| 12 | regenerate active Pattern at 100% note density |
| 13 | Bias: OFF -> ROOT/OCT -> ROOT+5TH -> ROOT+4TH+5TH -> OFF |
| 14 | Bias Weight: OFF when Bias is OFF; otherwise 25 -> 50 -> 75 -> 100 -> 25 |
| 15 | octave span down; minimum 1 octave |
| 16 | octave span up; maximum 2 octaves |

Bias safety behaviour:

- Bias OFF => Pad 13 and Pad 14 LEDs off; Weight OFF/0%.
- Selecting a Bias while Weight is OFF immediately sets Weight to 25%.
- 0% is not selectable while Bias is active.
- Cycling Bias back to OFF resets Weight to OFF/0%.

## Maschine Jam

### Matrix and track controls

| Control | Mapping |
|---|---:|
| 8x8 matrix | MIDI Notes 0-63 |
| Scene 1-8 | Notes 64-71 |
| Group A-H | Notes 72-79 |
| Touch strips 1-8 | CC48-55 |

### Function controls

The Jam template and bridge use dedicated CCs for song/select/mute/solo/duplicate/direct-note/note-repeat/touch modes. See `Maschine_Tracker_Jam_Bridge_V0455.jsfx` for the complete canonical list.

The D-pad must remain:

```text
Up    CC21
Left  CC22
Right CC23
Down  CC24
```

### LED behaviour

Jam feedback is written from Lua into shared-memory LED tables and emitted by the Jam bridge only when values change. Indexed Controller Editor colours are used to preserve the track colour identity.