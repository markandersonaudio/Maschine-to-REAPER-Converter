# ============================================================
# MASCHINE TRACKER DISPLAY V0.45.5
#
# Compatible with:
#   Maschine Tracker V0.45.5.lua
#   Maschine Tracker MIDI Engine V0.45.5.jsfx
#
# LEFT SCREEN  = live tracker
# RIGHT SCREEN = selected-step details
#
# Start this program BEFORE opening REAPER.
#
# Requires:
#   python -m pip install pyusb libusb-package pillow
# ============================================================

import os
import sys
import time
import tempfile
import math

import usb.core
import usb.util

from PIL import Image, ImageDraw, ImageFont


# ============================================================
# USB / DISPLAY
# ============================================================

VID = 0x17CC
PID = 0x1600

DISPLAY_INTERFACE = 5
DISPLAY_ENDPOINT = 0x04

SCREEN_W = 480
SCREEN_H = 272
PIXEL_PAIRS = (SCREEN_W * SCREEN_H) // 2

STATE_FILE = os.path.join(
    tempfile.gettempdir(),
    "MaschineTrackerState.txt",
)


# ============================================================
# LIBUSB BACKEND
# ============================================================

try:
    import libusb_package

    backend = libusb_package.get_libusb1_backend()
    print("Using libusb-package backend.")

except Exception as exc:
    backend = None
    print("libusb-package backend unavailable:")
    print(exc)


# ============================================================
# FONTS
# ============================================================

def load_font(size, bold=False):
    candidates = (
        [
            r"C:\Windows\Fonts\consolab.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ]
        if bold
        else
        [
            r"C:\Windows\Fonts\consola.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]
    )

    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass

    return ImageFont.load_default()


FONT_10 = load_font(10)
FONT_11 = load_font(11)
FONT_13 = load_font(13)
FONT_15 = load_font(15)
FONT_17 = load_font(17)
FONT_18B = load_font(18, True)
FONT_21B = load_font(21, True)
FONT_24B = load_font(24, True)
FONT_52B = load_font(52, True)


# ============================================================
# COLOURS
# ============================================================

BG = (13, 15, 19)
PANEL = (25, 29, 35)
HEADER = (35, 41, 50)

TEXT = (238, 240, 244)
MUTED = (135, 143, 154)

GRID_A = (24, 28, 34)
GRID_B = (29, 33, 39)
OUTSIDE = (18, 20, 24)

SELECTED = (48, 86, 130)
PLAYHEAD = (246, 204, 91)
ACTIVE = (91, 185, 112)

ACCENT = (105, 176, 245)
RED = (230, 90, 90)
MULTI_INST = (245, 151, 67)  # legacy colour constant

# Fixed colour identity for Tracker 1..16. The same conceptual
# palette is used by the Jam Indexed-mode LED feedback.
TRACK_COLOURS = [
    (235, 72, 72),    # 1  Red
    (48, 184, 210),   # 2  Cyan
    (235, 220, 56),   # 3  Yellow
    (148, 76, 209),   # 4  Violet
    (56, 184, 90),    # 5  Green
    (220, 86, 158),   # 6  Pink
    (64, 110, 224),   # 7  Blue
    (242, 122, 52),   # 8  Orange
    (52, 199, 145),   # 9  Mint
    (230, 61, 128),   # 10 Fuchsia
    (168, 214, 52),   # 11 Lime
    (122, 76, 178),   # 12 Plum
    (42, 154, 202),   # 13 Turquoise
    (245, 158, 64),   # 14 Light Orange
    (178, 66, 194),   # 15 Purple
    (245, 194, 62),   # 16 Warm Yellow
]


def track_colour(track):
    try:
        index = int(track) - 1
    except Exception:
        index = 0

    index = max(0, min(15, index))
    return TRACK_COLOURS[index]


def blend(a, b, amount):
    amount = max(0.0, min(1.0, float(amount)))

    return tuple(
        int(round(a[i] * (1.0 - amount) + b[i] * amount))
        for i in range(3)
    )


# Top-row action feedback. The Lua state exposes monotonic counters for
# CC22-29; a changed counter creates a short display flash.
TOP_PULSE_LAST = [None] * 8
TOP_FLASH_UNTIL = [0.0] * 8


def update_top_flash(state):
    vals = state.get("toppulses", [0] * 8)
    now = time.monotonic()
    for i in range(8):
        value = vals[i] if i < len(vals) else 0
        if TOP_PULSE_LAST[i] is None:
            TOP_PULSE_LAST[i] = value
        elif value != TOP_PULSE_LAST[i]:
            TOP_PULSE_LAST[i] = value
            TOP_FLASH_UNTIL[i] = now + 0.13
    return tuple(now < t for t in TOP_FLASH_UNTIL)


def draw_top_guides(draw, state, right=False):
    theme = track_colour(int(state.get("track", 1)))
    arranger = bool(state.get("arranger", 0))
    follow = bool(state.get("shiftmod", 0))
    if arranger:
        if not right:
            labels = ["ZOOM -", "ZOOM +", "COPY", "PASTE"] if follow else ["ARR -", "ARR +", "COPY", "PASTE"]
        else:
            labels = ["DUP", "REST", "CLEAR", "CLR TRACK"]
    else:
        labels = (["PREV 8", "NEXT 8", "CMD -", "CMD +"] if not right else ["UNUSED", "DEFAULT", "CLR STEP", "CLR PAT"])
    offset = 4 if right else 0
    now = time.monotonic()
    w = 116
    gap = 4
    for i, label in enumerate(labels):
        active = now < TOP_FLASH_UNTIL[offset + i]
        x1 = 4 + i * (w + gap)
        x2 = x1 + w
        draw.rounded_rectangle(
            (x1, 2, x2, 24),
            radius=4,
            fill=blend(PANEL, theme, 0.52 if active else 0.08),
            outline=theme if active else (58, 64, 72),
            width=2 if active else 1,
        )
        centered_text(draw, (x1 + 2, 3, x2 - 2, 23), label, FONT_10, TEXT if active else MUTED)


def compact_track_name(name, width=7):
    name = str(name or "").strip()
    if not name:
        return ""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name.upper())
    words = [w for w in cleaned.split() if w]
    if not words:
        return name[:width].upper()
    joined = " ".join(words)
    if len(joined) <= width:
        return joined
    if len(words) >= 2:
        initials = "".join(w[0] for w in words[:-1])
        candidate = (initials + " " + words[-1])[:width]
        if len(candidate.strip()) >= 3:
            return candidate
    return words[0][:width]

# ============================================================
# NOTE / COMMAND NAMES
# ============================================================

NOTE_NAMES = [
    "C", "C#", "D", "D#",
    "E", "F", "F#", "G",
    "G#", "A", "A#", "B",
]


def note_name(note):
    try:
        note = int(note)
    except Exception:
        return "---"

    if note < 0:
        return "---"

    pitch = note % 12
    octave = note // 12 - 1
    return NOTE_NAMES[pitch] + str(octave)


def command_name(command):
    """Compact tracker-grid command name."""
    try:
        command = int(command)
    except Exception:
        command = 0

    return {
        0: "-",
        1: "ROL",
        2: "DLY",
        3: "PIT",
        4: "MOD",
        5: "SHF",
        6: "FLM",
        7: "O2",
        8: "O3",
        9: "O4",
    }.get(command, "?")


def command_long_name(command):
    try:
        command = int(command)
    except Exception:
        command = 0

    return {
        0: "NONE",
        1: "ROLL",
        2: "DELAY",
        3: "PITCH",
        4: "MOD",
        5: "SHIFT",
        6: "FLAM",
        7: "OCT2",
        8: "OCT3",
        9: "OCT4",
    }.get(command, "UNKNOWN")


def pitch_display_value(value):
    try:
        value = max(0, min(127, int(value)))
    except Exception:
        value = 64
    if value <= 64:
        return value - 64
    return max(1, min(64, round((value - 64) * 64 / 63)))


def signed_text(value):
    value = int(value)
    return f"+{value}" if value > 0 else str(value)


def command_parameter_display(command, parameter):
    try:
        command = int(command)
        parameter = int(parameter)
    except Exception:
        return "0"
    if command == 3:
        return signed_text(pitch_display_value(parameter))
    if command == 5:
        return signed_text(max(0, min(100, parameter)) - 50)
    return str(parameter)


def command_range_text(command):
    try:
        command = int(command)
    except Exception:
        command = 0
    return {
        0: "--",
        1: "1..16",
        2: "0..99",
        3: "-64..+64",
        4: "0..127",
        5: "-50..+50",
        6: "1..99",
        7: "2..16",
        8: "3..16",
        9: "4..16",
    }.get(command, "--")


def command_value_info(command, parameter, probability):
    try:
        command = int(command)
        parameter = int(parameter)
    except Exception:
        command = 0
        parameter = 0

    if command == 1:
        repeats = max(1, min(16, parameter))
        return ("ROLL REPEATS", f"{repeats}x", "TRIGGERS WITHIN EACH STEP")
    if command == 2:
        delay = max(0, min(99, parameter))
        return ("STEP DELAY", f"{delay}%", "OFFSET LATER THROUGH CURRENT STEP")
    if command == 3:
        value = pitch_display_value(parameter)
        return ("PITCH BEND", signed_text(value), "0 = CENTRE")
    if command == 4:
        value = max(0, min(127, parameter))
        return ("MODULATION", f"{value}", "MIDI CC1")
    if command == 5:
        value = max(0, min(100, parameter)) - 50
        return ("MICRO SHIFT", f"{value:+d}%", "EARLY / LATE WITHIN STEP")
    if command == 6:
        value = max(1, min(99, parameter))
        return ("FLAM SPACING", f"{value}%", "SECOND HIT AFTER FIRST")
    if command in (7, 8, 9):
        octaves = command - 5
        minimum = octaves
        divisions = max(minimum, min(16, parameter))
        return (f"OCT{octaves} RATE", str(divisions), f"ORIGINAL + {octaves-1} OCTAVE STEPS")
    return ("COMMAND VALUE", "--", "NO COMMAND SELECTED")


# ============================================================
# RGB888 -> RGB565 BIG ENDIAN
#
# Matches the working MMK3-HID-Control image path:
# one 16-bit RGB565 word per pixel, sent big-endian.
# ============================================================

R565 = [((i >> 3) << 11) for i in range(256)]
G565 = [((i >> 2) << 5) for i in range(256)]
B565 = [(i >> 3) for i in range(256)]


def image_to_rgb565(image):
    image = image.convert("RGB")

    if image.size != (SCREEN_W, SCREEN_H):
        image = image.resize((SCREEN_W, SCREEN_H))

    src = image.tobytes()
    output = bytearray(SCREEN_W * SCREEN_H * 2)

    pos = 0

    for i in range(0, len(src), 3):
        value = (
            R565[src[i]]
            | G565[src[i + 1]]
            | B565[src[i + 2]]
        )

        output[pos] = (value >> 8) & 0xFF
        output[pos + 1] = value & 0xFF
        pos += 2

    return bytes(output)


# ============================================================
# MK3 FULL IMAGE PACKET
#
# Hardware-tested layout:
#
#  8 bytes  header
#  4 bytes  x/y
#  4 bytes  width/height
#  4 bytes  transfer command (0000FF00 for full display)
#  pixels   RGB565 big-endian
#  4 bytes  blit
#  4 bytes  end
# ============================================================

def image_packet(screen, image):
    if screen not in (0, 1):
        raise ValueError("screen must be 0 or 1")

    pixels = image_to_rgb565(image)

    header = bytes([
        0x84,
        0x00,
        screen,
        0x60,
        0x00,
        0x00,
        0x00,
        0x00,
    ])

    coordinates = (
        (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
    )

    size = (
        SCREEN_W.to_bytes(2, "big")
        + SCREEN_H.to_bytes(2, "big")
    )

    # 480 * 272 / 2 = 65280 = 0xFF00 pixel pairs
    transfer = bytes.fromhex("0000ff00")

    blit = bytes.fromhex("03000000")

    end = bytes([
        0x40,
        0x00,
        screen,
        0x00,
    ])

    packet = (
        header
        + coordinates
        + size
        + transfer
        + pixels
        + blit
        + end
    )

    expected = 28 + (SCREEN_W * SCREEN_H * 2)

    if len(packet) != expected:
        raise RuntimeError(
            f"Unexpected display packet size: {len(packet)} "
            f"(expected {expected})"
        )

    return packet


# ============================================================
# STATE FILE
# ============================================================

def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            text = f.read()

    except (FileNotFoundError, PermissionError, OSError):
        return None

    lines = text.splitlines()

    if not lines:
        return None

    if lines[0] != "MASCHINE_TRACKER_STATE|1":
        return None

    state = {"steps": {}, "arrtracks": {}, "arrsongs": {}, "touchsteps": {}, "vistracks": {}}

    try:
        for line in lines[1:]:
            parts = line.split("|")

            if len(parts) < 2:
                continue

            key = parts[0]

            if key == "step":
                if len(parts) < 10:
                    continue

                step = int(parts[1])

                state["steps"][step] = {
                    "note": int(parts[2]),
                    "velocity": int(parts[3]),
                    "pan": int(parts[4]),
                    "command": int(parts[5]),
                    "parameter": int(parts[6]),
                    "length": int(parts[7]),
                    "probability": int(parts[8]),
                    "active": int(parts[9]),
                }

            elif key == "arrtrack":
                if len(parts) < 7:
                    continue

                track_index = int(parts[1])
                state["arrtracks"][track_index] = {
                    "hasnote": int(parts[2]),
                    "note": int(parts[3]),
                    "inrange": int(parts[4]),
                    "length": int(parts[5]),
                    "division": int(parts[6]),
                    "name": "|".join(parts[7:]) if len(parts) > 7 else f"Track {track_index:02d}",
                }

            elif key == "arrsong":
                if len(parts) < 7:
                    continue
                track_index = int(parts[1])
                if len(parts) >= 11:
                    cell_edit = int(parts[6])
                    seq_text = parts[7]
                    dur_text = parts[8]
                    rest_len_text = parts[9]
                    rest_div_text = parts[10]
                else:
                    # V0.41 state-file compatibility.
                    cell_edit = 0
                    seq_text = parts[6] if len(parts) > 6 else ""
                    dur_text = ""
                    rest_len_text = ""
                    rest_div_text = ""
                state["arrsongs"][track_index] = {
                    "playpos": int(parts[2]),
                    "activepattern": int(parts[3]),
                    "progress": max(0, min(1000, int(parts[4]))),
                    "editpos": int(parts[5]),
                    "celledit": cell_edit,
                    "sequence": [int(x) for x in seq_text.split(",") if x != ""],
                    "durations": [max(1,int(x)) for x in dur_text.split(",") if x != ""],
                    "restlengths": [max(0,int(x)) for x in rest_len_text.split(",") if x != ""],
                    "restdivisions": [max(0,int(x)) for x in rest_div_text.split(",") if x != ""],
                }

            elif key == "touchstep":
                if len(parts) < 8:
                    continue
                strip = int(parts[1])
                state["touchsteps"][strip] = {
                    "step": int(parts[2]),
                    "note": int(parts[3]),
                    "velocity": int(parts[4]),
                    "probability": int(parts[5]),
                    "length": int(parts[6]),
                    "pan": int(parts[7]),
                }

            elif key == "vistrack":
                if len(parts) < 12:
                    continue
                track_index = int(parts[1])
                state["vistracks"][track_index] = {
                    "step": int(parts[2]),
                    "note": int(parts[3]),
                    "velocity": int(parts[4]),
                    "length": int(parts[5]),
                    "pan": int(parts[6]),
                    "command": int(parts[7]),
                    "parameter": int(parts[8]),
                    "probability": int(parts[9]),
                    "mute": int(parts[10]),
                    "solo": int(parts[11]),
                }

            elif key in ("toppulses", "padnotes", "patterncreated", "arrseq"):
                state[key] = [int(x) for x in parts[1].split(",") if x != ""]

            else:
                state[key] = parts[1]

        # If Lua was halfway through rewriting the file, wait for
        # the next pass instead of showing a partial state.
        if len(state["steps"]) != 512:
            return None

        integer_keys = [
            "running",
            "track",
            "cursor",
            "playhead",
            "pattern",
            "playing",
            "column",
            "quick",
            "fixed",
            "wrap",
            "octave",
            "page",
            "jamconnected",
            "trackmute",
            "trackvolume",
            "tracksolo",
            "jamgroupmode",
            "knobfocus",
            "patternmode",
            "patternselect",
            "patternslot",
            "playpattern",
            "fourmode",
            "instlock",
            "jambank",
            "shiftmod",
            "padmode",
            "jamselect",
            "lastpadnote",
            "displaycursor",
            "settingsheld",
            "noteaudition",
            "playheadfollow",
            "banknavrelative",
            "stepsizediv",
            "jamstepbank",
            "tempomode",
            "arranger",
            "overview",
            "arrangereditpos",
            "arrangerplaypos",
            "arrangercelledit",
            "visualizer",
            "visualstyle",
            "visualauto",
            "variationheld",
            "variationbias",
            "variationstrength",
            "variationoctaves",
            "arrangeredittick",
            "notedivmode",
            "sensitivitymode",
            "sensitivityencoder",
            "sensitivitylevel",
            "sensitivitydemo",
            "scaleedit",
            "scalelock",
            "scaleroot",
            "directnote",
            "directaudition",
            "jamtouchmode",
            "lengthtouchrange",
            "tutorials",
            "tutorialactive",
            "inheritvel",
            "inheritln",
            "inheritprob",
            "inheritcmd",
            "arrangerzoom",
            "songtick",
        ]

        for key in integer_keys:
            if key in state:
                state[key] = int(state[key])

        if "tempo" in state:
            state["tempo"] = float(state["tempo"])

        required = [
            "running",
            "track",
            "cursor",
            "playhead",
            "pattern",
            "playing",
            "column",
            "quick",
            "fixed",
            "wrap",
            "octave",
            "page",
        ]

        if any(key not in state for key in required):
            return None

        return state

    except (ValueError, IndexError):
        return None


# ============================================================
# DRAWING HELPERS
# ============================================================

def new_screen():
    return Image.new(
        "RGB",
        (SCREEN_W, SCREEN_H),
        BG,
    )


def centered_text(draw, bounds, text, font_value, fill):
    x1, y1, x2, y2 = bounds

    box = draw.textbbox(
        (0, 0),
        text,
        font=font_value,
    )

    width = box[2] - box[0]
    height = box[3] - box[1]

    x = x1 + (x2 - x1 - width) / 2
    y = y1 + (y2 - y1 - height) / 2 - 2

    draw.text(
        (x, y),
        text,
        font=font_value,
        fill=fill,
    )


def centered_multiline_text(draw, bounds, text, font_value, fill, spacing=0):
    """Center compact multi-line labels inside a bounded pad area."""
    x1, y1, x2, y2 = bounds
    box = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font_value,
        spacing=spacing,
        align="center",
    )
    width = box[2] - box[0]
    height = box[3] - box[1]
    x = x1 + (x2 - x1 - width) / 2
    y = y1 + (y2 - y1 - height) / 2 - 1
    draw.multiline_text(
        (x, y),
        text,
        font=font_value,
        fill=fill,
        spacing=spacing,
        align="center",
    )


def badge(draw, box, text, enabled=True):
    fill = SELECTED if enabled else PANEL
    outline = ACCENT if enabled else (70, 76, 86)

    draw.rounded_rectangle(
        box,
        radius=5,
        fill=fill,
        outline=outline,
        width=1,
    )

    centered_text(
        draw,
        box,
        text,
        FONT_13,
        TEXT if enabled else MUTED,
    )


# ============================================================
# WAITING SCREENS
# ============================================================

def whats_new_lines():
    return [
        "ARRANGER: 4-D directions navigate track + position",
        "4-D PUSH = CELL EDIT; TURN = Pattern / REST",
        "REST: K8 changes length; CLEAR preserves timing",
        "EMPTY cells + REST cells are now separately visible",
        "Top row becomes Arranger navigation/edit controls",
    ]


def render_whats_new(state=None):
    state = state or {}
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    draw.rectangle((0,0,479,34),fill=blend(HEADER,theme,.28))
    draw.text((14,8),"WHAT'S NEW IN V0.45.5",font=FONT_18B,fill=ACTIVE)
    items=[
        ("EMPTY CELLS", "Unassigned Arranger cells are visible and selectable."),
        ("SONG MODE", "SETTINGS P10 chooses LOOP SONG or linear TRUE SONG."),
        ("UNDO / REDO", "Arranger Pattern, REST, clear, paste and length edits are historied."),
        ("TOP ROW", "ARR-/ARR+, COPY, PASTE, DUP, REST, CLEAR, CLR TRACK."),
        ("SCROLLBARS", "Arranger and Pattern screens show off-screen position/length."),
    ]
    y=45
    for title,body in items:
        draw.text((14,y),title,font=FONT_13,fill=theme)
        draw.text((132,y+1),body,font=FONT_10,fill=TEXT)
        y += 42
    return image


def waiting_screen(left=True):
    if not left:
        return render_whats_new(None)

    image = new_screen()
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, 479, 45), fill=HEADER)
    draw.text((15, 10), "MASCHINE TRACKER V0.45.5", font=FONT_24B, fill=TEXT)
    centered_text(draw, (20, 75, 460, 150), "WAITING FOR REAPER", FONT_24B, PLAYHEAD)
    centered_text(draw, (20, 145, 460, 195), "Run Maschine Tracker V0.45.5.lua", FONT_15, MUTED)
    return image


