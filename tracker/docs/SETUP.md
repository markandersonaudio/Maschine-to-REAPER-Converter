# Setup

## Requirements

- REAPER
- Native Instruments Maschine MK3
- Native Instruments Controller Editor
- optional: Maschine Jam
- Python 3 for the display companion
- Python packages: `pyusb`, `libusb-package`, `pillow`

## 1. Controller Editor

Import:

- `v0.45.5/Maschine_Tracker_V0455_MK3.ncm3`
- optionally `v0.45.5/Maschine_Tracker_V0455_Jam.ncmj`

Important MK3 checks:

- VARIATION = CC87, Trigger, value 127, LED For MIDI In
- Pad LEDs = For MIDI In + Indexed
- 4-D directions = Up CC30 / Right CC31 / Down CC32 / Left CC33

Important Jam check:

- D-pad = Up CC21 / Left CC22 / Right CC23 / Down CC24

## 2. REAPER tracker-source track

Create a track that receives the Maschine MK3 Controller Editor MIDI input.

Insert:

```text
Maschine Tracker MIDI Engine V0.45.5
```

This track is the tracker event/playback source.

Route its outgoing MIDI channels to the corresponding instrument tracks:

```text
Tracker channel 1  -> instrument track 1
Tracker channel 2  -> instrument track 2
...
Tracker channel 16 -> instrument track 16
```

The destination instruments may be Komplete Kontrol, Kontakt, Massive, Massive X or any other MIDI-controlled VSTi. The tracker itself does not require a particular sound source.

## 3. MK3 LED feedback track

Create a separate REAPER track containing:

```text
Maschine Tracker MK3 LED Bridge V0.45.5
```

Set its MIDI hardware output to the Maschine MK3 Controller Editor port.

## 4. Maschine Jam bridge

If using Jam, create another track with:

```text
Maschine Tracker Jam Bridge V0.45.5
```

Configure:

- MIDI input: Maschine Jam Controller Editor port / all channels
- record arm: on
- monitoring: on
- MIDI hardware output: Maschine Jam Controller Editor port

## 5. Main Lua tracker

Install/run:

```text
v0.45.5/Maschine_Tracker_V0455.lua
```

through REAPER's ReaScript system.

## 6. MK3 display companion

Install Python dependencies:

```text
python -m pip install pyusb libusb-package pillow
```

Then run:

```text
python Maschine_Tracker_Display_V0455.py
```

The display process searches for USB VID/PID `17CC:1600`, claims interface 5 and writes to endpoint `0x04`.

On Windows, direct USB access may require an appropriate libusb-compatible driver arrangement for the relevant interface. Changing USB drivers can affect Native Instruments software, so test carefully and keep a rollback path.

## 7. First test

Before building a full project:

1. Start the Lua tracker and Python display.
2. Enter one note on Track 1.
3. Start **tracker** playback with the MK3 PLAY button.
4. Verify that REAPER transport itself does not need to run.
5. Verify the note stops according to LEN.
6. Verify pad LEDs and both MK3 screens update.
7. Toggle VARIATION twice and confirm tracker playback is not affected.

See [`TESTING.md`](TESTING.md) for the full regression checklist.