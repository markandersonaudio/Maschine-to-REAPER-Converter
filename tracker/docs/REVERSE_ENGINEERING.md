# Reverse-engineering and interoperability notes

## Scope and method

Maschine Tracker was developed as an interoperability project using public/user-accessible behaviour, MIDI messages, Controller Editor configurations, USB-device inspection and black-box hardware testing.

No Native Instruments source code, firmware, proprietary application code or copied internal algorithms are used by this tracker.

The notes below distinguish **observed hardware facts** from **project implementation choices**.

## Maschine MK3 display transport

### Observed / hardware-tested

The current display companion communicates with the MK3 identified as:

- USB Vendor ID: `0x17CC`
- USB Product ID: `0x1600`
- USB configuration: 1
- display interface: 5
- write endpoint: `0x04`
- two screens: index 0 and 1
- screen dimensions: `480 x 272`
- pixel format: RGB565, one 16-bit word per pixel, **big-endian**

A working full-screen packet is:

```text
8 bytes   header
4 bytes   x/y coordinates
4 bytes   width/height
4 bytes   transfer command
261120 B  480*272*2 RGB565 pixel payload
4 bytes   blit command
4 bytes   end command
```

For a full-screen transfer the current implementation uses:

```text
header:   84 00 <screen> 60 00 00 00 00
x/y:      00 00 00 00
size:     01 E0 01 10       # 480, 272 big-endian
transfer: 00 00 FF 00
blit:     03 00 00 00
end:      40 00 <screen> 00
```

The complete packet length is `28 + 480*272*2 = 261148` bytes.

This layout was verified by sending rendered test images and then used for the normal tracker interface. The implementation comments also note compatibility with the working MMK3-HID-Control image path, but this project performs its own packet construction.

### Project choice

The display is driven from Python with PyUSB/libusb and Pillow. That is not a claim about how Native Instruments' own software renders the displays; it is simply the transport that proved reliable for this project.

## Controller Editor / MIDI mapping

### Observed / hardware-tested

The tracker uses Controller Editor to expose hardware controls as ordinary MIDI events and to receive LED feedback.

A particularly important MK3 pad rule was established through repeated hardware testing:

- Pad 1-16: `behavior=gate`
- no `onIfDown="on"` attribute
- LED On: **For MIDI In**
- Color Mode: **Indexed**

Changing those details caused pad-light behaviour to stop matching the tracker, so the template is treated as canonical configuration data.

For VARIATION, the known-working physical mapping is:

```text
CC87 / Trigger / value 127 / LED For MIDI In
```

V0.45 initially changed Variation to Gate so a release message could be used. On the tested setup this introduced playback/restart behaviour. Returning to the V0.44 Trigger behaviour and latching Variation in software removed the need for a release event.

### Maschine Jam mappings verified in the project

- matrix: MIDI Notes `0-63`
- Scene 1-8: Notes `64-71`
- Group A-H: Notes `72-79`
- touch strips: CC `48-55`
- D-pad: Up=CC21, Left=CC22, Right=CC23, Down=CC24

LED feedback uses the indexed colour values expected by the Controller Editor templates.

## MIDI/LED bridge architecture

The MIDI mapping itself is not a proprietary protocol: Controller Editor presents ordinary MIDI notes/CCs. The reverse-engineering work was primarily discovering a stable assignment and LED-return arrangement that behaves consistently on the hardware.

The project uses JSFX as a translator because JSFX can receive/send MIDI in REAPER's processing graph with predictable timing. Lua then consumes a compact event queue through gmem.

## Shared-memory failure that exposed an important rule

V0.45.2 had a subtle compatibility bug: `VARIATION_MODE_GMEM` was referenced by the MIDI Engine without being assigned.

In JSFX an uninitialised variable evaluates to `0`, so code intended to write the Variation flag effectively wrote to:

```text
gmem[0]
```

`gmem[0]` is the tracker internal playback on/off flag. Pressing VARIATION therefore behaved like tracker Play/Pause while leaving REAPER transport itself untouched.

The hardware symptom was essential to diagnosing the software mistake. V0.45.3 fixed it by explicitly defining:

```text
VARIATION_MODE_GMEM = 15213
```

in the MIDI Engine, matching Lua and the MK3 LED Bridge.

This is documented here because future contributors adding mode flags should allocate and define the same address in every cooperating component.

## What was not reverse engineered

The tracker does **not** attempt to reproduce:

- Maschine application internals
- Native Instruments firmware logic
- Maschine DSP algorithms
- proprietary preset formats
- proprietary sample content
- Native Instruments source code

REAPER, Controller Editor and the hardware remain external dependencies.

## Reproducibility guidance

When extending hardware support:

1. Record the exact physical control and Controller Editor assignment.
2. Capture the exact MIDI message generated on press/release/turn.
3. Verify whether REAPER passes or consumes the message.
4. Verify the return MIDI value needed for the desired LED state.
5. Keep hardware observations separate from software assumptions.
6. Add a regression test/checklist item before changing the canonical template.

For display work, capture USB interface/endpoint information and test a solid-colour/full-screen transfer before adding UI rendering.