def draw_toggle_badge(
    draw,
    x,
    y,
    width,
    label,
    active,
    theme,
    active_colour=None,
):
    """Draw one compact on/off status icon for the MK3 right screen."""

    height = 25

    if active:
        colour = active_colour if active_colour is not None else theme
        fill = blend(PANEL, colour, 0.58)
        outline = colour
        text_fill = TEXT
        dot_fill = colour
    else:
        fill = BG
        outline = (55, 61, 70)
        text_fill = MUTED
        dot_fill = (55, 61, 70)

    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=5,
        fill=fill,
        outline=outline,
        width=1,
    )

    draw.ellipse(
        (x + 5, y + 8, x + 11, y + 14),
        fill=dot_fill,
    )

    centered_text(
        draw,
        (x + 12, y, x + width - 2, y + height),
        label,
        FONT_13,
        text_fill,
    )


def draw_padlock(draw, x, y, fill, scale=1.0):
    """Small vector padlock, avoiding dependence on a Unicode lock glyph."""

    body_w = max(6, int(10 * scale))
    body_h = max(5, int(8 * scale))
    shackle_w = max(5, int(7 * scale))
    shackle_h = max(4, int(6 * scale))
    stroke = max(1, int(round(scale)))

    body_x1 = int(x)
    body_y1 = int(y + shackle_h - 1)
    body_x2 = body_x1 + body_w
    body_y2 = body_y1 + body_h

    shackle_x1 = body_x1 + (body_w - shackle_w) // 2
    shackle_y1 = int(y)
    shackle_x2 = shackle_x1 + shackle_w
    shackle_y2 = body_y1 + 2

    draw.arc(
        (shackle_x1, shackle_y1, shackle_x2, shackle_y2),
        180,
        360,
        fill=fill,
        width=stroke,
    )

    draw.line(
        (shackle_x1, shackle_y1 + shackle_h // 2,
         shackle_x1, body_y1 + 1),
        fill=fill,
        width=stroke,
    )

    draw.line(
        (shackle_x2, shackle_y1 + shackle_h // 2,
         shackle_x2, body_y1 + 1),
        fill=fill,
        width=stroke,
    )

    draw.rounded_rectangle(
        (body_x1, body_y1, body_x2, body_y2),
        radius=max(1, int(2 * scale)),
        outline=fill,
        fill=blend(BG, fill, 0.24),
        width=stroke,
    )


