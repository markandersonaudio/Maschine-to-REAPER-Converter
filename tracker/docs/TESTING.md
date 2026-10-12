# Testing and regression checklist

The original V0.45.5 checklist is preserved at:

`../v0.45.5/Documentation/Maschine_Tracker_V0455_Test_Checklist.txt`

This page highlights the highest-risk regressions.

## Load/compile

- Lua loads in REAPER without runtime errors.
- all three JSFX compile in REAPER.
- Python display script imports/starts.
- MK3 and Jam templates import into Controller Editor.

## Playback

- tracker PLAY starts tracker playback only.
- STOP stops tracker playback.
- note lengths release correctly; no stuck notes.
- REAPER transport is independent unless the user deliberately starts it.
- VARIATION does not start, stop, restart or pause tracker playback.

## Variation regression

- `VARIATION_MODE_GMEM` is exactly 15213 in Lua, MIDI Engine and LED Bridge.
- CC87 remains Trigger 127.
- mode toggles once per press.
- probability Pads 1-4 produce only 0,10,...,100.
- Bias OFF => Bias and Weight LEDs off.
- first active Bias automatically gives Weight 25%.
- active Weight cycle excludes 0%.
- Bias OFF resets Weight OFF/0%.

## Arranger timing

Create at least two tracks with different pattern lengths, for example 8 and 16 steps.

- moving vertically preserves musical time;
- selected cell number may differ between tracks;
- empty time is selectable at the same tick;
- inserting a Pattern starts it exactly at that tick;
- cursor advances to exact Pattern end;
- REST/EMPTY clear semantics are unchanged;
- LOOP SONG and TRUE SONG remain distinct.

## LEDs

- MK3 Pads 1-16 show tracker colours in normal mode.
- live/selected/active states do not leave stuck LEDs.
- all pad pages use For MIDI In + Indexed.
- Jam matrix, Scene and Group feedback update.

## Display

- both 480x272 screens update.
- normal tracker screen remains readable.
- Arranger horizontal/vertical screens follow the musical-time cursor.
- Variation screen shows all 16 functions.
- V0.45.5 Pad 13 Bias value is still displayed according to the release implementation; the later requested in-tile value change belongs to a future build.