def active_pattern_is_multi(state):
    # Per-step instrument routing was removed in V0.45.5.
    return False


# ============================================================
# LEFT SCREEN - TRACKER
# ============================================================

def render_tracker(state):
    image = new_screen()
    draw = ImageDraw.Draw(image)

    track = int(state.get("track", 1))
    cursor = int(state.get("cursor", 1))
    display_cursor = int(state.get("displaycursor", cursor))
    playhead = int(state.get("playhead", 1))
    pattern = int(state.get("pattern", 16))
    playing = bool(state.get("playing", 0))
    selected_column = int(state.get("column", 1))
    knob_focus = int(state.get("knobfocus", 0))
    note_change = bool(state.get("fourmode", 0))
    track_muted = bool(state.get("trackmute", 0))
    track_solo = bool(state.get("tracksolo", 0))
    theme = track_colour(track)

    draw_top_guides(draw, state, right=False)
    draw.rectangle((0, 28, 479, 58), fill=blend(HEADER, theme, 0.30))
    first = ((display_cursor - 1) // 8) * 8 + 1
    last = first + 7
    draw.text((7, 34), f"T{track:02d}", font=FONT_17, fill=TEXT)
    draw.text((45, 35), str(state.get("trackname", ""))[:15], font=FONT_13, fill=theme)
    draw.text((184, 34), f"{first:03d}-{last:03d}", font=FONT_17, fill=theme)
    pattern_slot = int(state.get("patternslot", 1))
    play_pattern = int(state.get("playpattern", pattern_slot))
    draw.text((280, 34), f"P{pattern_slot:02d} L{pattern:03d}", font=FONT_15, fill=TEXT)
    if playing and play_pattern != pattern_slot:
        draw.text((374, 35), f"PLAY P{play_pattern:02d}", font=FONT_10, fill=PLAYHEAD)
    status = "NOTE CHANGE" if note_change else ("SOLO" if track_solo else ("MUTE" if track_muted else ("PLAY" if playing else "STOP")))
    draw.text((397 if note_change else 435, 36), status, font=FONT_10 if note_change else FONT_11, fill=ACCENT if note_change else (ACTIVE if playing or track_solo else (RED if track_muted else MUTED)))

    # V0.45.5: K7 is unused and PAN remains outside the tracker.
    knob_key_map = {1:"note",2:"length",3:"velocity",4:"probability",5:"command",6:"parameter",8:"step"}
    selected_key_map = {1:"note",2:"velocity",3:"command",4:"parameter",5:"length",6:"probability"}
    focus_key = knob_key_map.get(knob_focus, selected_key_map.get(selected_column,"note"))
    columns = [
        ("step",8,42,"ST"),        ("note",62,58,"NOTE"),
        ("length",124,38,"LN"),
        ("velocity",168,48,"VEL"),
        ("probability",222,54,"PRB"),
        ("command",292,52,"CMD"),
        ("parameter",354,112,"VAL"),
    ]
    draw.rectangle((52, 59, 55, 270), fill=blend(BG, theme, 0.50))
    draw.rectangle((282, 59, 285, 270), fill=blend(BG, theme, 0.50))
    for key,x,w,h in columns:
        header_fill = theme if key == focus_key else MUTED
        if note_change and key != "note":
            header_fill = blend(BG, header_fill, 0.55)
        draw.text((x,61),h,font=FONT_11,fill=header_fill)

    y=79; rh=23
    for step in range(first,last+1):
        data=state["steps"].get(step,{})
        outside=step>pattern
        selected=step==cursor
        is_playhead=playing and play_pattern==pattern_slot and step==playhead
        active=bool(data.get("active",0)) and play_pattern==pattern_slot
        prob_zero = active and int(data.get("probability",100)) == 0
        dimmed = note_change and not selected

        if dimmed:
            row=(16,18,22)
        elif prob_zero:
            row=(44,46,51) if selected else (30,32,36)
        elif outside:
            row=OUTSIDE
        elif selected:
            row=blend(BG,theme,.30)
        elif (step-first)%2:
            row=GRID_B
        else:
            row=GRID_A
        draw.rectangle((4,y-3,474,y+18),fill=row)

        if is_playhead:
            ph_col = blend(BG, PLAYHEAD, 0.28) if dimmed else PLAYHEAD
            draw.rectangle((4,y-3,7,y+18),fill=ph_col)

        vals={
            "step":f"{step:03d}",
            "note":note_name(data.get("note",-1)),
            "length":f"{int(data.get('length',1)):02d}",
            "velocity":f"{int(data.get('velocity',100)):03d}",
            "probability":f"{int(data.get('probability',100)):03d}",
            "command":command_name(data.get("command",0)),
            "parameter":command_parameter_display(data.get("command",0), data.get("parameter",0)),
        }
        for key,x,w,h in columns:
            if dimmed:
                fill=(72,78,88)
            elif prob_zero:
                fill=(170,174,182) if selected and key==focus_key else (102,108,118)
            elif selected and key==focus_key:
                fill=TEXT
            elif key=="note" and active:
                fill=ACTIVE
            elif outside:
                fill=MUTED
            else:
                fill=TEXT
            if note_change and selected and key == "note":
                fill = PLAYHEAD
            draw.text((x,y),vals[key],font=FONT_13 if key!="note" else FONT_15,fill=fill)
        y+=rh

    # Pattern viewport scrollbar: fixed to the current pattern length.
    bar_x1, bar_x2, bar_y1, bar_y2 = 4, 474, 267, 271
    draw.rectangle((bar_x1,bar_y1,bar_x2,bar_y2),fill=(31,35,41))
    plen=max(1,pattern)
    visible=min(8,plen)
    view_start=max(0,min(first-1,plen-visible))
    thumb_w=max(10,int((bar_x2-bar_x1)*(visible/plen)))
    travel=max(0,(bar_x2-bar_x1)-thumb_w)
    frac=0.0 if plen<=visible else view_start/max(1,plen-visible)
    tx=bar_x1+int(travel*frac)
    draw.rectangle((tx,bar_y1,tx+thumb_w,bar_y2),fill=theme)
    return image


def _pad_grid_boxes(grid_y=116, pad_h=33, gap_y=4):
    rows = [[13,14,15,16],[9,10,11,12],[5,6,7,8],[1,2,3,4]]
    result = {}
    pad_w=112; gap_x=6; grid_x=5
    for ri,row in enumerate(rows):
        for ci,pad in enumerate(row):
            x1=grid_x+ci*(pad_w+gap_x); y1=grid_y+ri*(pad_h+gap_y)
            result[pad]=(x1,y1,x1+pad_w,y1+pad_h)
    return result


def draw_mode_pad_grid(draw, state, labels, selected=None, inactive_from=9, base_colour=None, selected_colour=(245,245,245)):
    theme=base_colour or track_colour(int(state.get("track",1)))
    for pad,box in _pad_grid_boxes().items():
        label=labels.get(pad,"--")
        inactive = pad >= inactive_from
        is_sel = selected == pad
        if inactive:
            fill=BG; outline=(45,49,56); txt=(72,77,86)
        elif is_sel:
            fill=selected_colour; outline=selected_colour; txt=BG
        else:
            fill=blend(PANEL,theme,.38); outline=theme; txt=TEXT
        draw.rounded_rectangle(box,radius=5,fill=fill,outline=outline,width=2 if is_sel else 1)
        x1,y1,x2,y2=box
        draw.text((x1+5,y1+3),f"P{pad}",font=FONT_10,fill=txt if is_sel else MUTED)
        centered_text(draw,(x1+20,y1+2,x2-3,y2-2),str(label),FONT_11,txt)


def render_settings(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    draw.rectangle((0,0,479,34),fill=blend(HEADER,theme,.28))
    draw.text((10,6),"SETTINGS / PREFERENCES",font=FONT_18B,fill=TEXT)
    prefs={
        1:("WRAP",bool(state.get("wrap",1)),"ON","OFF"),
        2:("ARRANGER VIEW",str(state.get("arrangerview","HORIZONTAL")).upper()=="VERTICAL","VERTICAL","HORIZONTAL"),
        3:("PLAYHEAD FOLLOW",bool(state.get("playheadfollow",0)),"ON","OFF"),
        4:("BANK NAV",bool(state.get("banknavrelative",1)),"RELATIVE","FIRST STEP"),
        5:("TUTORIALS",bool(state.get("tutorials",1)),"ON","OFF"),
        6:("INHERIT VEL",bool(state.get("inheritvel",0)),"ON","OFF"),
        7:("INHERIT LN",bool(state.get("inheritln",0)),"ON","OFF"),
        8:("INHERIT PROB",bool(state.get("inheritprob",0)),"ON","OFF"),
        9:("INHERIT CMD",bool(state.get("inheritcmd",0)),"CMD + VAL","OFF"),
        10:("SONG MODE",str(state.get("songmode","LOOP")).upper()=="TRUE","TRUE","LOOP"),
    }
    boxes=_pad_grid_boxes(grid_y=42,pad_h=52,gap_y=5)
    for pad,box in boxes.items():
        pref=prefs.get(pad); x1,y1,x2,y2=box
        if pref:
            label,active,on,off=pref; outline=theme if active else (65,72,82); fill=blend(PANEL,theme,.40 if active else .07)
            draw.rounded_rectangle(box,radius=6,fill=fill,outline=outline,width=2 if active else 1)
            draw.text((x1+5,y1+4),f"P{pad}",font=FONT_10,fill=theme)
            centered_text(draw,(x1+3,y1+14,x2-3,y1+35),label,FONT_10 if len(label)>11 else FONT_11,TEXT if active else MUTED)
            centered_text(draw,(x1+3,y1+34,x2-3,y2-2),on if active else off,FONT_10,ACTIVE if active else MUTED)
        else:
            draw.rounded_rectangle(box,radius=6,fill=BG,outline=(43,48,55),width=1)
            draw.text((x1+5,y1+4),f"P{pad}",font=FONT_10,fill=(65,70,78))
    return image


def render_pattern_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    length=int(state.get("pattern",16)); div=int(state.get("stepsizediv",16))
    length_presets=[1,2,4,8,16,32,64,128]
    divisions=[1,2,4,8,16,32,64,128]
    div_labels=["WHOLE","HALF","1/4","1/8","1/16","1/32","1/64","1/128"]

    draw.rectangle((0,0,479,38),fill=blend(HEADER,theme,.34))
    centered_text(draw,(4,2,476,36),"PATTERN / DIVISION",FONT_21B,PLAYHEAD)
    current_div = "WHOLE" if div==1 else ("HALF" if div==2 else f"1/{div}")
    draw.text((10,45),f"LENGTH {length} STEPS",font=FONT_15,fill=TEXT)
    draw.text((248,45),f"DIV {current_div}",font=FONT_15,fill=ACCENT)
    draw.text((10,67),"PADS 1-8 = LENGTH",font=FONT_10,fill=theme)
    draw.text((248,67),"PADS 9-16 = NOTE DIVISION",font=FONT_10,fill=ACCENT)

    boxes=_pad_grid_boxes(grid_y=88,pad_h=40,gap_y=4)
    for pad,box in boxes.items():
        x1,y1,x2,y2=box
        if pad <= 8:
            value=length_presets[pad-1]
            label=f"LEN {value}"
            selected=(length==value)
            colour=theme
        else:
            idx=pad-9
            value=divisions[idx]
            label=div_labels[idx]
            selected=(div==value)
            colour=ACCENT
        fill=(245,245,245) if selected else blend(PANEL,colour,.28)
        outline=(245,245,245) if selected else colour
        txt=BG if selected else TEXT
        draw.rounded_rectangle(box,radius=5,fill=fill,outline=outline,width=2 if selected else 1)
        draw.text((x1+4,y1+3),f"P{pad}",font=FONT_10,fill=(80,84,90) if selected else MUTED)
        centered_text(draw,(x1+20,y1+2,x2-3,y2-2),label,FONT_11,txt)
    return image


def render_pattern_select(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    current=max(1,min(16,int(state.get("patternslot",1))))
    created=state.get("patterncreated",[])
    draw.rectangle((0,0,479,38),fill=blend(HEADER,theme,.34))
    centered_text(draw,(4,2,476,36),"PATTERN SELECT",FONT_21B,theme)
    draw.text((10,46),f"TRACK {int(state.get('track',1)):02d}   CURRENT P{current:02d}",font=FONT_15,fill=TEXT)
    draw.text((10,66),"EMPTY PAD = CREATE   |   CREATED PAD = SELECT FOR EDIT",font=FONT_10,fill=MUTED)
    draw.text((10,79),"4-D PUSH = MAKE SELECTED PATTERN AUDIBLE AT NEXT BOUNDARY",font=FONT_10,fill=theme)
    boxes=_pad_grid_boxes(grid_y=94,pad_h=38,gap_y=4)
    for pad,box in boxes.items():
        exists = (pad-1 < len(created) and bool(created[pad-1])) or pad == 1
        selected = pad == current
        colour = theme if exists else (52,57,64)
        fill = (245,245,245) if selected else blend(PANEL,colour,.32 if exists else .04)
        outline = (245,245,245) if selected else colour
        txt = BG if selected else (TEXT if exists else MUTED)
        x1,y1,x2,y2=box
        draw.rounded_rectangle(box,radius=5,fill=fill,outline=outline,width=2 if selected else 1)
        draw.text((x1+4,y1+3),f"P{pad}",font=FONT_10,fill=(80,84,90) if selected else MUTED)
        centered_text(draw,(x1+20,y1+2,x2-3,y2-2),f"PAT {pad:02d}" if exists else "NEW",FONT_11,txt)
    return image


def render_note_division_mode(state):
    # Compatibility fallback for old state files; V0.45.5 uses FOLLOW+PATTERN.
    return render_pattern_mode(state)


def sensitivity_demo_text(enc,value):
    if enc==1: return note_name(int(value))
    if enc==2: return f"LEN {int(value)}"
    if enc==3: return str(int(value))
    if enc==4: return f"{int(value)}%"
    if enc==5: return command_long_name(int(value))
    if enc==6: return str(int(value))
    if enc==7: return "UNUSED"
    return f"STEP {int(value):03d}"


def render_sensitivity_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    enc=max(1,min(8,int(state.get("sensitivityencoder",1)))); level=max(1,min(8,int(state.get("sensitivitylevel",5))))
    names=["NOTE","LENGTH","VELOCITY","PROBABILITY","COMMAND","VALUE","UNUSED","STEP"]
    demo=state.get("sensitivitydemo",0)
    draw.rectangle((0,0,479,38),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,2,476,36),"SENSITIVITY MODE",FONT_21B,TEXT)
    draw.text((10,46),f"ENCODER {enc}  |  {names[enc-1]}",font=FONT_17,fill=theme)
    draw.text((10,70),"TEST VALUE",font=FONT_11,fill=MUTED); draw.text((92,65),sensitivity_demo_text(enc,demo),font=FONT_21B,fill=TEXT)
    draw.text((286,70),"TOUCH ENCODER | TURN TO TEST",font=FONT_10,fill=MUTED)
    labels={1:"V.LO",2:"LOW",3:"L-M",4:"M-LO",5:"MED",6:"M-HI",7:"HIGH",8:"V.HI"}
    draw_mode_pad_grid(draw,state,labels,selected=level,inactive_from=9,base_colour=(242,122,52),selected_colour=(245,245,245))
    return image


def render_scale_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    roots=NOTE_NAMES; root=roots[int(state.get("scaleroot",0))%12]; scale=str(state.get("scalename","Major")); lock=bool(state.get("scalelock",0))
    draw.rectangle((0,0,479,40),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,2,476,38),"SCALE LOCK",FONT_21B,theme)

    draw.rounded_rectangle((18,56,462,133),radius=8,fill=blend(PANEL,theme,.30 if lock else .06),outline=theme,width=2 if lock else 1)
    centered_text(draw,(24,63,456,86),"PAD 1  SCALE LOCK",FONT_15,TEXT)
    centered_text(draw,(24,88,456,124),"ON" if lock else "OFF",FONT_24B,ACTIVE if lock else MUTED)
    centered_text(draw,(20,139,460,160),"EXISTING PATTERN NOTES ARE NOT CHANGED",FONT_10,MUTED)

    # Bottom row mirrors the physical right-screen encoders 5 and 6.
    boxes=[(4,174,117,230),(122,174,235,230),(240,174,353,230),(358,174,471,230)]
    items=[("K5 ROOT",root,theme),("K6 SCALE",scale[:12],ACCENT),("K7","UNUSED",MUTED),("K8","--",MUTED)]
    for box,(label,value,colour) in zip(boxes,items):
        x1,y1,x2,y2=box
        draw.rounded_rectangle(box,radius=5,fill=blend(PANEL,colour,.18 if colour!=MUTED else .04),outline=colour if colour!=MUTED else (58,64,72),width=1)
        centered_text(draw,(x1+2,y1+3,x2-2,y1+20),label,FONT_10,MUTED)
        centered_text(draw,(x1+2,y1+21,x2-2,y2-3),value,FONT_15,TEXT if colour!=MUTED else MUTED)
    centered_text(draw,(10,238,470,264),"TURN K5 = ROOT   |   TURN K6 = SCALE",FONT_11,theme)
    return image


def render_direct_note_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    audition=bool(state.get("directaudition",0)); octave=int(state.get("octave",4))
    draw.rectangle((0,0,479,40),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,2,476,38),"DIRECT NOTE",FONT_21B,theme)
    centered_text(draw,(5,50,235,88),f"OCTAVE {octave}",FONT_21B,TEXT)
    centered_text(draw,(245,50,475,88),"AUDITION" if audition else "WRITE",FONT_21B,ACTIVE if audition else PLAYHEAD)
    centered_text(draw,(10,108,470,145),"A   B   C   D   E   F   G      H = #",FONT_18B,TEXT)
    centered_text(draw,(10,158,470,187),"UP / DOWN  =  OCTAVE",FONT_15,MUTED)
    centered_text(draw,(10,190,470,219),"NOTE REPEAT  =  AUDITION ON/OFF",FONT_15,MUTED)
    centered_text(draw,(10,228,470,260),"PRESS NOTES TO EXIT",FONT_13,theme)
    return image


def render_touch_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1))); mode=int(state.get("jamtouchmode",0))
    title={1:"VELOCITY TOUCH",2:"PROBABILITY TOUCH",3:"LENGTH TOUCH"}.get(mode,"TOUCH EDIT")
    draw.rectangle((0,0,479,39),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,2,476,37),title,FONT_21B,theme)
    if mode==3:
        draw.text((10,45),f"LENGTH RANGE: {int(state.get('lengthtouchrange',4))} VALUES   |   UP/DOWN CHANGES RANGE",font=FONT_11,fill=MUTED)
    else:
        draw.text((10,45),"STRIPS 1-8 EDIT THE 8 STEPS ON THE LEFT SCREEN",font=FONT_11,fill=MUTED)
    x0=5; w=55; gap=4
    for i in range(1,9):
        info=state.get("touchsteps",{}).get(i,{})
        step=int(info.get("step",i)); note=int(info.get("note",-1))
        if mode==1: value=str(int(info.get("velocity",0)))
        elif mode==2: value=f"{int(info.get('probability',0))}%"
        else: value=f"L{int(info.get('length',1))}"
        x1=x0+(i-1)*(w+gap); x2=x1+w
        active=note>=0
        draw.rounded_rectangle((x1,72,x2,257),radius=5,fill=blend(BG,theme,.20 if active else .03),outline=theme if active else (52,57,64),width=1)
        centered_text(draw,(x1,77,x2,99),f"S{i}",FONT_11,theme if active else MUTED)
        centered_text(draw,(x1,102,x2,126),f"{step}",FONT_13,TEXT if active else MUTED)
        centered_text(draw,(x1,138,x2,177),value,FONT_17,TEXT if active else MUTED)
        centered_text(draw,(x1,188,x2,214),note_name(note),FONT_11,theme if active else MUTED)
        centered_text(draw,(x1,225,x2,252),"EDIT" if active else "EMPTY",FONT_10,ACTIVE if active else MUTED)
    return image


def render_tutorial(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1))); mode=str(state.get("tutorialmode","")).upper()
    tutorials={
        "PATTERN / DIVISION":["FOLLOW + PATTERN = TIMING MENU","PADS 1-8 = LENGTH PRESETS","PADS 9-16 = WHOLE ... 1/128","RELEASE PATTERN TO EXIT"],
        "PATTERN LENGTH":["PADS 1-8 = LENGTH PRESETS","PADS 9-16 = NOTE DIVISION","RELEASE PATTERN TO EXIT"],
        "SENSITIVITY":["TOUCH AN ENCODER TO SELECT","PADS 1-8 = SENSITIVITY","TURN ENCODER TO TEST","VOLUME = EXIT"],
        "DIRECT NOTE":["A-G = NOTE   H = SHARP","UP/DOWN = OCTAVE","NOTE REPEAT = AUDITION","NOTES = EXIT"],
        "VELOCITY TOUCH":["STRIPS 1-8 = VISIBLE STEPS","MOVE STRIP = SET VELOCITY","CLEAR = RESET 8   LEVEL+CLEAR = ALL","LEVEL = EXIT"],
        "PROBABILITY TOUCH":["STRIPS 1-8 = VISIBLE STEPS","MOVE STRIP = SET PROBABILITY","CLEAR = RESET 8   CONTROL+CLEAR = ALL","CONTROL = EXIT"],
        "LENGTH TOUCH":["STRIPS 1-8 = VISIBLE STEPS","MOVE STRIP = SET LENGTH","UP/DOWN = CHANGE RANGE","CLEAR = RESET 8   AUX+CLEAR = ALL"],
        "SCALE LOCK":["K5 = ROOT   K6 = SCALE","PAD 1 = SCALE LOCK ON/OFF","KEYBOARD = EXIT"],
        "SONG ARRANGER":["4-D DIRECTIONS = MOVE GRID","4-D PUSH = CELL EDIT; TURN = PATTERN/REST","REST: K8 = LENGTH   CLR STEP = SAME-LENGTH REST","ARRANGER = EXIT"],
        "OVERVIEW":["BROWSER = 16-TRACK OVERVIEW","FOLLOW + BROWSER = VISUALISER","BROWSER = EXIT"],
        "PATTERN SELECT":["PADS 1-16 = PATTERN SLOTS","EMPTY PAD CREATES A NEW PATTERN","CREATED PAD SELECTS IT","4-D PUSH = HEAR SELECTED PATTERN"],
        "TEMPO":["LARGE ENCODER = TEMPO","1 DETENT = 1 BPM","TEMPO = EXIT"],
        "VISUALISER":["LIVE MIDI FROM ALL 16 TRACKS","LARGE ENCODER = CHANGE STYLE","PUSH = AUTO CYCLE","FOLLOW + BROWSER = EXIT"],
    }
    lines=tutorials.get(mode,["USE THE HIGHLIGHTED CONTROLS","TO OPERATE THIS MODE"])
    draw.rectangle((0,0,479,43),fill=blend(HEADER,theme,.38)); centered_text(draw,(4,3,476,40),mode or "TUTORIAL",FONT_21B,theme)
    y=70
    for line in lines:
        centered_text(draw,(15,y,465,y+30),line,FONT_15,TEXT if y<130 else MUTED); y+=38
    centered_text(draw,(12,235,468,264),"TUTORIALS: SETTINGS + PAD 5",FONT_11,theme)
    return image


def render_tempo_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1))); tempo=float(state.get("tempo",120.0))
    draw.rectangle((0,0,479,39),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,2,476,37),"TEMPO MODE",FONT_21B,TEXT)
    centered_text(draw,(10,61,470,174),f"{tempo:.0f}",FONT_52B,PLAYHEAD); centered_text(draw,(10,151,470,204),"BPM",FONT_21B,TEXT)
    centered_text(draw,(10,216,470,263),"LARGE ENCODER  |  1 TICK = 1 BPM",FONT_15,MUTED); return image


def render_overview(state, first_track, last_track):
    image=new_screen(); draw=ImageDraw.Draw(image)
    current_track=int(state.get("track",1)); step=int(state.get("displaycursor",state.get("cursor",1))); track_range=list(range(first_track,last_track+1))
    draw.rectangle((0,0,479,38),fill=HEADER); draw.text((8,6),"BROWSER OVERVIEW",font=FONT_18B,fill=TEXT); draw.text((325,6),f"STEP {step:03d}",font=FONT_18B,fill=PLAYHEAD)
    x0=4; y0=47; gap=3; col_w=56; col_h=220
    for col,track in enumerate(track_range):
        x1=x0+col*(col_w+gap); x2=x1+col_w; info=state.get("arrtracks",{}).get(track,{})
        has_note=bool(info.get("hasnote",0)); in_range=bool(info.get("inrange",0)); note=int(info.get("note",-1)); colour=track_colour(track); name=compact_track_name(info.get("name",f"Track {track}"),7)
        if has_note: fill=blend(BG,colour,.24); outline=TEXT if track==current_track else colour; header_fill=colour; body_fill=TEXT if in_range else MUTED
        else: fill=blend(BG,PANEL,.30); outline=(52,57,64); header_fill=MUTED; body_fill=(88,94,103)
        draw.rounded_rectangle((x1,y0,x2,y0+col_h),radius=5,fill=fill,outline=outline,width=2 if track==current_track else 1)
        centered_text(draw,(x1,y0+4,x2,y0+24),f"T{track}",FONT_13,header_fill)
        centered_text(draw,(x1+1,y0+23,x2-1,y0+39),name,FONT_10,header_fill if has_note else MUTED)
        draw.line((x1+4,y0+43,x2-4,y0+43),fill=outline,width=1)
        note_text=note_name(note) if in_range else "OUT"
        centered_text(draw,(x1,y0+59,x2,y0+110),note_text,FONT_18B,body_fill)
        centered_text(draw,(x1,y0+124,x2,y0+144),f"1/{int(info.get('division',16))}",FONT_10,header_fill)
        centered_text(draw,(x1,y0+149,x2,y0+169),f"L{int(info.get('length',16))}",FONT_10,TEXT if has_note else MUTED)
        centered_text(draw,(x1,y0+188,x2,y0+211),"ACTIVE" if has_note else "EMPTY",FONT_10,header_fill if has_note else MUTED)
    return image


def _arranger_entries(info, min_positions=0):
    seq=list(info.get("sequence",[]))
    durs=list(info.get("durations",[]))
    rest_lengths=list(info.get("restlengths",[]))
    rest_divs=list(info.get("restdivisions",[]))
    count=max(len(seq), int(min_positions or 0))
    entries=[]
    cursor=0
    for i in range(count):
        slot=int(seq[i]) if i < len(seq) else -1
        dur=max(1,int(durs[i])) if i < len(durs) else 128
        entries.append({
            "pos":i+1,
            "slot":slot,
            "start":cursor,
            "duration":dur,
            "end":cursor+dur,
            "restlength":int(rest_lengths[i]) if i < len(rest_lengths) else (16 if slot <= 0 else 0),
            "restdivision":int(rest_divs[i]) if i < len(rest_divs) else (16 if slot <= 0 else 0),
        })
        cursor += dur
    return entries


def _arranger_zoom_span(state):
    # Stable discrete zoom levels. Lower level = farther out.
    spans={1:4096,2:2048,3:1024,4:512,5:256}
    return spans.get(max(1,min(5,int(state.get("arrangerzoom",3)))),1024)


def _arranger_time_window(state):
    span=_arranger_zoom_span(state)
    selected=max(1,min(16,int(state.get("track",1))))
    info=state.get("arrsongs",{}).get(selected,{})
    edit=max(1,int(info.get("editpos",state.get("arrangereditpos",1))))
    entries=_arranger_entries(info,edit)
    fallback=entries[edit-1]["start"] if entries and edit <= len(entries) else 0
    anchor=max(0,int(state.get("arrangeredittick",fallback)))
    start=(anchor // span) * span
    return start,start+span


def _arranger_entries_cover_time(info, target_time, edit_tick=None):
    entries=_arranger_entries(info)
    explicit_count=len(info.get("sequence",[]))
    cursor=entries[-1]["end"] if entries else 0

    # V0.45.5: when the edit anchor is beyond explicit material, draw one
    # virtual EMPTY beginning at the exact musical-time anchor. A gap before
    # it is shown as EMPTY but is not persisted merely by navigation.
    if edit_tick is not None and int(edit_tick) >= cursor:
        edit_tick=max(0,int(edit_tick))
        if edit_tick > cursor:
            entries.append({"pos":0,"slot":-1,"start":cursor,"duration":edit_tick-cursor,"end":edit_tick,
                            "restlength":0,"restdivision":0,"virtual_gap":True})
        entries.append({"pos":explicit_count+1,"slot":-1,"start":edit_tick,"duration":128,"end":edit_tick+128,
                        "restlength":16,"restdivision":16,"virtual_edit":True})
        cursor=edit_tick+128

    while (not entries or entries[-1]["end"] < target_time) and len(entries) < 128:
        pos=explicit_count+1
        cursor=entries[-1]["end"] if entries else 0
        entries.append({"pos":pos,"slot":-1,"start":cursor,"duration":128,"end":cursor+128,
                        "restlength":16,"restdivision":16,"virtual_fill":True})
    return entries


def _arranger_play_time(info):
    play=max(0,int(info.get("playpos",0)))
    entries=_arranger_entries(info,play)
    if play < 1 or play > len(entries): return None
    e=entries[play-1]
    progress=max(0.0,min(1.0,float(info.get("progress",0))/1000.0))
    return e["start"] + e["duration"]*progress


def _arranger_total_extent(state, span):
    total=span
    selected=max(1,min(16,int(state.get("track",1))))
    for track in range(1,17):
        info=state.get("arrsongs",{}).get(track,{})
        minpos=int(info.get("editpos",1)) if track==selected else 0
        entries=_arranger_entries(info,minpos)
        if entries: total=max(total,entries[-1]["end"])
    total=max(total,int(state.get("arrangeredittick",0))+span)
    if str(state.get("songmode","LOOP")).upper()=="TRUE":
        total=max(total,int(state.get("songtick",0))+span)
    return max(1,total)


def _draw_horizontal_scrollbar(draw, state, t0, t1, x1=36, x2=474, y1=267, y2=271):
    span=max(1,t1-t0); total=_arranger_total_extent(state,span)
    draw.rectangle((x1,y1,x2,y2),fill=(31,35,41))
    trackw=x2-x1
    thumb=max(10,int(trackw*min(1.0,span/total)))
    max_start=max(1,total-span)
    frac=max(0.0,min(1.0,t0/max_start)) if total>span else 0.0
    tx=x1+int((trackw-thumb)*frac)
    draw.rectangle((tx,y1,tx+thumb,y2),fill=track_colour(int(state.get("track",1))))


def _draw_vertical_scrollbar(draw, state, t0, t1, x1=475, x2=479, y1=66, y2=264):
    span=max(1,t1-t0); total=_arranger_total_extent(state,span)
    draw.rectangle((x1,y1,x2,y2),fill=(31,35,41))
    trackh=y2-y1
    thumb=max(10,int(trackh*min(1.0,span/total)))
    max_start=max(1,total-span)
    frac=max(0.0,min(1.0,t0/max_start)) if total>span else 0.0
    ty=y1+int((trackh-thumb)*frac)
    draw.rectangle((x1,ty,x2,ty+thumb),fill=track_colour(int(state.get("track",1))))


def _draw_arranger_progress(draw, box, progress, colour, vertical=False):
    x1,y1,x2,y2=box
    p=max(0.0,min(1.0,float(progress)/1000.0))
    if vertical:
        h=max(1,int((y2-y1-2)*p))
        draw.rectangle((x2-3,y1+1,x2-1,y1+1+h),fill=colour)
    else:
        w=max(1,int((x2-x1-2)*p))
        draw.rectangle((x1+1,y2-3,x1+1+w,y2-1),fill=colour)


def _arranger_cell_label(entry):
    if entry["slot"] < 0: return "--"
    if entry["slot"] == 0: return "R"
    return f"{entry['slot']:02d}"


def _arranger_playhead_time(state):
    if not bool(state.get("playing",0)): return None
    if str(state.get("songmode","LOOP")).upper()=="TRUE":
        return max(0,int(state.get("songtick",0)))
    selected=max(1,min(16,int(state.get("track",1))))
    return _arranger_play_time(state.get("arrsongs",{}).get(selected,{}))


def render_song_arranger_horizontal(state):
    image=new_screen(); draw=ImageDraw.Draw(image)
    selected=max(1,min(16,int(state.get("track",1)))); theme=track_colour(selected)
    draw_top_guides(draw,state,right=False)
    t0,t1=_arranger_time_window(state); span=max(1,t1-t0)
    edit=int(state.get("arrangereditpos",1)); edit_tick=max(0,int(state.get("arrangeredittick",0))); mode="EDIT" if bool(state.get("arrangercelledit",0)) else "NAV"
    songmode=str(state.get("songmode","LOOP")).upper()
    draw.rectangle((0,27,479,51),fill=blend(HEADER,theme,.30))
    draw.text((6,31),"ARRANGER H",font=FONT_13,fill=TEXT)
    draw.text((102,32),f"T{selected:02d} POS {edit:03d}  TICK {edit_tick}",font=FONT_10,fill=theme)
    draw.text((363,32),f"{mode} Z{int(state.get('arrangerzoom',3))}",font=FONT_10,fill=PLAYHEAD if songmode=="TRUE" else MUTED)
    label_w=34; x0=label_w+2; x2=474; y0=54; row_h=13

    play_time=_arranger_playhead_time(state)
    if play_time is not None and t0 <= play_time <= t1:
        px=x0+int((play_time-t0)/span*(x2-x0))
        draw.line((px,52,px,264),fill=PLAYHEAD,width=1)

    for track in range(1,17):
        info=state.get("arrsongs",{}).get(track,{})
        entries=_arranger_entries_cover_time(info,t1,edit_tick if track==selected else None)
        play=int(info.get("playpos",0)); progress=int(info.get("progress",0)); editpos=int(info.get("editpos",1))
        y=y0+(track-1)*row_h; tcol=track_colour(track); selected_row=(track==selected)
        if selected_row: draw.rectangle((0,y-1,474,y+row_h-2),fill=blend(BG,theme,.16))
        draw.text((3,y),f"T{track:02d}",font=FONT_10,fill=theme if selected_row else tcol)
        for e in entries:
            if e["end"] <= t0 or e["start"] >= t1: continue
            cx1=max(x0,x0+int((e["start"]-t0)/span*(x2-x0)))
            cx2=min(x2,x0+int((e["end"]-t0)/span*(x2-x0)))
            if cx2 <= cx1: cx2=cx1+1
            playing=bool(state.get("playing",0)) and e["pos"]==play
            editing=selected_row and (bool(e.get("virtual_edit")) or (e["start"] <= edit_tick < e["end"] and not e.get("virtual_gap")))
            edit_mode=editing and bool(info.get("celledit",state.get("arrangercelledit",0)))
            empty=e["slot"]<0; rest=e["slot"]==0
            if playing: fill=blend(PANEL,PLAYHEAD,.24)
            elif editing: fill=blend(PANEL,theme,.42 if edit_mode else .25)
            elif empty: fill=(14,16,19)
            elif rest: fill=blend(GRID_A,MUTED,.10)
            else: fill=GRID_A
            outline=PLAYHEAD if playing else ((245,245,245) if edit_mode else (theme if editing else ((37,42,49) if empty else (48,54,62))))
            draw.rectangle((cx1,y-1,cx2-1,y+row_h-3),fill=fill,outline=outline,width=2 if edit_mode else 1)
            if cx2-cx1 >= 11:
                centered_text(draw,(cx1,y-2,cx2-1,y+row_h-3),_arranger_cell_label(e),FONT_10,TEXT if (playing or editing) else MUTED)
            if playing and e["pos"] <= len(info.get("sequence",[])):
                _draw_arranger_progress(draw,(cx1,y-1,cx2-1,y+row_h-3),progress,PLAYHEAD,False)
    _draw_horizontal_scrollbar(draw,state,t0,t1)
    return image


def render_song_arranger_vertical(state):
    image=new_screen(); draw=ImageDraw.Draw(image)
    selected=max(1,min(16,int(state.get("track",1)))); theme=track_colour(selected)
    draw_top_guides(draw,state,right=False)
    t0,t1=_arranger_time_window(state); span=max(1,t1-t0)
    edit=int(state.get("arrangereditpos",1)); edit_tick=max(0,int(state.get("arrangeredittick",0))); mode="EDIT" if bool(state.get("arrangercelledit",0)) else "NAV"
    songmode=str(state.get("songmode","LOOP")).upper()
    draw.rectangle((0,27,479,50),fill=blend(HEADER,theme,.30))
    draw.text((6,31),"ARRANGER V",font=FONT_13,fill=TEXT)
    draw.text((102,32),f"T{selected:02d} POS {edit:03d} TICK {edit_tick}",font=FONT_10,fill=theme)
    draw.text((363,32),f"{mode} Z{int(state.get('arrangerzoom',3))}",font=FONT_10,fill=PLAYHEAD if songmode=="TRUE" else MUTED)
    left=29; y0=66; y2=264; col_w=(474-left)//16
    for track in range(1,17):
        x=left+(track-1)*col_w; tcol=track_colour(track)
        if track==selected: draw.rectangle((x,51,x+col_w-1,264),fill=blend(BG,theme,.12))
        centered_text(draw,(x,51,x+col_w-1,64),str(track),FONT_10,theme if track==selected else tcol)

    play_time=_arranger_playhead_time(state)
    if play_time is not None and t0 <= play_time <= t1:
        py=y0+int((play_time-t0)/span*(y2-y0))
        draw.line((left,py,474,py),fill=PLAYHEAD,width=1)

    for track in range(1,17):
        info=state.get("arrsongs",{}).get(track,{})
        entries=_arranger_entries_cover_time(info,t1,edit_tick if track==selected else None); play=int(info.get("playpos",0)); progress=int(info.get("progress",0)); editpos=int(info.get("editpos",1))
        x=left+(track-1)*col_w
        for e in entries:
            if e["end"] <= t0 or e["start"] >= t1: continue
            cy1=max(y0,y0+int((e["start"]-t0)/span*(y2-y0)))
            cy2=min(y2,y0+int((e["end"]-t0)/span*(y2-y0)))
            if cy2 <= cy1: cy2=cy1+1
            playing=bool(state.get("playing",0)) and e["pos"]==play
            editing=(track==selected and (bool(e.get("virtual_edit")) or (e["start"] <= edit_tick < e["end"] and not e.get("virtual_gap"))))
            edit_mode=editing and bool(info.get("celledit",state.get("arrangercelledit",0)))
            empty=e["slot"]<0; rest=e["slot"]==0
            if playing: fill=blend(PANEL,PLAYHEAD,.24)
            elif editing: fill=blend(PANEL,theme,.42 if edit_mode else .25)
            elif empty: fill=(14,16,19)
            elif rest: fill=blend(GRID_A,MUTED,.10)
            else: fill=GRID_A
            outline=PLAYHEAD if playing else ((245,245,245) if edit_mode else (theme if editing else ((37,42,49) if empty else (45,50,58))))
            draw.rectangle((x,cy1,x+col_w-2,cy2-1),fill=fill,outline=outline,width=2 if edit_mode else 1)
            if cy2-cy1 >= 10:
                centered_text(draw,(x,cy1,x+col_w-2,cy2-1),_arranger_cell_label(e),FONT_10,TEXT if (playing or editing) else MUTED)
            if playing and e["pos"] <= len(info.get("sequence",[])):
                _draw_arranger_progress(draw,(x,cy1,x+col_w-2,cy2-1),progress,PLAYHEAD,True)
    _draw_vertical_scrollbar(draw,state,t0,t1)
    return image


def render_song_arranger(state):
    if str(state.get("arrangerview","HORIZONTAL")).upper()=="VERTICAL":
        return render_song_arranger_vertical(state)
    return render_song_arranger_horizontal(state)


def render_song_arranger_details(state):
    image=new_screen(); draw=ImageDraw.Draw(image); track=int(state.get("track",1)); theme=track_colour(track)
    draw_top_guides(draw,state,right=True)
    info=state.get("arrsongs",{}).get(track,{})
    seq=list(info.get("sequence",state.get("arrseq",[]))); edit=max(1,int(info.get("editpos",state.get("arrangereditpos",1)))); play=max(0,int(info.get("playpos",state.get("arrangerplaypos",0))))
    progress=max(0,min(1000,int(info.get("progress",0)))); active=max(0,min(16,int(info.get("activepattern",state.get("playpattern",1)))))
    view=str(state.get("arrangerview","HORIZONTAL")).upper(); cell_edit=bool(info.get("celledit",state.get("arrangercelledit",0)))
    current=seq[edit-1] if edit<=len(seq) else -1
    rest_lengths=info.get("restlengths",[]); rest_divs=info.get("restdivisions",[])
    silent=current <= 0
    rest_len=int(rest_lengths[edit-1]) if silent and edit-1 < len(rest_lengths) and int(rest_lengths[edit-1])>0 else 16
    rest_div=int(rest_divs[edit-1]) if silent and edit-1 < len(rest_divs) and int(rest_divs[edit-1])>0 else 16

    draw.rectangle((0,27,479,61),fill=blend(HEADER,theme,.34)); centered_text(draw,(4,29,476,59),"SONG ARRANGER",FONT_18B,theme)
    status="CELL EDIT" if cell_edit else "NAVIGATE"
    draw.text((10,67),f"T{track:02d} {view}  {state.get('songmode','LOOP')} SONG",font=FONT_13,fill=TEXT)
    draw.text((342,67),status,font=FONT_11,fill=(245,245,245) if cell_edit else MUTED)
    edit_tick=max(0,int(state.get("arrangeredittick",0)))
    draw.text((10,88),f"EDIT {edit:03d}  TICK {edit_tick}",font=FONT_15,fill=theme)
    draw.text((300,88),f"PLAY {play:03d}" if play else "PLAY --",font=FONT_15,fill=PLAYHEAD if play else MUTED)

    if current < 0: edit_text="EDIT: EMPTY"
    elif current == 0: edit_text=f"EDIT: REST  L{rest_len} @ 1/{rest_div}"
    else: edit_text=f"EDIT: P{current:02d}"
    draw.rounded_rectangle((8,111,471,141),radius=6,fill=blend(PANEL,theme,.27 if cell_edit else .08),outline=(245,245,245) if cell_edit else theme,width=2 if cell_edit else 1)
    centered_text(draw,(12,113,467,139),edit_text,FONT_17,(245,245,245) if cell_edit else TEXT)

    if cell_edit:
        draw.text((10,151),"4-D TURN",font=FONT_11,fill=theme); draw.text((94,151),"EMPTY / REST / CREATED PATTERNS",font=FONT_11,fill=TEXT)
        draw.text((10,169),"4-D PUSH",font=FONT_11,fill=theme); draw.text((94,169),"DONE",font=FONT_11,fill=TEXT)
    else:
        draw.text((10,151),"4-D DIR",font=FONT_11,fill=theme); draw.text((94,151),"MOVE TRACK + CELL",font=FONT_11,fill=TEXT)
        draw.text((10,169),"FOLLOW+1/2",font=FONT_11,fill=theme); draw.text((110,169),"ZOOM OUT / IN",font=FONT_11,fill=TEXT)

    if current==0:
        draw.text((10,191),"K8",font=FONT_11,fill=theme); draw.text((48,191),f"REST LENGTH {rest_len} STEPS @ 1/{rest_div}",font=FONT_11,fill=TEXT)
    elif current < 0:
        draw.text((10,191),"EMPTY",font=FONT_11,fill=MUTED); draw.text((70,191),"SILENT / UNASSIGNED",font=FONT_11,fill=MUTED)
    else:
        draw.text((10,191),"CLEAR",font=FONT_11,fill=theme); draw.text((70,191),"PATTERN -> SAME-LENGTH REST",font=FONT_11,fill=TEXT)

    draw.text((10,215),"1/2 MOVE   3 COPY   4 PASTE   5 DUP   6 REST",font=FONT_10,fill=MUTED)
    draw.text((10,231),"7 CLEAR: PAT>REST>EMPTY   8 CLEAR TRACK",font=FONT_10,fill=MUTED)
    draw.text((10,247),"FOLLOW + PAD1/PAD2 = UNDO / REDO",font=FONT_10,fill=MUTED)
    draw.text((10,259),"SETTINGS: P2 VIEW   P10 LOOP/TRUE SONG",font=FONT_10,fill=MUTED)
    return image


def render_variation_mode(state):
    image=new_screen(); draw=ImageDraw.Draw(image); theme=track_colour(int(state.get("track",1)))
    bias=max(0,min(3,int(state.get("variationbias",0))))
    strength=max(0,min(100,int(state.get("variationstrength",0))))
    octaves=max(1,min(2,int(state.get("variationoctaves",1))))
    scale_on=bool(state.get("scalelock",0)); scale_name=str(state.get("scalename","CHROMATIC"))

    cyan=(44,184,205); orange=(242,122,52); green=(68,186,98); violet=(157,96,220); white=(245,245,245)
    bias_names=["OFF","ROOT/OCT","ROOT+5TH","ROOT+4TH+5TH"]

    # V0.45.5 uses the extra right-screen space for descriptive multi-line
    # labels instead of the older CUR/ALL abbreviations.
    labels={
        1:"RANDOM STEP\nPROBABILITY\n0-100",       2:"RANDOM STEP\nPROBABILITY\n50-100",
        3:"RANDOM PATTERN\nPROBABILITY\n0-100",    4:"RANDOM PATTERN\nPROBABILITY\n50-100",
        5:"RANDOM STEP\nVELOCITY\n60-127",          6:"RANDOM STEP\nVELOCITY\n1-59",
        7:"RANDOM PATTERN\nVELOCITY\n60-127",       8:"RANDOM PATTERN\nVELOCITY\n1-59",
        9:"RANDOM NOTE\nDENSITY\n25%",             10:"RANDOM NOTE\nDENSITY\n50%",
        11:"RANDOM NOTE\nDENSITY\n75%",            12:"RANDOM NOTE\nDENSITY\n100%",
        13:"RANDOM NOTE\nGENERATOR BIAS",
        14:"BIAS\nPERCENTAGE\n" + ("OFF" if strength==0 else f"{strength}%"),
        15:"OCTAVE\nSPAN -",                          16:"OCTAVE\nSPAN +",
    }
    colours={**{i:cyan for i in range(1,5)},**{i:orange for i in range(5,9)},**{i:green for i in range(9,13)},13:violet,14:violet,15:white,16:white}

    draw.rectangle((0,0,479,37),fill=blend(HEADER,theme,.28))
    centered_text(draw,(4,2,476,35),"VARIATION",FONT_21B,white)
    source=(f"SCALE {scale_name}" if scale_on else "CHROMATIC")
    draw.text((8,43),f"NOTE GEN: {source}",font=FONT_10,fill=theme)
    draw.text((269,43),f"SPAN {octaves} OCT",font=FONT_10,fill=TEXT)
    draw.text((375,43),f"BIAS {bias_names[bias]}",font=FONT_10,fill=violet if bias else MUTED)

    boxes=_pad_grid_boxes(grid_y=60,pad_h=46,gap_y=3)
    for pad,box in boxes.items():
        col=colours[pad]; x1,y1,x2,y2=box
        fill=blend(PANEL,col,.34 if pad<=14 else .18)
        draw.rounded_rectangle(box,radius=5,fill=fill,outline=col,width=1)
        draw.text((x1+4,y1+3),f"P{pad}",font=FONT_10,fill=MUTED if pad<15 else (85,85,85))
        # Reserve the left edge for P# and use the remaining width for a
        # three-line description. Bias P13 leaves a little room for its arrow.
        right_pad = 15 if pad == 13 else 3
        centered_multiline_text(
            draw,
            (x1+17,y1+2,x2-right_pad,y2-2),
            labels[pad],
            FONT_10,
            BG if pad>=15 else TEXT,
            spacing=0,
        )

    # Show the Bias -> Percentage relationship directly on the two pads.
    b13=boxes[13]; b14=boxes[14]
    arrow_y=(b13[1]+b13[3])//2
    arrow_col=violet if bias>0 else MUTED
    arrow_start=b13[2]-13; arrow_end=b14[0]+3
    draw.line((arrow_start,arrow_y,arrow_end,arrow_y),fill=arrow_col,width=2)
    draw.line((arrow_end,arrow_y,arrow_end-4,arrow_y-3),fill=arrow_col,width=2)
    draw.line((arrow_end,arrow_y,arrow_end-4,arrow_y+3),fill=arrow_col,width=2)

    centered_text(draw,(6,258,474,271),"VARIATION = EXIT • P9-12 CLEAR + REGENERATE CURRENT PATTERN",FONT_10,MUTED)
    return image

def render_details(state):
    if bool(state.get("tutorialactive",0)):
        return render_tutorial(state)
    if bool(state.get("settingsheld",0)):
        return render_settings(state)
    if bool(state.get("patternmode",0)):
        return render_pattern_mode(state)
    if bool(state.get("patternselect",0)):
        return render_pattern_select(state)
    if bool(state.get("sensitivitymode",0)):
        return render_sensitivity_mode(state)
    if bool(state.get("scaleedit",0)):
        return render_scale_mode(state)
    if bool(state.get("variationheld",0)):
        return render_variation_mode(state)
    if bool(state.get("directnote",0)):
        return render_direct_note_mode(state)
    if int(state.get("jamtouchmode",0)) > 0:
        return render_touch_mode(state)
    if bool(state.get("tempomode",0)):
        return render_tempo_mode(state)

    image=new_screen(); draw=ImageDraw.Draw(image); track=int(state.get("track",1)); cursor=int(state.get("cursor",1)); pattern=int(state.get("pattern",16)); theme=track_colour(track)
    draw_top_guides(draw,state,right=True)
    draw.rectangle((0,28,479,52),fill=blend(HEADER,theme,.25))
    draw.text((6,32),f"T{track:02d} {str(state.get('trackname',''))[:12]}",font=FONT_13,fill=theme)
    draw.text((173,32),f"{cursor:03d}/{pattern:03d}",font=FONT_13,fill=TEXT)
    draw.text((268,32),f"BANK {chr(65+max(0,min(7,int(state.get('page',0)))))}",font=FONT_13,fill=theme)
    four="NOTE CHANGE" if bool(state.get("fourmode",0)) else "STEP SELECT"
    draw.text((357,34),four,font=FONT_10,fill=ACCENT)

    padnotes=state.get("padnotes",[]); pad_mode=bool(state.get("padmode",0)); shift=bool(state.get("shiftmod",0))
    rows=[[13,14,15,16],[9,10,11,12],[5,6,7,8],[1,2,3,4]]; x0=7; y0=58; pw=53; ph=24; gx=4; gy=3
    for ri,row in enumerate(rows):
        for ci,pad in enumerate(row):
            x1=x0+ci*(pw+gx); y1=y0+ri*(ph+gy); x2=x1+pw; y2=y1+ph
            is_white=(pad>=13); col=(245,245,245) if is_white else theme
            fill=blend(PANEL,col,.45); txt=BG if is_white else TEXT
            note = padnotes[pad-1] if pad-1 < len(padnotes) else -1
            if pad<=12: label=note_name(note)
            elif pad==13: label="S-"
            elif pad==14: label="S+"
            elif pad==15: label="P-12" if shift else "O-"
            else: label="P+12" if shift else "O+"
            draw.rounded_rectangle((x1,y1,x2,y2),radius=3,fill=fill,outline=col,width=1)
            draw.text((x1+3,y1+3),str(pad),font=FONT_10,fill=MUTED if not is_white else (85,85,85)); draw.text((x1+17,y1+2),label,font=FONT_10,fill=txt)

    data=state.get("steps",{}).get(cursor,{})
    px1,py1,px2,py2=242,58,473,167
    draw.rounded_rectangle((px1,py1,px2,py2),radius=6,fill=blend(PANEL,theme,.18),outline=theme,width=1)
    draw.text((252,68),"SELECTED NOTE",font=FONT_10,fill=MUTED); draw.text((252,82),note_name(data.get("note",-1)),font=FONT_21B,fill=TEXT)
    draw.text((336,87),f"VEL {int(data.get('velocity',100)):03d}",font=FONT_11,fill=theme)
    draw.text((252,115),command_long_name(data.get("command",0)),font=FONT_13,fill=theme if int(data.get("command",0)) else MUTED)
    current_cmd=int(data.get("command",0)); current_param=int(data.get("parameter",0))
    display_val=command_parameter_display(current_cmd,current_param)
    draw.text((252,137),f"LEN {int(data.get('length',1))}   PRB {int(data.get('probability',100))}%   VAL {display_val}",font=FONT_11,fill=TEXT)

    cmd_summary=f"{command_name(current_cmd)} {command_range_text(current_cmd)}"
    items=[("CMD",cmd_summary), ("VAL",display_val), ("UNUSED","--"), ("STEP",f"{cursor:03d}")]
    for i,(lab,val) in enumerate(items):
        x1=4+i*118; x2=x1+113
        draw.rounded_rectangle((x1,174,x2,230),radius=5,fill=blend(PANEL,theme,.08),outline=(65,72,82),width=1)
        centered_text(draw,(x1,176,x2,196),lab,FONT_10,MUTED); centered_text(draw,(x1,198,x2,226),val,FONT_10 if lab=="CMD" else FONT_15,TEXT if lab!="UNUSED" else MUTED)

    quick=bool(state.get("quick",1)); slots=[("FIXED VEL",bool(state.get("fixed",0))), ("PAD MODE",pad_mode), ("UNUSED",False), ("UNUSED",False), ("STEP ON" if quick else "STEP LOCKED",quick)]
    bw=91; gap=4
    for i,(lab,active) in enumerate(slots):
        draw_toggle_badge(draw,4+i*(bw+gap),239,bw,lab,active,theme,active_colour=ACTIVE if i in (1,4) else None)
    return image


# ============================================================
# SINGLE-SCREEN MIDI VISUALISER
# ============================================================

VISUALISER_NAMES = {
    1: "NOTE RAIN",
    2: "PARTICLES",
    3: "ORBIT",
    4: "MIDI BARS",
    5: "TUNNEL",
    6: "STEP PULSE",
}


def _vis_colour(track, muted=False, solo=False, any_solo=False):
    colour = track_colour(track)
    if muted:
        return blend(BG, colour, 0.18)
    if any_solo and not solo:
        return blend(BG, colour, 0.24)
    return blend(colour, TEXT, 0.18 if solo else 0.0)


def _visualizer_active_tracks(state):
    tracks = state.get("vistracks", {})
    any_solo = any(bool(info.get("solo", 0)) for info in tracks.values())
    active = []
    for track in range(1, 17):
        info = tracks.get(track, {})
        note = int(info.get("note", -1))
        velocity = max(0, min(127, int(info.get("velocity", 0))))
        if note < 0 or velocity <= 0:
            continue
        colour = _vis_colour(track, bool(info.get("mute",0)), bool(info.get("solo",0)), any_solo)
        active.append((track, info, note, velocity, colour))
    return tracks, active, any_solo


def render_visualizer_left(state):
    image = new_screen()
    draw = ImageDraw.Draw(image)
    style = max(1, min(6, int(state.get("visualstyle", 1))))
    auto = bool(state.get("visualauto", 0))
    playing = bool(state.get("playing", 0))
    playhead = int(state.get("playhead", 1))
    phase = ((playhead - 1) % 16) / 16.0
    tracks, active, any_solo = _visualizer_active_tracks(state)

    # Fine grid keeps the single-screen effects anchored without dominating them.
    for x in range(0, SCREEN_W, 30):
        draw.line((x, 0, x, SCREEN_H), fill=(18, 21, 27), width=1)
    for y in range(32, SCREEN_H, 30):
        draw.line((0, y, SCREEN_W, y), fill=(17, 20, 25), width=1)

    if style == 1:  # NOTE RAIN
        for track, info, note, velocity, colour in active:
            x = int(18 + (track - 1) * 29.0)
            y = int(235 - (max(0,min(127,note)) / 127.0) * 185)
            tail = min(120, 16 + int(info.get("length",1)) * 7)
            width = max(2, min(6, velocity // 22 + 1))
            draw.rounded_rectangle((x-width, y, x+width, min(264, y+tail)), radius=width, fill=blend(BG,colour,.68), outline=colour)
            draw.ellipse((x-width-2,y-3,x+width+2,y+5),fill=colour)

    elif style == 2:  # PARTICLES
        for track, info, note, velocity, colour in active:
            cx = int(18 + (track - 1) * 29.0)
            cy = int(230 - max(0,min(127,note)) / 127.0 * 172)
            count = max(3, min(12, velocity // 12))
            radius = 4 + velocity // 24
            step = int(info.get("step",1)); cmd = int(info.get("command",0))
            extra = 3 if cmd in (1,6) else 0
            for j in range(count + extra):
                ang = track * 0.71 + step * 0.23 + j * 2.399 + phase * math.tau
                dist = radius + (j % 4) * (2 + velocity / 36)
                px = int(cx + math.cos(ang) * dist)
                py = int(cy + math.sin(ang) * dist)
                r = 1 + (j + track) % 3
                draw.ellipse((px-r,py-r,px+r,py+r),fill=blend(BG,colour,0.45 + 0.12*(j%3)))
            draw.ellipse((cx-radius//2,cy-radius//2,cx+radius//2,cy+radius//2),outline=colour,width=2)

    elif style == 3:  # ORBIT
        cx, cy = 240, 139
        for ring in range(1, 17):
            r = 18 + ring * 6
            draw.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(27,31,38),width=1)
        for track, info, note, velocity, colour in active:
            r = 18 + track * 6
            pitch_phase = (max(0,min(127,note)) / 127.0 - .5) * 1.35
            ang = phase * math.tau + track * 0.47 + pitch_phase
            x = int(cx + math.cos(ang) * r)
            y = int(cy + math.sin(ang) * r)
            size = 3 + velocity // 24
            draw.line((cx,cy,x,y),fill=blend(BG,colour,.28),width=1)
            draw.ellipse((x-size,y-size,x+size,y+size),fill=colour)

    elif style == 4:  # MIDI BARS
        base_y = 249
        cell = 29
        for track in range(1,17):
            info = tracks.get(track,{})
            note = int(info.get("note",-1)); vel=max(0,min(127,int(info.get("velocity",0))))
            x0 = 9 + (track-1)*cell
            colour = _vis_colour(track,bool(info.get("mute",0)),bool(info.get("solo",0)),any_solo)
            h = int(vel / 127.0 * 170) if note >= 0 else 3
            w = 17 + (max(0,note) % 7)
            draw.rounded_rectangle((x0,base_y-h,x0+w,base_y),radius=3,fill=blend(BG,colour,.58),outline=colour)
            draw.text((x0+2,253),str(track),font=FONT_10,fill=colour)

    elif style == 5:  # TUNNEL
        cx,cy=240,138
        for i in range(11):
            frac=((i/11.0)+phase)%1.0
            w=int(48+frac*410); h=int(20+frac*218)
            shade=int(28+frac*30)
            draw.rectangle((cx-w//2,cy-h//2,cx+w//2,cy+h//2),outline=(shade,shade+3,shade+8),width=1)
        for track,info,note,velocity,colour in active:
            x=int(35 + (track-1)/15.0*410)
            y=int(52 + (1-max(0,min(127,note))/127.0)*165)
            depth=((int(info.get("step",1))-1)%8)/8.0
            size=int(3 + velocity/24 + depth*5)
            draw.rectangle((x-size,y-size,x+size,y+size),outline=colour,width=2)
            draw.line((cx,cy,x,y),fill=blend(BG,colour,.23),width=1)

    else:  # STEP PULSE
        # 4x4 track cells pulse from the currently sounding note/velocity.
        margin_x, margin_y = 18, 48
        gap = 7
        cell_w = (SCREEN_W - margin_x*2 - gap*3) // 4
        cell_h = (SCREEN_H - margin_y - 18 - gap*3) // 4
        for track in range(1,17):
            row=(track-1)//4; col=(track-1)%4
            x1=margin_x+col*(cell_w+gap); y1=margin_y+row*(cell_h+gap)
            x2=x1+cell_w; y2=y1+cell_h
            info=tracks.get(track,{})
            note=int(info.get("note",-1)); vel=max(0,min(127,int(info.get("velocity",0))))
            colour=_vis_colour(track,bool(info.get("mute",0)),bool(info.get("solo",0)),any_solo)
            intensity=.08 + (vel/127.0)*.52 if note>=0 else .03
            draw.rounded_rectangle((x1,y1,x2,y2),radius=6,fill=blend(BG,colour,intensity),outline=colour if note>=0 else (48,53,61),width=2 if note>=0 else 1)
            draw.text((x1+6,y1+5),f"T{track:02d}",font=FONT_10,fill=colour if note>=0 else MUTED)
            centered_text(draw,(x1+4,y1+13,x2-4,y2-4),note_name(note) if note>=0 else "--",FONT_17,TEXT if note>=0 else MUTED)

    name = VISUALISER_NAMES.get(style, "VISUALISER")
    draw.rounded_rectangle((8,6,214,29),radius=5,fill=(18,21,27),outline=(55,61,70))
    draw.text((16,9),f"VISUALISER - {name}",font=FONT_11,fill=TEXT)
    draw.rounded_rectangle((264,6,471,29),radius=5,fill=(18,21,27),outline=(55,61,70))
    draw.text((274,9),f"ENC STYLE  PUSH AUTO {'ON' if auto else 'OFF'}",font=FONT_10,fill=ACTIVE if auto else MUTED)
    if not playing:
        draw.text((225,9),"STOP",font=FONT_11,fill=RED)
    return image


# ============================================================
# USB CONNECTION
# ============================================================

def connect():
    print()
    print("MASCHINE TRACKER DISPLAY V0.45.5")
    print("================================")
    print()

    print("Searching for Maschine MK3...")

    dev = usb.core.find(
        idVendor=VID,
        idProduct=PID,
        backend=backend,
    )

    if dev is None:
        raise RuntimeError(
            "Maschine MK3 17CC:1600 not found."
        )

    print("Maschine MK3 found.")
    print("Activating USB configuration 1...")

    try:
        dev.set_configuration(1)
        print("USB configuration 1 active.")

    except usb.core.USBError as exc:
        # On some systems configuration 1 may already be active.
        # The colour test proved that continuing can still work.
        print("set_configuration(1) returned:")
        print(exc)
        print("Continuing with current active configuration...")

    time.sleep(1.0)

    cfg = dev.get_active_configuration()

    interface = cfg[
        (
            DISPLAY_INTERFACE,
            0,
        )
    ]

    endpoint = usb.util.find_descriptor(
        interface,
        custom_match=lambda ep:
            ep.bEndpointAddress
            == DISPLAY_ENDPOINT,
    )

    if endpoint is None:
        raise RuntimeError(
            "Display endpoint 0x04 not found."
        )

    print(
        f"Display interface: {interface.bInterfaceNumber}"
    )

    print(
        f"Display endpoint: 0x{endpoint.bEndpointAddress:02X}"
    )

    print("Claiming display interface 5...")

    usb.util.claim_interface(
        dev,
        DISPLAY_INTERFACE,
    )
    print("Display interface claimed.")

    return dev


# ============================================================
# SEND SCREEN
# ============================================================

def send_screen(dev, screen, image):
    packet = image_packet(
        screen,
        image,
    )

    written = dev.write(
        DISPLAY_ENDPOINT,
        packet,
        timeout=5000,
    )

    if written != len(packet):
        raise RuntimeError(
            f"Short USB write on screen {screen}: "
            f"{written}/{len(packet)} bytes"
        )


# ============================================================
# STATE SIGNATURES
# ============================================================

def left_signature(state):
    flash=update_top_flash(state)
    cursor=int(state.get("displaycursor",state.get("cursor",1))); first=((cursor-1)//8)*8+1
    steps=tuple(tuple(sorted(state.get("steps",{}).get(i,{}).items())) for i in range(first,first+8))
    arr=tuple((i,tuple(sorted(state.get("arrtracks",{}).get(i,{}).items()))) for i in range(1,17))
    arrsong=tuple((i,repr(state.get("arrsongs",{}).get(i,{}))) for i in range(1,17))
    vis=tuple((i,tuple(sorted(state.get("vistracks",{}).get(i,{}).items()))) for i in range(1,17))
    return (state.get("track"),state.get("trackname"),state.get("cursor"),state.get("displaycursor"),state.get("playhead"),state.get("pattern"),state.get("patternslot"),state.get("playpattern"),state.get("playing"),state.get("column"),state.get("trackmute"),state.get("tracksolo"),state.get("knobfocus"),state.get("fourmode"),state.get("arranger"),state.get("arrangerview"),state.get("songmode"),state.get("arrangerzoom"),state.get("songtick"),state.get("overview"),state.get("arrangereditpos"),state.get("arrangerplaypos"),state.get("arrangercelledit"),state.get("patternselect"),state.get("playheadfollow"),state.get("visualizer"),state.get("visualstyle"),state.get("visualauto"),state.get("variationheld"),state.get("variationbias"),state.get("variationstrength"),state.get("variationoctaves"),state.get("arrangeredittick"),tuple(state.get("arrseq",[])),flash[:4],arr,arrsong,steps,vis)


def right_signature(state):
    flash=update_top_flash(state)
    current=tuple(sorted(state.get("steps",{}).get(int(state.get("cursor",1)),{}).items()))
    touch=tuple((i,tuple(sorted(state.get("touchsteps",{}).get(i,{}).items()))) for i in range(1,9))
    arr=tuple((i,tuple(sorted(state.get("arrtracks",{}).get(i,{}).items()))) for i in range(1,17))
    arrsong=tuple((i,repr(state.get("arrsongs",{}).get(i,{}))) for i in range(1,17))
    vis=tuple((i,tuple(sorted(state.get("vistracks",{}).get(i,{}).items()))) for i in range(1,17))
    keys=("track","trackname","cursor","pattern","patternslot","playpattern","playing","quick","fixed","wrap","octave","page","trackmute","tracksolo","knobfocus","patternmode","patternselect","fourmode","shiftmod","padmode","settingsheld","noteaudition","playheadfollow","banknavrelative","stepsizediv","tempomode","tempo","arranger","arrangerview","songmode","overview","arrangereditpos","arrangerplaypos","arrangercelledit","visualizer","visualstyle","visualauto","variationheld","variationbias","variationstrength","variationoctaves","arrangeredittick","notedivmode","sensitivitymode","sensitivityencoder","sensitivitylevel","sensitivitydemo","scaleedit","scalelock","scaleroot","scalename","directnote","directaudition","jamtouchmode","lengthtouchrange","tutorials","tutorialactive","tutorialmode","inheritvel","inheritln","inheritprob","inheritcmd","arrangerzoom","songtick")
    return tuple(state.get(k) for k in keys)+(tuple(state.get("padnotes",[])),tuple(state.get("patterncreated",[])),tuple(state.get("arrseq",[])),flash[4:],current,touch,arr,arrsong,vis)


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    dev = connect()
    claimed = True

    try:
        send_screen(
            dev,
            0,
            waiting_screen(True),
        )

        send_screen(
            dev,
            1,
            waiting_screen(False),
        )

        print()
        print("DISPLAY COMPANION READY")
        print("=======================")
        print()
        print(
            "Now open REAPER and run Maschine Tracker V0.45.5.lua"
        )
        print()
        print("State file:")
        print(STATE_FILE)
        print()
        print("Press Ctrl+C in this window to stop.")
        print()

        last_left = ("waiting",)
        last_right = ("waiting",)
        last_offline = False

        while True:
            state = read_state()

            if state is None:
                time.sleep(0.005)
                continue

            if not state["running"]:
                if not last_offline:
                    send_screen(
                        dev,
                        0,
                        waiting_screen(True),
                    )

                    send_screen(
                        dev,
                        1,
                        waiting_screen(False),
                    )

                    last_left = ("waiting",)
                    last_right = ("waiting",)
                    last_offline = True

                time.sleep(0.05)
                continue

            last_offline = False

            new_left = left_signature(state)
            new_right = right_signature(state)

            if bool(state.get("visualizer", 0)):
                if new_left != last_left:
                    send_screen(dev, 0, render_visualizer_left(state))
                    last_left = new_left
                if new_right != last_right:
                    send_screen(dev, 1, render_whats_new(state))
                    last_right = new_right
            else:
                if new_left != last_left:
                    if bool(state.get("arranger", 0)):
                        left_image = render_song_arranger(state)
                    elif bool(state.get("overview", 0)):
                        left_image = render_overview(state, 1, 8)
                    else:
                        left_image = render_tracker(state)

                    send_screen(dev, 0, left_image)
                    last_left = new_left

                if new_right != last_right:
                    special_overlay = (
                        bool(state.get("tutorialactive", 0))
                        or bool(state.get("settingsheld", 0))
                        or bool(state.get("patternmode", 0))
                        or bool(state.get("patternselect", 0))
                        or bool(state.get("notedivmode", 0))
                        or bool(state.get("sensitivitymode", 0))
                        or bool(state.get("scaleedit", 0))
                        or bool(state.get("directnote", 0))
                        or int(state.get("jamtouchmode", 0)) != 0
                        or bool(state.get("tempomode", 0))
                    )
                    if bool(state.get("arranger", 0)) and not special_overlay:
                        right_image = render_song_arranger_details(state)
                    elif bool(state.get("overview", 0)) and not special_overlay:
                        right_image = render_overview(state, 9, 16)
                    else:
                        right_image = render_details(state)

                    send_screen(
                        dev,
                        1,
                        right_image,
                    )

                    last_right = new_right

            time.sleep(0.008)

    except KeyboardInterrupt:
        print()
        print("Stopping display companion...")

    finally:
        if claimed:
            try:
                usb.util.release_interface(
                    dev,
                    DISPLAY_INTERFACE,
                )
            except Exception:
                pass

        usb.util.dispose_resources(
            dev
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print()
        print("DISPLAY COMPANION FAILED")
        print("========================")
        print()
        print(
            type(exc).__name__
            + ": "
            + str(exc)
        )
        print()
        input("Press Enter to close...")
        sys.exit(1)