-- MASCHINE TRACKER V0.45.5
-- Requires Maschine Tracker MIDI Engine V0.45.5
-- and Maschine Tracker Display V0.45.5.py

local VERSION = "0.45.2"

local NUM_TRACKS = 16
local NUM_PATTERNS = 16
local MAX_ARRANGER_STEPS = 128
local ARRANGER_EMPTY = -1
local MAX_STEPS = 512

local CELL_BASE = 20000
local CELL_SIZE = 7
local ACTIVE_DISPLAY_BASE = 80000
local DEFAULT_STEP_DIVISION = 16
local STEP_DIVISIONS = {1, 2, 4, 8, 16, 32, 64, 128}

local WINDOW_W = 960
local WINDOW_H = 1100
local ROW_HEIGHT = 28
local HEADER_HEIGHT = 138
local HEADER_Y = 138
local GRID_Y = 170
local FOOTER_HEIGHT = 186

local SEP = package.config:sub(1, 1)

local DISPLAY_STATE_PATH =
    (os.getenv("TEMP") or reaper.GetResourcePath())
    ..
    SEP
    ..
    "MaschineTrackerState.txt"

local last_display_state = nil

-- ============================================================
-- PROJECT PERSISTENCE
--
-- Stored inside the REAPER project (.RPP) using ProjExtState.
-- The project itself still needs to be saved in REAPER.
-- ============================================================

local PROJECT_EXT_SECTION = "MaschineTrackerV017"
local PROJECT_STATE_VERSION = 4

local last_saved_meta = nil
local last_saved_tracks = {}
local last_saved_mix = nil
local last_saved_lengths = nil
local last_saved_resolutions = nil
local project_state_loaded = false

local current_track = 1
local current_step = 1

-- V0.29 live REAPER instrument-track names.
-- Names are resolved from the MIDI Engine track's sends by MIDI source
-- channel, so Tracker Track N follows the destination of MIDI Ch N.
track_names = {}
last_track_name_refresh = 0
TRACK_NAME_REFRESH_SECONDS = 0.50

for track_index = 1, NUM_TRACKS do
    track_names[track_index] =
        string.format(
            "Track %02d",
            track_index
        )
end
local current_column = 1
local scroll_step = 1

local pattern_length = 16
local track_pattern_length = {}
local track_step_division = {}
local playing = false
local clipboard = nil

local quick_step_mode = true
local fixed_velocity = false
local step_wrapping = true

-- V0.35 Settings-page preferences.
-- WRAP already existed and remains ON by default.
note_audition_enabled = false
playhead_follow_enabled = false
bank_nav_relative = true
inherit_velocity = false
inherit_length = false
inherit_probability = false
inherit_command = false
-- V0.45.5 arranger preferences.
arranger_view_vertical = false
true_song_mode = false -- false = LOOP SONG, true = TRUE SONG
arranger_zoom_level = 3 -- 1..5, default fixed-scale 1024-tick view
arranger_clipboard = nil
settings_held = false

-- Transient hardware/display modes.
tempo_mode = false
-- V0.45.5: ARRANGER is now the per-track song-pattern sequencer.
arranger_mode = false
arranger_edit_position = {}
-- V0.45.5: one global musical-time edit anchor (1/128-note ticks) is preserved
-- while moving between arranger tracks. Per-track edit positions are derived
-- from this anchor instead of being copied by sequence index.
arranger_edit_tick = 0
-- V0.45.5: 4-D PUSH enters a dedicated arranger-cell edit mode.
arranger_cell_edit_mode = false
-- The former ARRANGER overview/visualiser entry point moved to BROWSER.
browser_overview_mode = false
pattern_select_mode = false
visualizer_mode = false
visualizer_style = 1
visualizer_auto = false
visualizer_last_cycle = 0
VISUALIZER_STYLE_COUNT = 6
note_division_mode = false
sensitivity_mode = false
scale_edit_mode = false

-- V0.45.5 latched VARIATION mode (Trigger button; press again to exit).
variation_held = false
variation_bias_mode = 0      -- 0 OFF, 1 ROOT/OCT, 2 ROOT+5TH, 3 ROOT+4TH+5TH
variation_bias_strength = 0  -- 0 only when Bias OFF; active values 25,50,75,100
variation_octave_span = 1    -- 1..2 octaves

-- Project musical scale state.
scale_lock_enabled = false
scale_root = 0
scale_index = 1

-- Jam performance/edit modes. Touch strips are tracker-edit-only in V0.45.5.
jam_direct_note_mode = false
jam_direct_audition_mode = false
jam_touch_mode = 0 -- 0 inactive, 1 velocity, 2 probability, 3 length
jam_h_sharp_held = false
jam_direct_held_notes = {}
jam_direct_written_steps = {}
length_touch_range = 4

-- Tutorial overlay state. Tutorial preference is a persistent user setting.
tutorials_enabled = true
tutorial_mode = ""
tutorial_until = 0
TUTORIAL_SECONDS = 2.6

-- Per-encoder sensitivity is a persistent user preference.
encoder_sensitivity = {5, 5, 5, 5, 5, 5, 5, 5}
sensitivity_selected_encoder = 1
sensitivity_demo_values = {60, 1, 100, 100, 0, 0, 0, 1}

-- Fine-resolution accumulator for the eight small MK3 encoders.
-- Normal = two detents per edit; FOLLOW = one detent per edit.
mk3_knob_accumulator = {0, 0, 0, 0, 0, 0, 0, 0}

local FIXED_VELOCITY_VALUE = 100

local entry_octave = 4

local default_velocity = 100
-- V0.45.5 new-note parameter defaults are fixed and no longer follow edited cells.
local NEW_NOTE_LENGTH = 1
local NEW_NOTE_PROBABILITY = 100
local NEW_NOTE_COMMAND = 0
local NEW_NOTE_PARAMETER = 0
-- Cell slot 3 is retained as a reserved compatibility field; pan is no longer used.
local RESERVED_CELL3 = 0
local default_pan = 64 -- legacy metadata slot only
local default_length = 1 -- legacy metadata slot only
local default_probability = 100 -- legacy metadata slot only

-- Legacy INST Lock state is retained only so older project metadata can migrate.
-- V0.45.5 routes each tracker track permanently to its matching MIDI channel.
inst_lock_mode = false

-- Seed Lua's PRNG once for the hardware randomisation functions.
math.randomseed(
    math.floor(
        reaper.time_precise()
        *
        1000000
    )
    %
    2147483647
)

-- Canonical values used whenever a step is cleared.
-- These do not change when the encoder changes entry defaults.
local RESET_VELOCITY = 100
local RESET_LENGTH = 1
local RESET_PROBABILITY = 100

local mk3_page = 0
local mk3_page_initialized = false

local MK3_PAGE_NAMES = {
    "A","B","C","D",
    "E","F","G","H"
}

-- Matching 16-track colour identity for the REAPER GUI.
local TRACK_GUI_COLOURS = {
    {0.92, 0.28, 0.28}, -- 1  red
    {0.18, 0.72, 0.82}, -- 2  cyan
    {0.92, 0.86, 0.22}, -- 3  yellow
    {0.58, 0.30, 0.82}, -- 4  violet
    {0.22, 0.72, 0.35}, -- 5  green
    {0.86, 0.34, 0.62}, -- 6  pink
    {0.25, 0.44, 0.88}, -- 7  blue
    {0.95, 0.48, 0.20}, -- 8  orange
    {0.20, 0.78, 0.58}, -- 9  mint
    {0.90, 0.24, 0.50}, -- 10 fuchsia
    {0.66, 0.84, 0.20}, -- 11 lime
    {0.48, 0.30, 0.70}, -- 12 plum
    {0.16, 0.60, 0.78}, -- 13 turquoise
    {0.96, 0.62, 0.25}, -- 14 light orange
    {0.70, 0.26, 0.76}, -- 15 purple
    {0.96, 0.76, 0.24}  -- 16 warm yellow
}

function current_track_colour()

    local index = current_track

    if index < 1 then
        index = 1
    elseif index > NUM_TRACKS then
        index = NUM_TRACKS
    end

    local c =
        TRACK_GUI_COLOURS[
            index
        ]

    return c[1], c[2], c[3]
end

local HW_WRITE = 12000
local HW_READ = 12001
local HW_BASE = 12010
local HW_STRIDE = 4
local HW_SLOTS = 64

-- Separate Maschine Jam queue. Using a second queue avoids write
-- collisions between the MK3 JSFX and the Jam bridge JSFX.
local JAM_WRITE = 13000
local JAM_READ = 13001
local JAM_HEARTBEAT = 13002
local JAM_BASE = 13010
local JAM_STRIDE = 4
local JAM_SLOTS = 64

-- Jam LED state table. The Jam bridge reads these 64 values and
-- sends only changes back to the controller. Values are NI
-- Indexed-mode MIDI colour values.
local JAM_LED_BASE = 14000
local JAM_LED_COUNT = 64

local JAM_SCENE_LED_BASE = 14080
local JAM_GROUP_LED_BASE = 14090

-- Per-tracker-track mixer state shared with the MIDI engine.
local TRACK_MUTE_BASE = 15000
local TRACK_VOLUME_BASE = 15020

local TRACK_SOLO_BASE = 15070
local ANY_SOLO_GMEM = 15086

local CURRENT_TRACK_GMEM = 15040

-- MK3 small encoders are tracker-only from V0.35 onward.
-- gmem 15050 is intentionally unused/free.
local PATTERN_MODE_GMEM = 15051
local MK3_PAGE_GMEM = 15052
local DIRECT_BANK_MODE_GMEM = 15053
local TEMPO_MODE_GMEM = 15054
local ARRANGER_MODE_GMEM = 15055
local SONG_MODE_GMEM = 15056
VISUALIZER_MODE_GMEM = 15210
VISUALIZER_STYLE_GMEM = 15211
VISUALIZER_AUTO_GMEM = 15212
VARIATION_MODE_GMEM = 15213
NOTES_MODE_GMEM = 15200
SENSITIVITY_MODE_GMEM = 15201
SCALE_LOCK_GMEM = 15202
SCALE_EDIT_GMEM = 15203
JAM_DIRECT_NOTE_MODE_GMEM = 15204
JAM_DIRECT_AUDITION_GMEM = 15205
JAM_TOUCH_MODE_GMEM = 15206
LENGTH_TOUCH_RANGE_GMEM = 15207
ANY_MUTE_GMEM = 15208
TUTORIALS_GMEM = 15209

-- V0.27 Jam track bank:
--   0 = Tracks 1-8
--   1 = Tracks 9-16
JAM_TRACK_BANK_GMEM = 15060
JAM_STEP_BANK_GMEM = 15061
jam_track_bank = 0
jam_step_bank = 0

PAD_MODE_GMEM = 15062
JAM_SELECT_MODE_GMEM = 15063
JAM_GROUP_MODE_GMEM = 15064
FOLLOW_MOD_GMEM = 15065

LOCK_STEP_GMEM = 15066
FIXED_VEL_GMEM = 15067
INST_LOCK_GMEM = 15068
SETTINGS_HELD_GMEM = 15069

AUDITION_TRIGGER_GMEM = 15300
AUDITION_NOTE_GMEM = 15301
AUDITION_VELOCITY_GMEM = 15302
AUDITION_CHANNEL_GMEM = 15303
AUDITION_GATE_MS_GMEM = 15304
RESTART_TRIGGER_GMEM = 15310
DIRECT_AUDITION_TRIGGER_GMEM = 15320
DIRECT_AUDITION_NOTE_GMEM = 15321
DIRECT_AUDITION_VELOCITY_GMEM = 15322
DIRECT_AUDITION_CHANNEL_GMEM = 15323
DIRECT_AUDITION_ON_GMEM = 15324

MK3_PAD_LED_BASE = 15600
MK3_PAD_NOTE_MAP_BASE = 15620
JAM_STRIP_LED_BASE = 14120

-- false = A-H control Mute, true = A-H control Solo.
jam_group_solo_mode = false

-- PAD_MODE_GMEM is retained as the hardware/display indicator slot,
-- but now represents NOTE AUDITION state. MK3 pads are always tracker entry.

-- Jam matrix behavior:
-- true  = current Select behavior
-- false = grid toggles note presence using last MK3 pad note
jam_select_mode = true

-- Last musical note played from the MK3 pad grid. Used by Jam Edit mode.
last_mk3_pad_note = -1

-- Momentary state driven by MK3 FOLLOW CC56.
follow_modifier = false

local TRACK_PATTERN_LENGTH_BASE = 15100
local TRACK_PLAYSTEP_BASE = 15120
local TRACK_STEP_DIV_BASE = 15140


-- CC86 Pattern-button mode.
-- false = 4-D rotary uses its normal Step/Note mode
-- true  = 4-D rotary temporarily edits PATTERN LENGTH
local pattern_edit_mode = false

-- 4-D rotary base mode. Deliberately not persisted so a new tracker
-- session always opens in the predictable Step Selector mode.
-- false = Step Selector Mode
-- true  = Note Change Mode
four_d_note_mode = false

-- Hardware focus used by the MK3 screens.
-- A touch or turn highlights the matching encoder parameter briefly.
local knob_focus = 0
local knob_focus_until = 0
local KNOB_FOCUS_SECONDS = 1.0

local track_muted = {}
local track_volume = {}
local track_solo = {}

-- Scene-button trigger flash state.
-- We remember each active tracker cell so a new Note On can produce
-- a short white flash even when another note on that track is still held.
local jam_scene_flash_until = {}
local jam_previous_active = {}

for track_index = 1, NUM_TRACKS do
    track_muted[track_index] = false
    track_volume[track_index] = 127
    track_solo[track_index] = false
    track_pattern_length[track_index] = 16
    track_step_division[track_index] = DEFAULT_STEP_DIVISION

    jam_scene_flash_until[track_index] = 0
    jam_previous_active[track_index] = {}

    for step_index = 1, MAX_STEPS do
        jam_previous_active[track_index][step_index] = false
    end
end

-- Tracks 1-16 use a fixed colour identity. The Jam matrix displays
-- all 64 steps for whichever tracker track is currently selected.
local TRACK_LED_BRIGHT = {
    6,   -- 1  Red
    38,  -- 2  Cyan
    22,  -- 3  Yellow
    54,  -- 4  Violet
    30,  -- 5  Green
    62,  -- 6  Pink
    46,  -- 7  Blue
    10,  -- 8  Orange
    34,  -- 9  Mint
    66,  -- 10 Fuchsia
    26,  -- 11 Lime
    50,  -- 12 Plum
    42,  -- 13 Turquoise
    14,  -- 14 Light Orange
    58,  -- 15 Purple
    18   -- 16 Warm Yellow
}

local TRACK_LED_DIM = {
    4, 36, 20, 52,
    28, 60, 44, 8,
    32, 64, 24, 48,
    40, 12, 56, 16
}

local JAM_LED_WHITE_DIM = 76
local JAM_LED_WHITE_BRIGHT = 127

local jam_last_heartbeat = -1
local jam_last_heartbeat_time = 0
local jam_connected = false

local COL_NOTE  = 1
local COL_VOL   = 2
local COL_CMD   = 3
local COL_PARAM = 4
local COL_LEN   = 5
local COL_PROB  = 6

local COLUMN_NAMES = {
    "NOTE",
    "VOL",
    "CMD",
    "PARAM",
    "LEN",
    "PROB"
}

local X_STEP  = 20
local X_NOTE  = 100
local X_VOL   = 235
local X_CMD   = 355
local X_PARAM = 465
local X_LEN   = 610
local X_PROB  = 725

local COLUMN_X = {
    X_NOTE,
    X_VOL,
    X_CMD,
    X_PARAM,
    X_LEN,
    X_PROB
}

local COLUMN_W = {
    120,
    105,
    95,
    130,
    100,
    105
}

local MODE_BUTTON_X = 540
local MODE_BUTTON_Y = 72
local MODE_BUTTON_W = 125
local MODE_BUTTON_H = 27

local FIXED_BUTTON_X = 675
local FIXED_BUTTON_Y = 72
local FIXED_BUTTON_W = 145
local FIXED_BUTTON_H = 27

local WRAP_BUTTON_X = 830
local WRAP_BUTTON_Y = 72
local WRAP_BUTTON_W = 110
local WRAP_BUTTON_H = 27

reaper.gmem_attach("MaschineTrackerV06")


function clamp(value, minimum, maximum)

    if value < minimum then
        return minimum
    end

    if value > maximum then
        return maximum
    end

    return value
end


function scale_127(value, minimum, maximum)

    value =
        clamp(
            value,
            0,
            127
        )

    return math.floor(
        minimum
        +
        (value / 127)
        *
        (maximum - minimum)
        +
        0.5
    )
end


function step_division_to_qn(division)

    division =
        clamp(
            math.floor(division or DEFAULT_STEP_DIVISION),
            1,
            128
        )

    return 4 / division
end


function normalize_step_division(division)

    division =
        math.floor(
            division
            or
            DEFAULT_STEP_DIVISION
        )

    local best = STEP_DIVISIONS[1]
    local best_distance = math.abs(division - best)

    for i = 2, #STEP_DIVISIONS do

        local candidate = STEP_DIVISIONS[i]
        local distance = math.abs(division - candidate)

        if distance < best_distance then
            best = candidate
            best_distance = distance
        end
    end

    return best
end


function step_division_index(division)

    division = normalize_step_division(division)

    for i = 1, #STEP_DIVISIONS do
        if STEP_DIVISIONS[i] == division then
            return i
        end
    end

    return 5
end


function current_step_division()
    local slot = selected_pattern(current_track)
    return normalize_step_division(
        pattern_divisions[current_track][slot]
        or track_step_division[current_track]
        or DEFAULT_STEP_DIVISION
    )
end


function current_step_qn()

    return
        step_division_to_qn(
            current_step_division()
        )
end


function cell_address(track, step)

    local index =
        ((track - 1) * MAX_STEPS)
        +
        (step - 1)

    return
        CELL_BASE
        +
        index * CELL_SIZE
end


function active_address(track, step)

    return
        ACTIVE_DISPLAY_BASE
        +
        ((track - 1) * MAX_STEPS)
        +
        (step - 1)
end


function get_visible_steps()

    local available =
        gfx.h
        -
        GRID_Y
        -
        FOOTER_HEIGHT

    return clamp(
        math.floor(
            available / ROW_HEIGHT
        ),
        6,
        MAX_STEPS
    )
end


function display_bank_start()

    return
        math.floor(
            (current_step - 1)
            /
            8
        )
        *
        8
        +
        1
end


function display_bank_end()

    return
        display_bank_start()
        +
        7
end


local NOTE_NAMES = {
    "C","C#","D","D#",
    "E","F","F#","G",
    "G#","A","A#","B"
}

SCALE_NAMES = {
    "MAJOR",
    "NAT MIN",
    "HARM MIN",
    "MELOD MIN",
    "DORIAN",
    "PHRYGIAN",
    "LYDIAN",
    "MIXOLYDIAN",
    "LOCRIAN",
    "MAJ PENTA",
    "MIN PENTA",
    "BLUES"
}

SCALE_INTERVALS = {
    {0, 2, 4, 5, 7, 9, 11},
    {0, 2, 3, 5, 7, 8, 10},
    {0, 2, 3, 5, 7, 8, 11},
    {0, 2, 3, 5, 7, 9, 11},
    {0, 2, 3, 5, 7, 9, 10},
    {0, 1, 3, 5, 7, 8, 10},
    {0, 2, 4, 6, 7, 9, 11},
    {0, 2, 4, 5, 7, 9, 10},
    {0, 1, 3, 5, 6, 8, 10},
    {0, 2, 4, 7, 9},
    {0, 3, 5, 7, 10},
    {0, 3, 5, 6, 7, 10}
}

LENGTH_TOUCH_VALUES = {1, 2, 3, 4, 6, 8, 12, 16, 32, 64}
PROBABILITY_VALUES = {0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100}
USER_EXT_SECTION = "MaschineTrackerUserV036"


function quantize_probability(value)
    value = clamp(math.floor((tonumber(value) or 0) + 0.5), 0, 100)
    return clamp(math.floor((value + 5) / 10) * 10, 0, 100)
end


function note_name(note)

    if note == nil
        or
        note < 0
    then
        return "---"
    end

    local pitch =
        note % 12

    local oct =
        math.floor(
            note / 12
        ) - 1

    return
        NOTE_NAMES[
            pitch + 1
        ]
        ..
        tostring(oct)
end


function octave_base_note()

    return
        (entry_octave + 1)
        *
        12
end


function current_scale_intervals()

    return
        SCALE_INTERVALS[
            clamp(
                math.floor(scale_index or 1),
                1,
                #SCALE_INTERVALS
            )
        ]
end


function scale_degree_note(degree_number)

    degree_number = math.max(1, math.floor(degree_number or 1))

    local intervals = current_scale_intervals()
    local count = #intervals
    local zero_based = degree_number - 1
    local octave_add = math.floor(zero_based / count)
    local interval = intervals[(zero_based % count) + 1]

    return
        octave_base_note()
        + scale_root
        + interval
        + octave_add * 12
end


function scale_pad_note(pad_index, performance_mode)

    pad_index = clamp(math.floor(pad_index or 0), 0, 15)

    if not scale_lock_enabled then
        return clamp(octave_base_note() + pad_index, 0, 127)
    end

    if performance_mode then
        return clamp(scale_degree_note(pad_index + 1), 0, 127)
    end

    if pad_index <= 6 then
        return clamp(scale_degree_note(pad_index + 1), 0, 127)
    elseif pad_index == 7 then
        return clamp(octave_base_note() + scale_root + 12, 0, 127)
    elseif pad_index == 8 then
        return clamp(octave_base_note() + scale_root + 24, 0, 127)
    elseif pad_index == 9 then
        return clamp(octave_base_note() + scale_root - 12, 0, 127)
    elseif pad_index == 10 then
        return clamp(octave_base_note() + scale_root - 24, 0, 127)
    elseif pad_index == 11 then
        local intervals = current_scale_intervals()
        local fifth_index = math.min(5, #intervals)
        return clamp(octave_base_note() + scale_root + intervals[fifth_index] - 12, 0, 127)
    end

    return clamp(octave_base_note() + pad_index, 0, 127)
end


function is_note_in_scale(note)

    if not scale_lock_enabled then
        return true
    end

    note = clamp(math.floor(note or 0), 0, 127)
    local relative = (note - scale_root) % 12
    local intervals = current_scale_intervals()

    for i = 1, #intervals do
        if intervals[i] == relative then
            return true
        end
    end

    return false
end


function next_scale_note(note, direction)

    direction = direction < 0 and -1 or 1
    note = clamp(math.floor(note or octave_base_note()), 0, 127)

    if not scale_lock_enabled then
        return clamp(note + direction, 0, 127)
    end

    local candidate = note

    for i = 1, 24 do
        candidate = clamp(candidate + direction, 0, 127)
        if is_note_in_scale(candidate) then
            return candidate
        end
        if candidate == 0 or candidate == 127 then
            break
        end
    end

    return note
end


function scale_name()

    return SCALE_NAMES[clamp(math.floor(scale_index or 1), 1, #SCALE_NAMES)]
end


function scale_root_name()

    return NOTE_NAMES[clamp(math.floor(scale_root or 0), 0, 11) + 1]
end


function set_entry_octave(value)

    entry_octave = clamp(math.floor(value or entry_octave), -1, 6)
    mk3_page = clamp(entry_octave + 1, 0, 7)
    mk3_page_initialized = true
    reaper.gmem_write(MK3_PAGE_GMEM, mk3_page)
    save_meta_state(true)
end


function start_tutorial(mode_name)

    if not tutorials_enabled then
        tutorial_mode = ""
        tutorial_until = 0
        return
    end

    tutorial_mode = tostring(mode_name or "")
    tutorial_until = reaper.time_precise() + TUTORIAL_SECONDS
end


function dismiss_tutorial()

    tutorial_until = 0
end


function tutorial_active()

    return
        tutorials_enabled
        and
        tutorial_mode ~= ""
        and
        reaper.time_precise() < tutorial_until
end


function save_user_preferences()

    reaper.SetExtState(
        USER_EXT_SECTION,
        "EncoderSensitivity",
        table.concat(encoder_sensitivity, ","),
        true
    )

    reaper.SetExtState(
        USER_EXT_SECTION,
        "TutorialScreens",
        tutorials_enabled and "1" or "0",
        true
    )

    reaper.SetExtState(
        USER_EXT_SECTION,
        "LengthTouchRange",
        tostring(length_touch_range),
        true
    )

    reaper.SetExtState(USER_EXT_SECTION, "InheritVelocity", inherit_velocity and "1" or "0", true)
    reaper.SetExtState(USER_EXT_SECTION, "InheritLength", inherit_length and "1" or "0", true)
    reaper.SetExtState(USER_EXT_SECTION, "InheritProbability", inherit_probability and "1" or "0", true)
    reaper.SetExtState(USER_EXT_SECTION, "InheritCommand", inherit_command and "1" or "0", true)
    reaper.SetExtState(USER_EXT_SECTION, "ArrangerView", arranger_view_vertical and "VERTICAL" or "HORIZONTAL", true)
    reaper.SetExtState(USER_EXT_SECTION, "SongMode", true_song_mode and "TRUE" or "LOOP", true)
end


function load_user_preferences()

    local sensitivity_text = reaper.GetExtState(USER_EXT_SECTION, "EncoderSensitivity")

    if sensitivity_text ~= nil and sensitivity_text ~= "" then
        local values = {}
        for value_text in string.gmatch(sensitivity_text, "([^,]+)") do
            values[#values + 1] = tonumber(value_text)
        end
        if #values == 8 then
            for i = 1, 8 do
                encoder_sensitivity[i] = clamp(math.floor(values[i] or 5), 1, 8)
            end
        end
    end

    local tutorial_text = reaper.GetExtState(USER_EXT_SECTION, "TutorialScreens")
    if tutorial_text ~= nil and tutorial_text ~= "" then
        tutorials_enabled = tutorial_text ~= "0"
    end

    local range_text = reaper.GetExtState(USER_EXT_SECTION, "LengthTouchRange")
    if range_text ~= nil and range_text ~= "" then
        length_touch_range = clamp(math.floor(tonumber(range_text) or 4), 4, #LENGTH_TOUCH_VALUES)
    end

    local function read_toggle(key)
        local text = reaper.GetExtState(USER_EXT_SECTION, key)
        return text ~= nil and text ~= "" and text ~= "0"
    end

    inherit_velocity = read_toggle("InheritVelocity")
    inherit_length = read_toggle("InheritLength")
    inherit_probability = read_toggle("InheritProbability")
    inherit_command = read_toggle("InheritCommand")

    local arranger_view_text = reaper.GetExtState(USER_EXT_SECTION, "ArrangerView")
    if arranger_view_text ~= nil and arranger_view_text ~= "" then
        arranger_view_vertical = string.upper(arranger_view_text) == "VERTICAL" or arranger_view_text == "1"
    end

    local song_mode_text = reaper.GetExtState(USER_EXT_SECTION, "SongMode")
    if song_mode_text ~= nil and song_mode_text ~= "" then
        true_song_mode = string.upper(song_mode_text) == "TRUE" or song_mode_text == "1"
    end
end


function command_name(command)

    local names = {
        [1] = "ROL",
        [2] = "DLY",
        [3] = "PIT",
        [4] = "MOD",
        [5] = "SHF",
        [6] = "FLM",
        [7] = "O2",
        [8] = "O3",
        [9] = "O4"
No readable content was found in this file for the requested mode.
No readable content was found in this file for the requested mode.
No readable content was found in this file for the requested mode.
No readable content was found in this file for the requested mode.
    }
    return names[command] or "-"
end


function command_long_name(command)

    local names = {
        [1] = "ROLL",
        [2] = "DELAY",
        [3] = "PITCH",
        [4] = "MOD",
        [5] = "SHIFT",
        [6] = "FLAM",
        [7] = "OCT2",
        [8] = "OCT3",
        [9] = "OCT4"
    }
    return names[command] or "NONE"
end


function pitch_display_value(value)
    value = clamp(math.floor(value or 64), 0, 127)
    if value <= 64 then
        return value - 64
    end
    return clamp(math.floor(((value - 64) * 64 / 63) + 0.5), 1, 64)
end


function signed_value_text(value)
    value = math.floor(value or 0)
    if value > 0 then return "+" .. tostring(value) end
    return tostring(value)
end


function command_default_parameter(command)
    if command == 1 then return 4 end       -- ROLL subdivisions / retriggers
    if command == 2 then return 0 end       -- DELAY 0..99%
    if command == 3 then return 64 end      -- PITCH neutral centre, shown as 0
    if command == 4 then return 0 end       -- MOD CC1
    if command == 5 then return 50 end      -- SHIFT centre (-50..+50% step)
    if command == 6 then return 12 end      -- FLAM spacing % step
    if command == 7 then return 4 end       -- OCT2 burst subdivision
    if command == 8 then return 4 end       -- OCT3 burst subdivision
    if command == 9 then return 4 end       -- OCT4 burst subdivision
    return 0
end


function command_parameter_clamp(command, value)
    value = math.floor(value or 0)
    if command == 1 then return clamp(value, 1, 16) end
    if command == 2 then return clamp(value, 0, 99) end
    if command == 3 then return clamp(value, 0, 127) end
    if command == 4 then return clamp(value, 0, 127) end
    if command == 5 then return clamp(value, 0, 100) end
    if command == 6 then return clamp(value, 1, 99) end
    if command == 7 then return clamp(value, 2, 16) end
    if command == 8 then return clamp(value, 3, 16) end
    if command == 9 then return clamp(value, 4, 16) end
    return 0
end


function command_value_for_display(command, value)
    if command == 3 then
        return pitch_display_value(value)
    elseif command == 5 then
        return command_parameter_clamp(5, value) - 50
    end
    return command_parameter_clamp(command, value)
end


function command_range_text(command)
    if command == 1 then return "1..16" end
    if command == 2 then return "0..99%" end
    if command == 3 then return "-64..+64" end
    if command == 4 then return "0..127" end
    if command == 5 then return "-50..+50%" end
    if command == 6 then return "1..99%" end
    if command == 7 then return "2..16" end
    if command == 8 then return "3..16" end
    if command == 9 then return "4..16" end
    return "--"
end


function command_value_text(cell)
    if cell.command == 1 then
        return "REPEATS " .. tostring(command_parameter_clamp(1, cell.parameter)) .. "x / STEP"
    elseif cell.command == 2 then
        return "DELAY " .. tostring(command_parameter_clamp(2, cell.parameter)) .. "% OF STEP"
    elseif cell.command == 3 then
        return "PITCH " .. signed_value_text(pitch_display_value(cell.parameter))
    elseif cell.command == 4 then
        return "MOD CC1 " .. tostring(command_parameter_clamp(4, cell.parameter))
    elseif cell.command == 5 then
        return "SHIFT " .. signed_value_text(command_parameter_clamp(5, cell.parameter) - 50) .. "% STEP"
    elseif cell.command == 6 then
        return "FLAM +" .. tostring(command_parameter_clamp(6, cell.parameter)) .. "% STEP"
    elseif cell.command == 7 then
        return "OCT2 RATE " .. tostring(command_parameter_clamp(7, cell.parameter)) .. " / STEP"
    elseif cell.command == 8 then
        return "OCT3 RATE " .. tostring(command_parameter_clamp(8, cell.parameter)) .. " / STEP"
    elseif cell.command == 9 then
        return "OCT4 RATE " .. tostring(command_parameter_clamp(9, cell.parameter)) .. " / STEP"
    end
    return "NO COMMAND"
end

function new_empty_cell()

    return {
        note = -1,
        velocity = RESET_VELOCITY,
        pan = RESERVED_CELL3,
        command = 0,
        parameter = 0,
        length = RESET_LENGTH,
        probability = RESET_PROBABILITY
    }
end


local tracker = {}

for track = 1, NUM_TRACKS do

    tracker[track] = {}

    for step = 1, MAX_STEPS do

        tracker[track][step] =
            new_empty_cell()
    end
end


-- ============================================================
-- V0.45.5 PER-TRACK PATTERN SLOTS / SONG ARRANGER
-- ============================================================

pattern_store = {}
pattern_created = {}
selected_pattern_slot = {}
pattern_lengths = {}
pattern_divisions = {}
arranger_sequences = {}
arranger_rest_lengths = {}
arranger_rest_divisions = {}

local MULTI_PATTERN_CELL_BASE = 90000
local PATTERN_LENGTH_MATRIX_BASE = 88200
local PATTERN_DIV_MATRIX_BASE = 88460
local TRACK_ACTIVE_PATTERN_BASE = 88720
local TRACK_SELECTED_PATTERN_BASE = 88740
local ARRANGER_LENGTH_BASE = 88760
local ARRANGER_PLAY_POS_BASE = 88780
local ARRANGER_SEQUENCE_BASE = 951000
-- V0.45.5 arranger REST metadata. A sequence value of 0 means REST.
local ARRANGER_REST_LENGTH_BASE = 954000
local ARRANGER_REST_DIV_BASE = 956100
local TRACK_ACTIVE_REST_BASE = 958200
local TRACK_PENDING_PATTERN_BASE = 958240
local ARRANGER_SONG_TICK_GMEM = 958280
local PATTERN_SELECT_MODE_GMEM = 15057
local BROWSER_MODE_GMEM = 15058

local function new_empty_pattern()
    local result = {}
    for step = 1, MAX_STEPS do
        result[step] = new_empty_cell()
    end
    return result
end

for track = 1, NUM_TRACKS do
    pattern_store[track] = {}
    pattern_created[track] = {}
    pattern_lengths[track] = {}
    pattern_divisions[track] = {}
    arranger_sequences[track] = {}
    arranger_rest_lengths[track] = {}
    arranger_rest_divisions[track] = {}
    arranger_edit_position[track] = 1
    selected_pattern_slot[track] = 1

    pattern_store[track][1] = tracker[track]
    pattern_created[track][1] = true
    pattern_lengths[track][1] = track_pattern_length[track] or 16
    pattern_divisions[track][1] = track_step_division[track] or DEFAULT_STEP_DIVISION

    for slot = 2, NUM_PATTERNS do
        pattern_store[track][slot] = new_empty_pattern()
        pattern_created[track][slot] = false
        pattern_lengths[track][slot] = 16
        pattern_divisions[track][slot] = DEFAULT_STEP_DIVISION
    end
end

function selected_pattern(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    return clamp(math.floor(selected_pattern_slot[track_index] or 1), 1, NUM_PATTERNS)
end

function pattern_cell_address(track_index, pattern_slot, step)
    track_index = clamp(math.floor(track_index), 1, NUM_TRACKS)
    pattern_slot = clamp(math.floor(pattern_slot), 1, NUM_PATTERNS)
    step = clamp(math.floor(step), 1, MAX_STEPS)

    if pattern_slot == 1 then
        return cell_address(track_index, step)
    end

    local index =
        ((track_index - 1) * (NUM_PATTERNS - 1) + (pattern_slot - 2)) * MAX_STEPS
        + (step - 1)

    return MULTI_PATTERN_CELL_BASE + index * CELL_SIZE
end

function pattern_meta_address(base, track_index, pattern_slot)
    return base + (track_index - 1) * NUM_PATTERNS + (pattern_slot - 1)
end

function sync_tracker_alias(track_index)
    local slot = selected_pattern(track_index)
    tracker[track_index] = pattern_store[track_index][slot]
    track_pattern_length[track_index] = pattern_lengths[track_index][slot] or 16
    track_step_division[track_index] = pattern_divisions[track_index][slot] or DEFAULT_STEP_DIVISION
end

function sync_all_tracker_aliases()
    for track_index = 1, NUM_TRACKS do
        sync_tracker_alias(track_index)
    end
end


-- ============================================================
-- V0.35 MULTI-LEVEL EDIT HISTORY
-- ============================================================

UNDO_HISTORY_LIMIT = 64
undo_history = {}
redo_history = {}
history_current_snapshot = nil
history_restoring = false


-- ============================================================
-- PROJECT SAVE / RESTORE
-- ============================================================

function serialize_track(track_index, pattern_slot)

    local cells = {}
    pattern_slot = clamp(math.floor(pattern_slot or selected_pattern(track_index)), 1, NUM_PATTERNS)
    local source_pattern = pattern_store[track_index][pattern_slot]

    for step = 1, MAX_STEPS do

        local c =
            source_pattern[step]

        cells[#cells + 1] =
            table.concat(
                {
                    c.note,
                    c.velocity,
                    c.pan,
                    c.command,
                    c.parameter,
                    c.length,
                    c.probability
                },
                ","
            )
    end

    return table.concat(
        cells,
        ";"
    )
end


function parse_track_string(text, source_version)

    local restored = {}
    local step = 1

    for cell_text in string.gmatch(
        text or "",
        "([^;]+)"
    ) do

        if step > MAX_STEPS then
            return nil
        end

        local values = {}

        for value_text in string.gmatch(
            cell_text,
            "([^,]+)"
        ) do

            values[#values + 1] =
                tonumber(value_text)
        end

        if #values ~= 7 then
            return nil
        end

        for i = 1, 7 do
            if values[i] == nil then
                return nil
            end
        end

        local saved_command = clamp(math.floor(values[4]), 0, 9)
        local migrated_command = saved_command
        local migrated_parameter = clamp(math.floor(values[5]), 0, 127)

        -- V0.36 and older used: 1 ROLL, 2 CHANCE, 3 reserved MIDI CC, 4 DELAY.
        -- V0.38 removes CHANCE/INST and uses: 1 ROLL, 2 DELAY, 3 PITCH,
        -- 4 MOD, 5 SHIFT, 6 FLAM. Probability is already its own field.
        if (source_version or 1) < 2 then
            if saved_command == 4 then
                migrated_command = 2
            elseif saved_command == 2 or saved_command == 3 then
                migrated_command = 0
                migrated_parameter = 0
            end
        end

        migrated_parameter = command_parameter_clamp(migrated_command, migrated_parameter)

        restored[step] = {
            note = clamp(math.floor(values[1]), -1, 127),
            velocity = clamp(math.floor(values[2]), 0, 127),
            pan = RESERVED_CELL3,
            command = migrated_command,
            parameter = migrated_parameter,
            length = clamp(math.floor(values[6]), 1, 64),
            probability = quantize_probability(values[7])
        }

        step = step + 1
    end

    -- V0.35 expands patterns from 64 to 512 steps. Older projects
    -- therefore load their existing cells and receive empty cells for
    -- the newly-available range.
    if step <= 1 then
        return nil
    end

    while step <= MAX_STEPS do
        restored[step] = new_empty_cell()
        step = step + 1
    end

    return restored
end


function serialize_pattern_meta(track_index)
    local values = {selected_pattern(track_index)}
    for slot = 1, NUM_PATTERNS do
        values[#values + 1] = pattern_created[track_index][slot] and 1 or 0
        values[#values + 1] = clamp(math.floor(pattern_lengths[track_index][slot] or 16), 1, MAX_STEPS)
        values[#values + 1] = normalize_step_division(pattern_divisions[track_index][slot] or DEFAULT_STEP_DIVISION)
    end
    return table.concat(values, ",")
end

function save_pattern_meta_state(track_index)
    reaper.SetProjExtState(
        0,
        PROJECT_EXT_SECTION,
        string.format("patternmeta%02d", track_index),
        serialize_pattern_meta(track_index)
    )
end

function arranger_entry_duration_ticks(track_index, position)
    track_index = clamp(math.floor(track_index), 1, NUM_TRACKS)
    position = clamp(math.floor(position), 1, MAX_ARRANGER_STEPS)
    local sequence = arranger_sequences[track_index] or {}
    local slot = sequence[position]
    if slot == nil then return 0 end

    local entry_length
    local entry_division
    if slot <= 0 then
        entry_length = clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS)
        entry_division = normalize_step_division((arranger_rest_divisions[track_index] or {})[position] or DEFAULT_STEP_DIVISION)
    else
        entry_length = clamp(math.floor(pattern_lengths[track_index][slot] or 16), 1, MAX_STEPS)
        entry_division = normalize_step_division(pattern_divisions[track_index][slot] or DEFAULT_STEP_DIVISION)
    end

    return entry_length * math.max(1, math.floor(128 / entry_division))
end

function default_rest_metadata(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    local slot = selected_pattern(track_index)
    return
        clamp(math.floor(pattern_lengths[track_index][slot] or 16), 1, MAX_STEPS),
        normalize_step_division(pattern_divisions[track_index][slot] or DEFAULT_STEP_DIVISION)
end

function set_arranger_rest_metadata(track_index, position, rest_length, rest_division)
    track_index = clamp(math.floor(track_index), 1, NUM_TRACKS)
    position = clamp(math.floor(position), 1, MAX_ARRANGER_STEPS)
    arranger_rest_lengths[track_index][position] = clamp(math.floor(rest_length or 16), 1, MAX_STEPS)
    arranger_rest_divisions[track_index][position] = normalize_step_division(rest_division or DEFAULT_STEP_DIVISION)
end

function serialize_arranger_state(track_index)
    local sequence = arranger_sequences[track_index] or {}
    local values = {}
    for position = 1, #sequence do
        local slot = sequence[position]
        if slot ~= nil and slot <= 0 then
            local rest_length = clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS)
            local rest_division = normalize_step_division((arranger_rest_divisions[track_index] or {})[position] or DEFAULT_STEP_DIVISION)
            values[#values + 1] = string.format(slot == ARRANGER_EMPTY and "E%d:%d" or "R%d:%d", rest_length, rest_division)
        else
            values[#values + 1] = tostring(clamp(math.floor(slot or 1), 1, NUM_PATTERNS))
        end
    end
    return table.concat(values, ",")
end

function restore_arranger_serialized(track_index, data)
    arranger_sequences[track_index] = {}
    arranger_rest_lengths[track_index] = {}
    arranger_rest_divisions[track_index] = {}
    if not data or data == "" then return end

    for value_text in string.gmatch(data, "([^,]+)") do
        if #arranger_sequences[track_index] >= MAX_ARRANGER_STEPS then break end
        local empty_length_text, empty_division_text = string.match(value_text, "^E(%d+):(%d+)$")
        local rest_length_text, rest_division_text = string.match(value_text, "^R(%d+):(%d+)$")
        local position = #arranger_sequences[track_index] + 1
        if empty_length_text ~= nil then
            arranger_sequences[track_index][position] = ARRANGER_EMPTY
            set_arranger_rest_metadata(track_index, position, tonumber(empty_length_text), tonumber(empty_division_text))
        elseif rest_length_text ~= nil then
            arranger_sequences[track_index][position] = 0
            set_arranger_rest_metadata(track_index, position, tonumber(rest_length_text), tonumber(rest_division_text))
        else
            local slot = clamp(math.floor(tonumber(value_text) or 0), 0, NUM_PATTERNS)
            if slot >= 1 and pattern_created[track_index][slot] then
                arranger_sequences[track_index][position] = slot
            else
                arranger_sequences[track_index][position] = ARRANGER_EMPTY
                local l, d = default_rest_metadata(track_index)
                set_arranger_rest_metadata(track_index, position, l, d)
            end
        end
    end
end

function save_arranger_state(track_index, mark_dirty)
    reaper.SetProjExtState(
        0,
        PROJECT_EXT_SECTION,
        string.format("arranger%02d", track_index),
        serialize_arranger_state(track_index)
    )
    if mark_dirty then
        reaper.MarkProjectDirty(0)
        commit_history_state()
    end
end

function write_pattern_cell(track_index, pattern_slot, step)
    local c = pattern_store[track_index][pattern_slot][step]
    local base = pattern_cell_address(track_index, pattern_slot, step)
    reaper.gmem_write(base + 0, c.note)
    reaper.gmem_write(base + 1, c.velocity)
    reaper.gmem_write(base + 2, c.pan)
    reaper.gmem_write(base + 3, c.command)
    reaper.gmem_write(base + 4, c.parameter)
    reaper.gmem_write(base + 5, c.length)
    reaper.gmem_write(base + 6, c.probability)
end

function write_pattern_metadata(track_index, pattern_slot)
    reaper.gmem_write(
        pattern_meta_address(PATTERN_LENGTH_MATRIX_BASE, track_index, pattern_slot),
        pattern_lengths[track_index][pattern_slot] or 16
    )
    reaper.gmem_write(
        pattern_meta_address(PATTERN_DIV_MATRIX_BASE, track_index, pattern_slot),
        pattern_divisions[track_index][pattern_slot] or DEFAULT_STEP_DIVISION
    )
end

function publish_arranger_track(track_index)
    local sequence = arranger_sequences[track_index] or {}
    reaper.gmem_write(ARRANGER_LENGTH_BASE + track_index - 1, #sequence)
    for position = 1, MAX_ARRANGER_STEPS do
        local slot = sequence[position]
        local silent = slot ~= nil and slot <= 0
        reaper.gmem_write(
            ARRANGER_SEQUENCE_BASE + (track_index - 1) * MAX_ARRANGER_STEPS + position - 1,
            slot == nil and ARRANGER_EMPTY or slot
        )
        reaper.gmem_write(
            ARRANGER_REST_LENGTH_BASE + (track_index - 1) * MAX_ARRANGER_STEPS + position - 1,
            silent and clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS) or 0
        )
        reaper.gmem_write(
            ARRANGER_REST_DIV_BASE + (track_index - 1) * MAX_ARRANGER_STEPS + position - 1,
            silent and normalize_step_division((arranger_rest_divisions[track_index] or {})[position] or DEFAULT_STEP_DIVISION) or 0
        )
    end
end

function publish_pattern_track(track_index)
    local selected = selected_pattern(track_index)
    reaper.gmem_write(TRACK_SELECTED_PATTERN_BASE + track_index - 1, selected)
    for slot = 1, NUM_PATTERNS do
        write_pattern_metadata(track_index, slot)
        if pattern_created[track_index][slot] then
            for step = 1, MAX_STEPS do
                write_pattern_cell(track_index, slot, step)
            end
        end
    end
    publish_arranger_track(track_index)
end

function create_pattern_slot(track_index, pattern_slot)
    track_index = clamp(math.floor(track_index), 1, NUM_TRACKS)
    pattern_slot = clamp(math.floor(pattern_slot), 1, NUM_PATTERNS)
    if pattern_created[track_index][pattern_slot] then return false end

    pattern_store[track_index][pattern_slot] = new_empty_pattern()
    pattern_created[track_index][pattern_slot] = true
    pattern_lengths[track_index][pattern_slot] = 16
    pattern_divisions[track_index][pattern_slot] = DEFAULT_STEP_DIVISION
    write_pattern_metadata(track_index, pattern_slot)
    for step = 1, MAX_STEPS do
        write_pattern_cell(track_index, pattern_slot, step)
    end
    save_pattern_meta_state(track_index)
    reaper.MarkProjectDirty(0)
    return true
end

function select_pattern_slot(pattern_slot, create_if_missing)
    pattern_slot = clamp(math.floor(pattern_slot), 1, NUM_PATTERNS)
    if not pattern_created[current_track][pattern_slot] then
        if not create_if_missing then return false end
        create_pattern_slot(current_track, pattern_slot)
    end

    selected_pattern_slot[current_track] = pattern_slot
    sync_tracker_alias(current_track)
    sync_selected_pattern_length()
    reaper.gmem_write(TRACK_SELECTED_PATTERN_BASE + current_track - 1, pattern_slot)
    save_pattern_meta_state(current_track)
    save_meta_state(true)

    -- Keep undo/redo local to the newly selected edit context.
    undo_history = {}
    redo_history = {}
    history_current_snapshot = capture_history_snapshot()
    return true
end

function make_selected_pattern_audible()
    if arranger_mode then return false end
    local slot = selected_pattern(current_track)
    if not pattern_created[current_track][slot] then return false end

    if playing then
        -- Quantise the audible-pattern change to the next pattern boundary.
        reaper.gmem_write(TRACK_PENDING_PATTERN_BASE + current_track - 1, slot)
    else
        reaper.gmem_write(TRACK_ACTIVE_PATTERN_BASE + current_track - 1, slot)
        reaper.gmem_write(TRACK_ACTIVE_REST_BASE + current_track - 1, 0)
        reaper.gmem_write(TRACK_PENDING_PATTERN_BASE + current_track - 1, 0)
        reaper.gmem_write(TRACK_PLAYSTEP_BASE + current_track - 1, 0)
    end
    return true
end

function arranger_sequence_for_track(track_index)
    return arranger_sequences[track_index] or {}
end


function arranger_position_start_tick(track_index, position)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    position = clamp(math.floor(position or 1), 1, MAX_ARRANGER_STEPS)
    local tick = 0
    local sequence = arranger_sequences[track_index] or {}
    local limit = math.min(position - 1, #sequence)
    for pos = 1, limit do
        tick = tick + arranger_entry_duration_ticks(track_index, pos)
    end
    return tick
end


function arranger_track_extent_ticks(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    local sequence = arranger_sequences[track_index] or {}
    local tick = 0
    for pos = 1, #sequence do
        tick = tick + arranger_entry_duration_ticks(track_index, pos)
    end
    return tick
end


function arranger_default_duration_ticks(track_index)
    local rest_length, rest_division = default_rest_metadata(track_index)
    return rest_length * math.max(1, math.floor(128 / rest_division))
end


-- Resolve a musical-time anchor to the arranger cell that occupies that time.
-- Beyond explicit material, the result is a virtual EMPTY beginning exactly at
-- the requested time rather than an unrelated same-numbered sequence cell.
function arranger_resolve_tick(track_index, tick)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    tick = math.max(0, math.floor(tick or 0))
    local sequence = arranger_sequences[track_index] or {}
    local cursor = 0

    for pos = 1, #sequence do
        local duration = math.max(1, arranger_entry_duration_ticks(track_index, pos))
        local finish = cursor + duration
        if tick < finish then
            return pos, cursor, finish, true
        end
        cursor = finish
    end

    local virtual_pos = math.min(#sequence + 1, MAX_ARRANGER_STEPS)
    local virtual_end = tick + arranger_default_duration_ticks(track_index)
    return virtual_pos, tick, virtual_end, false
end


function sync_arranger_edit_position(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    local pos = select(1, arranger_resolve_tick(track_index, arranger_edit_tick))
    arranger_edit_position[track_index] = clamp(math.floor(pos or 1), 1, MAX_ARRANGER_STEPS)
    return arranger_edit_position[track_index]
end


function sync_all_arranger_edit_positions()
    for track_index = 1, NUM_TRACKS do
        sync_arranger_edit_position(track_index)
    end
end


function arranger_set_edit_tick(tick)
    arranger_edit_tick = math.max(0, math.floor(tick or 0))
    sync_all_arranger_edit_positions()
end


-- Convert an exact duration in master ticks to one or more legal EMPTY cells.
-- We first try to keep the preferred division, then any single-cell exact
-- representation, then decompose into a small number of exact chunks.
function arranger_duration_chunks(duration_ticks, preferred_division)
    local remaining = math.max(0, math.floor(duration_ticks or 0))
    local result = {}
    if remaining <= 0 then return result end

    local function try_single(ticks, division)
        division = normalize_step_division(division or DEFAULT_STEP_DIVISION)
        local factor = math.max(1, math.floor(128 / division))
        if ticks % factor ~= 0 then return nil end
        local length = math.floor(ticks / factor)
        if length < 1 or length > MAX_STEPS then return nil end
        return {length=length, division=division}
    end

    local preferred = try_single(remaining, preferred_division)
    if preferred then
        result[1] = preferred
        return result
    end

    for i = 1, #STEP_DIVISIONS do
        local one = try_single(remaining, STEP_DIVISIONS[i])
        if one then
            result[1] = one
            return result
        end
    end

    while remaining > 0 do
        local made = false
        for i = 1, #STEP_DIVISIONS do
            local division = STEP_DIVISIONS[i]
            local factor = math.max(1, math.floor(128 / division))
            if remaining >= factor then
                local length = math.min(MAX_STEPS, math.floor(remaining / factor))
                if length >= 1 then
                    local ticks = length * factor
                    result[#result + 1] = {length=length, division=division}
                    remaining = remaining - ticks
                    made = true
                    break
                end
            end
        end
        if not made then
            -- 1/128 resolution guarantees this fallback is representable.
            local length = math.min(MAX_STEPS, remaining)
            result[#result + 1] = {length=length, division=128}
            remaining = remaining - length
        end
    end

    return result
end


function arranger_rebuild_track_entries(track_index, entries)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    arranger_sequences[track_index] = {}
    arranger_rest_lengths[track_index] = {}
    arranger_rest_divisions[track_index] = {}

    local count = math.min(#entries, MAX_ARRANGER_STEPS)
    for pos = 1, count do
        local e = entries[pos]
        arranger_sequences[track_index][pos] = e.slot
        if e.slot <= 0 then
            set_arranger_rest_metadata(track_index, pos, e.length, e.division)
        end
    end
end


function arranger_entries_snapshot(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    local sequence = arranger_sequences[track_index] or {}
    local entries = {}
    for pos = 1, #sequence do
        local slot = sequence[pos]
        if slot <= 0 then
            entries[#entries + 1] = {
                slot=slot,
                length=clamp(math.floor((arranger_rest_lengths[track_index] or {})[pos] or 16),1,MAX_STEPS),
                division=normalize_step_division((arranger_rest_divisions[track_index] or {})[pos] or DEFAULT_STEP_DIVISION)
            }
        else
            entries[#entries + 1] = {slot=slot}
        end
    end
    return entries
end


function append_empty_duration_entries(target, duration_ticks, preferred_division)
    local chunks = arranger_duration_chunks(duration_ticks, preferred_division)
    for _, chunk in ipairs(chunks) do
        target[#target + 1] = {
            slot=ARRANGER_EMPTY,
            length=chunk.length,
            division=chunk.division
        }
    end
end


-- If the anchor is in empty space, materialise that exact musical-time point.
-- Existing Pattern/REST cells are not split: they remain the cell occupying
-- the anchor. EMPTY cells may be split so a new Pattern can begin exactly at
-- the selected time. Beyond the track end, exact-duration EMPTY gap cells are
-- inserted before the new target cell.
function arranger_materialize_edit_target(track_index)
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)
    local tick = math.max(0, math.floor(arranger_edit_tick or 0))
    local sequence = arranger_sequences[track_index] or {}
    local cursor = 0

    for pos = 1, #sequence do
        local duration = math.max(1, arranger_entry_duration_ticks(track_index, pos))
        local finish = cursor + duration
        local slot = sequence[pos]

        if tick == cursor then
            arranger_edit_position[track_index] = pos
            return pos, cursor, finish
        end

        if tick > cursor and tick < finish then
            if slot ~= ARRANGER_EMPTY then
                arranger_edit_position[track_index] = pos
                return pos, cursor, finish
            end

            local old_division = normalize_step_division((arranger_rest_divisions[track_index] or {})[pos] or DEFAULT_STEP_DIVISION)
            local before_ticks = tick - cursor
            local after_ticks = finish - tick
            local old_entries = arranger_entries_snapshot(track_index)
            local rebuilt = {}

            for i = 1, pos - 1 do rebuilt[#rebuilt + 1] = old_entries[i] end
            append_empty_duration_entries(rebuilt, before_ticks, old_division)
            local target_pos = #rebuilt + 1
            append_empty_duration_entries(rebuilt, after_ticks, old_division)
            for i = pos + 1, #old_entries do rebuilt[#rebuilt + 1] = old_entries[i] end

            if #rebuilt > MAX_ARRANGER_STEPS then return pos, cursor, finish end
            arranger_rebuild_track_entries(track_index, rebuilt)
            arranger_edit_position[track_index] = target_pos
            return target_pos, tick, tick + after_ticks
        end

        cursor = finish
    end

    -- No explicit material at this time. Append exact EMPTY timing up to the
    -- anchor, then an EMPTY target cell at the anchor itself.
    local gap = math.max(0, tick - cursor)
    local rebuilt = arranger_entries_snapshot(track_index)
    append_empty_duration_entries(rebuilt, gap, DEFAULT_STEP_DIVISION)
    local target_pos = #rebuilt + 1
    local default_length, default_division = default_rest_metadata(track_index)
    rebuilt[#rebuilt + 1] = {
        slot=ARRANGER_EMPTY,
        length=default_length,
        division=default_division
    }

    if #rebuilt <= MAX_ARRANGER_STEPS then
        arranger_rebuild_track_entries(track_index, rebuilt)
        arranger_edit_position[track_index] = target_pos
        local duration = default_length * math.max(1, math.floor(128 / default_division))
        return target_pos, tick, tick + duration
    end

    arranger_edit_position[track_index] = math.max(1, math.min(#sequence + 1, MAX_ARRANGER_STEPS))
    return arranger_edit_position[track_index], cursor, cursor
end


function ensure_arranger_position(track_index, position)
    -- Retained for compatibility with older call sites. V0.45.5 editing is
    -- musical-time based; missing sequence positions become timed EMPTY cells.
    track_index = clamp(math.floor(track_index), 1, NUM_TRACKS)
    position = clamp(math.floor(position), 1, MAX_ARRANGER_STEPS)
    local sequence = arranger_sequences[track_index]
    while #sequence < position - 1 do
        local fill_pos = #sequence + 1
        sequence[fill_pos] = ARRANGER_EMPTY
        local rest_length, rest_division = default_rest_metadata(track_index)
        set_arranger_rest_metadata(track_index, fill_pos, rest_length, rest_division)
    end
end


function set_arranger_pattern(pattern_slot)
    if not arranger_mode then return end
    pattern_slot = clamp(math.floor(pattern_slot), 1, NUM_PATTERNS)
    if not pattern_created[current_track][pattern_slot] then return end

    local pos, cell_start = arranger_materialize_edit_target(current_track)
    if not pos or pos < 1 then return end
    local sequence = arranger_sequences[current_track]
    sequence[pos] = pattern_slot
    arranger_rest_lengths[current_track][pos] = nil
    arranger_rest_divisions[current_track][pos] = nil

    local duration = clamp(math.floor(pattern_lengths[current_track][pattern_slot] or 16),1,MAX_STEPS)
        * math.max(1, math.floor(128 / normalize_step_division(pattern_divisions[current_track][pattern_slot] or DEFAULT_STEP_DIVISION)))
    arranger_set_edit_tick((cell_start or arranger_edit_tick) + duration)
    arranger_cell_edit_mode = false
    publish_arranger_track(current_track)
    save_arranger_state(current_track, true)
end


function set_arranger_rest(track_index, position, rest_length, rest_division)
    if not arranger_mode then return false end
    track_index = clamp(math.floor(track_index or current_track), 1, NUM_TRACKS)

    local pos, cell_start
    if position ~= nil then
        pos = clamp(math.floor(position), 1, MAX_ARRANGER_STEPS)
        cell_start = arranger_position_start_tick(track_index, pos)
        ensure_arranger_position(track_index, pos)
    else
        pos, cell_start = arranger_materialize_edit_target(track_index)
    end

    if not pos or pos < 1 then return false end
    arranger_sequences[track_index][pos] = 0
    set_arranger_rest_metadata(track_index, pos, rest_length, rest_division)
    arranger_set_edit_tick(cell_start or arranger_edit_tick)
    publish_arranger_track(track_index)
    save_arranger_state(track_index, true)
    return true
end


function arranger_cell_choices(track_index)
    local choices = {ARRANGER_EMPTY, 0}
    for slot = 1, NUM_PATTERNS do
        if pattern_created[track_index][slot] then choices[#choices + 1] = slot end
    end
    return choices
end


function cycle_arranger_cell(direction)
    if not arranger_mode or not arranger_cell_edit_mode then return false end
    direction = direction < 0 and -1 or 1
    local track_index = current_track
    local position, cell_start = arranger_materialize_edit_target(track_index)
    if not position then return false end
    local sequence = arranger_sequences[track_index]
    local old_slot = sequence[position]
    if old_slot == nil then old_slot = ARRANGER_EMPTY end
    local old_length, old_division

    if old_slot <= 0 then
        old_length = clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS)
        old_division = normalize_step_division((arranger_rest_divisions[track_index] or {})[position] or DEFAULT_STEP_DIVISION)
    else
        old_length = clamp(math.floor(pattern_lengths[track_index][old_slot] or 16), 1, MAX_STEPS)
        old_division = normalize_step_division(pattern_divisions[track_index][old_slot] or DEFAULT_STEP_DIVISION)
    end

    local choices = arranger_cell_choices(track_index)
    local current_index = 1
    for i, value in ipairs(choices) do
        if value == old_slot then current_index = i break end
    end
    local new_index = ((current_index - 1 + direction) % #choices) + 1
    local new_slot = choices[new_index]

    sequence[position] = new_slot
    if new_slot <= 0 then
        set_arranger_rest_metadata(track_index, position, old_length, old_division)
    else
        arranger_rest_lengths[track_index][position] = nil
        arranger_rest_divisions[track_index][position] = nil
    end
    arranger_set_edit_tick(cell_start or arranger_edit_tick)
    publish_arranger_track(track_index)
    save_arranger_state(track_index, true)
    return true
end


function change_arranger_rest_length(amount)
    if not arranger_mode then return false end
    local track_index = current_track
    local position, cell_start = arranger_resolve_tick(track_index, arranger_edit_tick)
    local sequence = arranger_sequences[track_index]
    if not sequence[position] or sequence[position] ~= 0 then return false end
    local current = clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS)
    local updated = clamp(current + amount, 1, MAX_STEPS)
    if updated == current then return true end
    arranger_rest_lengths[track_index][position] = updated
    arranger_set_edit_tick(cell_start or arranger_edit_tick)
    publish_arranger_track(track_index)
    save_arranger_state(track_index, true)
    return true
end


function move_arranger_position(delta)
    if not arranger_mode then return false end
    delta = delta < 0 and -1 or 1
    local track_index = current_track
    local pos, start_tick, end_tick, exists = arranger_resolve_tick(track_index, arranger_edit_tick)
    local target = arranger_edit_tick

    if delta > 0 then
        if exists then
            target = end_tick
        else
            target = arranger_edit_tick + arranger_default_duration_ticks(track_index)
        end
    default_velocity = clamp(
        math.floor(values[7]),
        1,
        127
    )

    if source_version >= 2 then
        default_pan = clamp(math.floor(values[8]), 0, 127)
    else
        default_pan = 64
    end

    default_length = clamp(
        math.floor(values[9]),
        1,
        64
    )

    default_probability = quantize_probability(values[10])

    -- Per-step instrument routing was removed in V0.45.5.
    inst_lock_mode = false

    if #values >= 12 then

        jam_track_bank =
            clamp(
                math.floor(
                    values[12]
                ),
                0,
                1
            )

    else

        jam_track_bank =
            0
    end

    if #values >= 13 then

        jam_select_mode =
            values[13] ~= 0

    else

        jam_select_mode =
            true
    end

    if #values >= 14 then

        jam_group_solo_mode =
            values[14] ~= 0

    else

        jam_group_solo_mode =
            false
    end

    if #values >= 15 then

        note_audition_enabled =
            values[15] ~= 0

    else

        note_audition_enabled =
            false
    end

    if #values >= 16 then
        playhead_follow_enabled = values[16] ~= 0
    else
        playhead_follow_enabled = false
    end

    if #values >= 17 then
        bank_nav_relative = values[17] ~= 0
    else
        bank_nav_relative = true
    end

    if #values >= 18 then
        jam_step_bank = clamp(math.floor(values[18]), 0, math.floor((MAX_STEPS - 1) / 64))
    else
        jam_step_bank = 0
    end

    if #values >= 19 then
        scale_lock_enabled = values[19] ~= 0
    else
        scale_lock_enabled = false
    end

    if #values >= 20 then
        scale_root = clamp(math.floor(values[20]), 0, 11)
    else
        scale_root = 0
    end

    if #values >= 21 then
        scale_index = clamp(math.floor(values[21]), 1, #SCALE_NAMES)
    else
        scale_index = 1
    end

    last_saved_meta = meta

    for track_index = 1, NUM_TRACKS do

        local track_key =
            string.format(
                "track%02d",
                track_index
            )

        local track_retval, data =
            reaper.GetProjExtState(
                0,
                PROJECT_EXT_SECTION,
                track_key
            )

        if track_retval > 0
            and
            data ~= ""
        then

            local restored =
                parse_track_string(
                    data,
                    source_version
                )

            if restored then

                tracker[track_index] = restored
                pattern_store[track_index][1] = restored
                last_saved_tracks[string.format("%02d:%02d", track_index, 1)] = data
            end
        end
    end

    -- Mixer state was added separately in V0.20.
    -- V0.29 extends it from:
    --   mute,volume
    -- to:
    --   mute,volume,solo
    local mix_retval, mix_data =
        reaper.GetProjExtState(
            0,
            PROJECT_EXT_SECTION,
            "mix"
        )

    if mix_retval > 0
        and
        mix_data ~= ""
    then

        local mix_values = {}

        for value_text in string.gmatch(
            mix_data,
            "([^,]+)"
        ) do

            mix_values[#mix_values + 1] =
                tonumber(
                    value_text
                )
        end

        if #mix_values ==
            NUM_TRACKS * 3
        then

            for track_index = 1, NUM_TRACKS do

                local base_index =
                    (track_index - 1)
                    *
                    3

                track_muted[track_index] =
                    mix_values[
                        base_index + 1
                    ]
                    ~= 0

                track_volume[track_index] =
                    clamp(
                        math.floor(
                            mix_values[
                                base_index + 2
                            ]
                        ),
                        0,
                        127
                    )

                track_solo[track_index] =
                    mix_values[
                        base_index + 3
                    ]
                    ~= 0
            end

            last_saved_mix =
                mix_data

        elseif #mix_values ==
            NUM_TRACKS * 2
        then

            -- Older project: restore mute/volume and start with no solos.
            for track_index = 1, NUM_TRACKS do

                local base_index =
                    (track_index - 1)
                    *
                    2

                track_muted[track_index] =
                    mix_values[
                        base_index + 1
                    ]
                    ~= 0

                track_volume[track_index] =
                    clamp(
                        math.floor(
                            mix_values[
                                base_index + 2
                            ]
                        ),
                        0,
                        127
                    )

                track_solo[track_index] =
                    false
            end

            -- Let the next actual mixer change rewrite the new format.
            last_saved_mix = nil
        end
    end


    local lengths_retval, lengths_data =
        reaper.GetProjExtState(
            0,
            PROJECT_EXT_SECTION,
            "lengths"
        )

    if lengths_retval > 0
        and
        lengths_data ~= ""
    then

        local length_values = {}

        for value_text in string.gmatch(
            lengths_data,
            "([^,]+)"
        ) do

            length_values[#length_values + 1] =
                tonumber(
                    value_text
                )
        end

        if #length_values == NUM_TRACKS then

            for track_index = 1, NUM_TRACKS do

                track_pattern_length[
                    track_index
                ] =
                    clamp(
                        math.floor(
                            length_values[
                                track_index
                            ]
                        ),
                        1,
                        MAX_STEPS
                    )
            end

            last_saved_lengths =
                lengths_data
        end
    end

    local resolutions_retval, resolutions_data =
        reaper.GetProjExtState(
            0,
            PROJECT_EXT_SECTION,
            "resolutions"
        )

    if resolutions_retval > 0
        and
        resolutions_data ~= ""
    then
        local resolution_values = {}

        for value_text in string.gmatch(resolutions_data, "([^,]+)") do
            resolution_values[#resolution_values + 1] = tonumber(value_text)
        end

        if #resolution_values == NUM_TRACKS then
            for track_index = 1, NUM_TRACKS do
                track_step_division[track_index] =
                    normalize_step_division(
                        resolution_values[track_index]
                    )
            end
            last_saved_resolutions = resolutions_data
        end
    end

    -- V0.45.5 pattern slots and arranger sequences. Older projects become Pattern 1.
    for track_index = 1, NUM_TRACKS do
        pattern_store[track_index][1] = tracker[track_index]
        pattern_created[track_index][1] = true
        pattern_lengths[track_index][1] = track_pattern_length[track_index] or 16
        pattern_divisions[track_index][1] = track_step_division[track_index] or DEFAULT_STEP_DIVISION
        selected_pattern_slot[track_index] = 1

        local meta_ret, pattern_meta = reaper.GetProjExtState(
            0, PROJECT_EXT_SECTION, string.format("patternmeta%02d", track_index)
        )
        if source_version >= 3 and meta_ret > 0 and pattern_meta ~= "" then
            local mv = {}
            for value_text in string.gmatch(pattern_meta, "([^,]+)") do
                mv[#mv + 1] = tonumber(value_text)
            end
            if #mv >= 1 + NUM_PATTERNS * 3 then
                selected_pattern_slot[track_index] = clamp(math.floor(mv[1] or 1), 1, NUM_PATTERNS)
                local idx = 2
                for slot = 1, NUM_PATTERNS do
                    pattern_created[track_index][slot] = (mv[idx] or 0) ~= 0
                    pattern_lengths[track_index][slot] = clamp(math.floor(mv[idx + 1] or 16), 1, MAX_STEPS)
                    pattern_divisions[track_index][slot] = normalize_step_division(mv[idx + 2] or DEFAULT_STEP_DIVISION)
                    idx = idx + 3
                end
                pattern_created[track_index][1] = true
            end
        end

        for slot = 2, NUM_PATTERNS do
            if pattern_created[track_index][slot] then
                local ret, data = reaper.GetProjExtState(
                    0,
                    PROJECT_EXT_SECTION,
                    string.format("track%02d_pattern%02d", track_index, slot)
                )
                if ret > 0 and data ~= "" then
                    local restored = parse_track_string(data, source_version)
                    if restored then
                        pattern_store[track_index][slot] = restored
                        last_saved_tracks[string.format("%02d:%02d", track_index, slot)] = data
                    end
                end
            end
        end

        local ar_ret, ar_data = reaper.GetProjExtState(
            0, PROJECT_EXT_SECTION, string.format("arranger%02d", track_index)
        )
        restore_arranger_serialized(track_index, (ar_ret > 0) and ar_data or "")
        arranger_edit_position[track_index] = 1
        sync_tracker_alias(track_index)
    end

    -- V0.45.5 arranger editing starts from one shared musical-time anchor.
    arranger_set_edit_tick(0)

    pattern_length =
        track_pattern_length[
            current_track
        ]
        or
        16

    if step_wrapping
        and
        current_step > pattern_length
    then

        current_step =
            pattern_length
    end

    return true
end


function write_cell(track, step)

    local c =
        tracker[
            track
        ][step]

    local slot = selected_pattern(track)
    local base = pattern_cell_address(track, slot, step)

    reaper.gmem_write(
        base + 0,
        c.note
    )

    reaper.gmem_write(
        base + 1,
        c.velocity
    )

    reaper.gmem_write(
        base + 2,
        c.pan
    )

    reaper.gmem_write(
        base + 3,
        c.command
    )

    reaper.gmem_write(
        base + 4,
        c.parameter
    )

    reaper.gmem_write(
        base + 5,
        c.length
    )

    reaper.gmem_write(
        base + 6,
        c.probability
    )
end


function write_track_mix_gmem(track_index)

    reaper.gmem_write(
        TRACK_MUTE_BASE
        +
        track_index
        -
        1,
        track_muted[track_index]
        and
        1
        or
        0
    )

    reaper.gmem_write(
        TRACK_VOLUME_BASE
        +
        track_index
        -
        1,
        track_volume[track_index]
    )

    reaper.gmem_write(
        TRACK_SOLO_BASE
        +
        track_index
        -
        1,
        track_solo[track_index]
        and
        1
        or
        0
    )
end


function write_all_gmem()

    reaper.gmem_write(
        1,
        reaper.Master_GetTempo()
    )

    reaper.gmem_write(
        2,
        current_step_qn()
    )

    reaper.gmem_write(
        7,
        pattern_length
    )

    for track = 1, NUM_TRACKS do
        publish_pattern_track(track)
        write_track_mix_gmem(track)

        -- Legacy mirrors remain the selected edit pattern for compatibility.
        reaper.gmem_write(TRACK_PATTERN_LENGTH_BASE + track - 1, track_pattern_length[track] or 16)
        reaper.gmem_write(TRACK_STEP_DIV_BASE + track - 1, track_step_division[track] or DEFAULT_STEP_DIVISION)
    end

    write_solo_summary_gmem()

    reaper.gmem_write(
        JAM_GROUP_MODE_GMEM,
        jam_group_solo_mode
        and
        1
        or
        0
    )

    reaper.gmem_write(
        FOLLOW_MOD_GMEM,
        follow_modifier
        and
        1
        or
        0
    )

    reaper.gmem_write(SETTINGS_HELD_GMEM, settings_held and 1 or 0)
    reaper.gmem_write(TEMPO_MODE_GMEM, tempo_mode and 1 or 0)
    reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
    reaper.gmem_write(SONG_MODE_GMEM, true_song_mode and 1 or 0)
    reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
    reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, pattern_select_mode and 1 or 0)
    reaper.gmem_write(JAM_STEP_BANK_GMEM, jam_step_bank)

    publish_mk3_led_modes()

    reaper.gmem_write(
        CURRENT_TRACK_GMEM,
        current_track
    )

    playing = false

    reaper.gmem_write(
        0,
        0
    )

    reaper.gmem_write(
        3,
        0
    )

    reaper.gmem_write(
        6,
        1
    )
end


function is_note_active(track, step)

    return
        reaper.gmem_read(
            active_address(
                track,
                step
            )
        )
        > 0
end


function build_display_state(running)

    local playhead =
        math.floor(
            reaper.gmem_read(3)
        )
        +
        1

    local display_cursor =
        current_step

    if playhead_follow_enabled
        and
        playing
    then

        display_cursor =
            clamp(
                playhead,
                1,
                MAX_STEPS
            )
    end

    local lines = {
        "MASCHINE_TRACKER_STATE|1",

        "running|"
        ..
        (
            running
            and
            "1"
            or
            "0"
        ),

        "version|"
        ..
        VERSION,

        "track|"
        ..
        current_track,

        "trackname|"
        ..
        current_track_name(),

        "cursor|"
        ..
        current_step,

        "displaycursor|"
        ..
        display_cursor,

        "playhead|"
        ..
        playhead,

        "pattern|"
        ..
        pattern_length,

        "playing|"
        ..
        (
            playing
            and
            "1"
            or
            "0"
        ),

        "column|"
        ..
        current_column,

        "quick|"
        ..
        (
            quick_step_mode
            and
            "1"
            or
            "0"
        ),

        "fixed|"
        ..
        (
            fixed_velocity
            and
            "1"
            or
            "0"
        ),

        "wrap|"
        ..
        (
            step_wrapping
            and
            "1"
            or
            "0"
        ),

        "octave|"
        ..
        entry_octave,

        "page|"
        ..
        mk3_page,

        "jamconnected|"
        ..
        (
            jam_connected
            and
            "1"
            or
            "0"
        ),

        "trackmute|"
        ..
        (
            track_muted[current_track]
            and
            "1"
            or
            "0"
        ),

        "trackvolume|"
        ..
        track_volume[current_track],

        "tracksolo|"
        ..
        (
            track_solo[current_track]
            and
            "1"
            or
            "0"
        ),

        "jamgroupmode|"
        ..
        (
            jam_group_solo_mode
            and
            "1"
            or
            "0"
        ),

        "knobfocus|"
        ..
        (
            (
                reaper.time_precise()
                <
                knob_focus_until
            )
            and
            knob_focus
            or
            0
        ),

        "patternmode|"
        ..
        (
            pattern_edit_mode
            and
            "1"
            or
            "0"
        ),

        "fourmode|"
        ..
        (
            four_d_note_mode
            and
            "1"
            or
            "0"
        ),

        "instlock|"
        ..
        (
            inst_lock_mode
            and
            "1"
            or
            "0"
        ),

        "jambank|"
        ..
        (
            jam_track_bank
            +
            1
        ),

        "shiftmod|"
        ..
        (
            follow_modifier
            and
            "1"
            or
            "0"
        ),

        "padmode|" .. (note_audition_enabled and "1" or "0"),

        "jamselect|"
        ..
        (
            jam_select_mode
            and
            "1"
            or
            "0"
        ),

        "lastpadnote|"
        ..
        last_mk3_pad_note,

        "settingsheld|"
        ..
        (
            settings_held
            and
            "1"
            or
            "0"
        ),

        "noteaudition|" .. (note_audition_enabled and "1" or "0"),
        "patternselect|" .. (pattern_select_mode and "1" or "0"),
        "patternslot|" .. selected_pattern(current_track),
        "playpattern|" .. ((playing and reaper.gmem_read(TRACK_ACTIVE_REST_BASE + current_track - 1) > 0) and 0 or (playing and clamp(math.floor(reaper.gmem_read(TRACK_ACTIVE_PATTERN_BASE + current_track - 1)), 1, NUM_PATTERNS) or selected_pattern(current_track))),
        "patterncreated|" .. table.concat((function()
            local v = {}
            for slot = 1, NUM_PATTERNS do v[#v + 1] = pattern_created[current_track][slot] and 1 or 0 end
            return v
        end)(), ","),
        "overview|" .. (browser_overview_mode and "1" or "0"),
        "arrangereditpos|" .. (arranger_edit_position[current_track] or 1),
        "arrangercelledit|" .. (arranger_cell_edit_mode and "1" or "0"),
        "arrangerplaypos|" .. ((playing and arranger_mode and #(arranger_sequences[current_track] or {}) > 0) and math.max(0, math.floor(reaper.gmem_read(ARRANGER_PLAY_POS_BASE + current_track - 1)) + 1) or 0),
        "arrseq|" .. table.concat(arranger_sequences[current_track] or {}, ","),

        "playheadfollow|" .. (playhead_follow_enabled and "1" or "0"),

        "banknavrelative|" .. (bank_nav_relative and "1" or "0"),
        "inheritvel|" .. (inherit_velocity and "1" or "0"),
        "inheritln|" .. (inherit_length and "1" or "0"),
        "inheritprob|" .. (inherit_probability and "1" or "0"),
        "inheritcmd|" .. (inherit_command and "1" or "0"),
        "arrangerview|" .. (arranger_view_vertical and "VERTICAL" or "HORIZONTAL"),
        "songmode|" .. (true_song_mode and "TRUE" or "LOOP"),
        "arrangerzoom|" .. arranger_zoom_level,
        "songtick|" .. math.max(0, math.floor(reaper.gmem_read(ARRANGER_SONG_TICK_GMEM))),

        "stepsizediv|" .. current_step_division(),

        "jamstepbank|" .. (jam_step_bank + 1),

        "tempomode|" .. (tempo_mode and "1" or "0"),

        "tempo|" .. string.format("%.2f", reaper.Master_GetTempo()),

        "arranger|" .. (arranger_mode and "1" or "0"),
        "visualizer|" .. (visualizer_mode and "1" or "0"),
        "visualstyle|" .. visualizer_style,
        "visualauto|" .. (visualizer_auto and "1" or "0"),
        "variationheld|" .. (variation_held and "1" or "0"),
        "variationbias|" .. variation_bias_mode,
        "variationstrength|" .. variation_bias_strength,
        "variationoctaves|" .. variation_octave_span,
        "arrangeredittick|" .. math.max(0, math.floor(arranger_edit_tick or 0)),
        "notedivmode|" .. (note_division_mode and "1" or "0"),
        "sensitivitymode|" .. (sensitivity_mode and "1" or "0"),
        "sensitivityencoder|" .. sensitivity_selected_encoder,
        "sensitivitylevel|" .. (encoder_sensitivity[sensitivity_selected_encoder] or 5),
        "sensitivitydemo|" .. (sensitivity_demo_values[sensitivity_selected_encoder] or 0),
        "scaleedit|" .. (scale_edit_mode and "1" or "0"),
        "scalelock|" .. (scale_lock_enabled and "1" or "0"),
        "scaleroot|" .. scale_root,
        "scalename|" .. scale_name(),
        "directnote|" .. (jam_direct_note_mode and "1" or "0"),
        "directaudition|" .. (jam_direct_audition_mode and "1" or "0"),
        "jamtouchmode|" .. jam_touch_mode,
        "lengthtouchrange|" .. length_touch_range,
        "tutorials|" .. (tutorials_enabled and "1" or "0"),
        "tutorialactive|" .. (tutorial_active() and "1" or "0"),
        "tutorialmode|" .. tutorial_mode,
        "toppulses|" .. table.concat({
            math.floor(reaper.gmem_read(15422)),
            math.floor(reaper.gmem_read(15423)),
            math.floor(reaper.gmem_read(15424)),
            math.floor(reaper.gmem_read(15425)),
            math.floor(reaper.gmem_read(15426)),
            math.floor(reaper.gmem_read(15427)),
            math.floor(reaper.gmem_read(15428)),
            math.floor(reaper.gmem_read(15429))
        }, ","),
        "padnotes|" .. table.concat({
            scale_pad_note(0, false),
            scale_pad_note(1, false),
            scale_pad_note(2, false),
            scale_pad_note(3, false),
            scale_pad_note(4, false),
            scale_pad_note(5, false),
            scale_pad_note(6, false),
            scale_pad_note(7, false),
            scale_pad_note(8, false),
            scale_pad_note(9, false),
            scale_pad_note(10, false),
            scale_pad_note(11, false),
            scale_pad_note(12, false),
            scale_pad_note(13, false),
            scale_pad_note(14, false),
            scale_pad_note(15, false)
        }, ",")
    }

    for step = 1, MAX_STEPS do

        local c =
            tracker[
                current_track
            ][step]

        local active =
            is_note_active(
                current_track,
                step
            )
            and
            1
            or
            0

        lines[
            #lines + 1
        ] =
            table.concat(
                {
                    "step",
                    step,
                    c.note,
                    c.velocity,
                    c.pan,
                    c.command,
                    c.parameter,
                    c.length,
                    c.probability,
                    active
                },
                "|"
            )
    end

    for track_index = 1, NUM_TRACKS do
        local active_length = track_pattern_length[track_index] or 16
        local has_note = false

        for step_index = 1, active_length do
            if tracker[track_index][step_index].note >= 0 then
                has_note = true
                break
            end
        end

        local in_range = display_cursor <= active_length
        local arranger_note = -1

        if in_range then
            arranger_note = tracker[track_index][display_cursor].note
        end

        local arranger_name = tostring(track_names[track_index] or ("Track " .. track_index))
        arranger_name = string.gsub(arranger_name, "|", "/")

        lines[#lines + 1] =
            table.concat(
                {
                    "arrtrack",
                    track_index,
                    has_note and 1 or 0,
                    arranger_note,
                    in_range and 1 or 0,
                    active_length,
                    normalize_step_division(track_step_division[track_index]),
                    arranger_name
                },
                "|"
            )
    end
    -- V0.45.5 complete 16-track, duration-aware song overview.
    -- Sequence 0 = REST. Each row also publishes actual entry durations so the
    -- Python display can align all tracks against musical time rather than
    -- pretending every arranger cell has the same length.
    for track_index = 1, NUM_TRACKS do
        local active_rest = reaper.gmem_read(TRACK_ACTIVE_REST_BASE + track_index - 1) > 0
        local active_slot = active_rest and 0 or clamp(math.floor(reaper.gmem_read(TRACK_ACTIVE_PATTERN_BASE + track_index - 1)), 1, NUM_PATTERNS)
        local play_pos_zero = math.floor(reaper.gmem_read(ARRANGER_PLAY_POS_BASE + track_index - 1))
        local play_pos = play_pos_zero >= 0 and (play_pos_zero + 1) or 0
        local play_step_zero = clamp(math.floor(reaper.gmem_read(TRACK_PLAYSTEP_BASE + track_index - 1)), 0, MAX_STEPS - 1)
        local active_length
        if active_rest and play_pos >= 1 then
            active_length = clamp(math.floor((arranger_rest_lengths[track_index] or {})[play_pos] or 16), 1, MAX_STEPS)
        else
            local length_slot = active_slot >= 1 and active_slot or selected_pattern(track_index)
            active_length = clamp(math.floor(pattern_lengths[track_index][length_slot] or 16), 1, MAX_STEPS)
        end
        local progress = clamp(math.floor((play_step_zero / active_length) * 1000 + 0.5), 0, 1000)
        local sequence = arranger_sequences[track_index] or {}
        local durations = {}
        local rest_lengths = {}
        local rest_divisions = {}
        for position = 1, #sequence do
            durations[#durations + 1] = arranger_entry_duration_ticks(track_index, position)
            rest_lengths[#rest_lengths + 1] = sequence[position] <= 0 and clamp(math.floor((arranger_rest_lengths[track_index] or {})[position] or 16), 1, MAX_STEPS) or 0
            rest_divisions[#rest_divisions + 1] = sequence[position] <= 0 and normalize_step_division((arranger_rest_divisions[track_index] or {})[position] or DEFAULT_STEP_DIVISION) or 0
        end
        lines[#lines + 1] = table.concat({
            "arrsong",
            track_index,
            play_pos,
            active_slot,
            progress,
            select(1, arranger_resolve_tick(track_index, arranger_edit_tick)),
            (track_index == current_track and arranger_cell_edit_mode) and 1 or 0,
            table.concat(sequence, ","),
            table.concat(durations, ","),
            table.concat(rest_lengths, ","),
            table.concat(rest_divisions, ",")
        }, "|")
    end

    for strip_index = 1, 8 do
        local touch_step = visible_step_for_strip(strip_index)
        local touch_cell = tracker[current_track][touch_step]
        lines[#lines + 1] = table.concat({
            "touchstep",
            strip_index,
            touch_step,
            touch_cell.note,
            touch_cell.velocity,
            touch_cell.probability,
            touch_cell.length,
            touch_cell.pan
        }, "|")
    end

    -- Compact live feed for the left-screen visualiser. Each tracker track
    -- reports the cell under its own playhead, preserving independent step divisions.
    for track_index = 1, NUM_TRACKS do
        local play_step = clamp(math.floor(reaper.gmem_read(TRACK_PLAYSTEP_BASE + track_index - 1)) + 1, 1, MAX_STEPS)
        local vc = tracker[track_index][play_step]
        lines[#lines + 1] = table.concat({
            "vistrack",
            track_index,
            play_step,
            vc.note,
            vc.velocity,
            vc.length,
            vc.pan,
            vc.command,
            vc.parameter,
            vc.probability,
            track_muted[track_index] and 1 or 0,
            track_solo[track_index] and 1 or 0
        }, "|")
    end

    return
        table.concat(
            lines,
            "\n"
        )
        ..
        "\n"
end


function write_display_state(running)

    local state =
        build_display_state(
            running
        )

    if state ==
        last_display_state
    then
        return
    end

    local file =
        io.open(
            DISPLAY_STATE_PATH,
            "w"
        )

    if file then

        file:write(
            state
        )

        file:close()

        last_display_state =
            state
    end
end


function publish_mk3_led_modes()

    reaper.gmem_write(
        LOCK_STEP_GMEM,
        quick_step_mode
        and
        0
        or
        1
    )

    reaper.gmem_write(FIXED_VEL_GMEM, fixed_velocity and 1 or 0)
    reaper.gmem_write(INST_LOCK_GMEM, 0)
    reaper.gmem_write(VISUALIZER_MODE_GMEM, visualizer_mode and 1 or 0)
    reaper.gmem_write(VISUALIZER_STYLE_GMEM, visualizer_style)
    reaper.gmem_write(VISUALIZER_AUTO_GMEM, visualizer_auto and 1 or 0)
    reaper.gmem_write(VARIATION_MODE_GMEM, variation_held and 1 or 0)
    reaper.gmem_write(TEMPO_MODE_GMEM, tempo_mode and 1 or 0)
    reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
    reaper.gmem_write(NOTES_MODE_GMEM, note_division_mode and 1 or 0)
    reaper.gmem_write(SENSITIVITY_MODE_GMEM, sensitivity_mode and 1 or 0)
    reaper.gmem_write(SCALE_LOCK_GMEM, scale_lock_enabled and 1 or 0)
    reaper.gmem_write(SCALE_EDIT_GMEM, scale_edit_mode and 1 or 0)
    reaper.gmem_write(JAM_DIRECT_NOTE_MODE_GMEM, jam_direct_note_mode and 1 or 0)
    reaper.gmem_write(JAM_DIRECT_AUDITION_GMEM, jam_direct_audition_mode and 1 or 0)
    reaper.gmem_write(JAM_TOUCH_MODE_GMEM, jam_touch_mode)
    reaper.gmem_write(LENGTH_TOUCH_RANGE_GMEM, length_touch_range)
    reaper.gmem_write(TUTORIALS_GMEM, tutorials_enabled and 1 or 0)
    reaper.gmem_write(PAD_MODE_GMEM, note_audition_enabled and 1 or 0)
    reaper.gmem_write(SONG_MODE_GMEM, true_song_mode and 1 or 0)
    reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, pattern_select_mode and 1 or 0)
    reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
end


function toggle_step_mode()

    quick_step_mode =
        not quick_step_mode

    publish_mk3_led_modes()

    save_meta_state(true)
end


function toggle_fixed_velocity()

    fixed_velocity =
        not fixed_velocity

    publish_mk3_led_modes()

    save_meta_state(true)
end


function toggle_step_wrapping()

    step_wrapping =
        not step_wrapping

    if step_wrapping
        and
        current_step > pattern_length
    then

        current_step =
            pattern_length
    end

    save_meta_state(true)
end


function toggle_settings_preference(pad_number)

    pad_number =
        clamp(
            math.floor(
                pad_number
            ),
            1,
            16
        )

    if pad_number == 1 then

        toggle_step_wrapping()

        return

    elseif pad_number == 2 then
        -- V0.45.5 uses the slot freed by NOTE AUDITION for the Arranger layout.
        arranger_view_vertical = not arranger_view_vertical
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 3 then

        playhead_follow_enabled =
            not playhead_follow_enabled

    elseif pad_number == 4 then

        bank_nav_relative =
            not bank_nav_relative

    elseif pad_number == 5 then

        tutorials_enabled = not tutorials_enabled
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 6 then
        inherit_velocity = not inherit_velocity
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 7 then
        inherit_length = not inherit_length
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 8 then
        inherit_probability = not inherit_probability
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 9 then
        inherit_command = not inherit_command
        save_user_preferences()
        publish_mk3_led_modes()
        return

    elseif pad_number == 10 then
        true_song_mode = not true_song_mode
        reaper.gmem_write(SONG_MODE_GMEM, true_song_mode and 1 or 0)
        save_user_preferences()
        publish_mk3_led_modes()
        return

    else

        return
    end

    save_meta_state(
        true
    )
end


function audition_note(
    note,
    velocity,
    track_index
)

    if not note_audition_enabled then
        return
    end

    note =
        clamp(
            math.floor(
                note
            ),
            0,
            127
        )

    velocity =
        clamp(
            math.floor(
                velocity
                or
                default_velocity
            ),
            1,
            127
        )

    track_index =
        clamp(
            math.floor(
                track_index
                or
                current_track
            ),
            1,
            16
        )

    reaper.gmem_write(
        AUDITION_NOTE_GMEM,
        note
    )

    reaper.gmem_write(
        AUDITION_VELOCITY_GMEM,
        velocity
    )

    reaper.gmem_write(
        AUDITION_CHANNEL_GMEM,
        track_index
        -
        1
    )

    reaper.gmem_write(
        AUDITION_GATE_MS_GMEM,
        100
    )

    local trigger =
        math.floor(
            reaper.gmem_read(
                AUDITION_TRIGGER_GMEM
            )
        )
        +
        1

    reaper.gmem_write(
        AUDITION_TRIGGER_GMEM,
        trigger
    )
end


function step_mode_name()

    if quick_step_mode then
        return "QUICK STEP"
    end

    return "LOCK STEP"
end


function velocity_mode_name()

    if fixed_velocity then
        return "FIXED VEL 100"
    end

    return "PAD VELOCITY"
end


function wrapping_mode_name()

    if step_wrapping then
        return "WRAP ON"
    end

    return "WRAP OFF"
end


function set_current_step_division(division)

    division = normalize_step_division(division)

    local slot = selected_pattern(current_track)
    if pattern_divisions[current_track][slot] == division then return end

    pattern_divisions[current_track][slot] = division
    track_step_division[current_track] = division
    write_pattern_metadata(current_track, slot)

    reaper.gmem_write(TRACK_STEP_DIV_BASE + current_track - 1, division)

    reaper.gmem_write(
        2,
        step_division_to_qn(division)
    )

    save_pattern_meta_state(current_track)
    save_track_resolutions_state(true)
end


function change_current_step_division(direction)

    direction = direction < 0 and -1 or 1

    local index = step_division_index(current_step_division())
    index = clamp(index + direction, 1, #STEP_DIVISIONS)

    set_current_step_division(
        STEP_DIVISIONS[index]
    )
end


function sync_selected_pattern_length()

    local slot = selected_pattern(current_track)
    pattern_length =
        clamp(
            math.floor(
                pattern_lengths[current_track][slot]
                or track_pattern_length[current_track]
                or 16
            ),
            1,
            MAX_STEPS
        )

    reaper.gmem_write(7, pattern_length)
    reaper.gmem_write(2, current_step_qn())
end


function set_pattern_length(value)

    pattern_length =
        clamp(
            value,
            1,
            MAX_STEPS
        )

    if step_wrapping
        and
        current_step > pattern_length
    then

        current_step =
            pattern_length
    end

    local slot = selected_pattern(current_track)
    pattern_lengths[current_track][slot] = pattern_length
    track_pattern_length[current_track] = pattern_length
    write_pattern_metadata(current_track, slot)

    reaper.gmem_write(TRACK_PATTERN_LENGTH_BASE + current_track - 1, pattern_length)

    reaper.gmem_write(
        7,
        pattern_length
    )

    save_pattern_meta_state(current_track)
    save_track_lengths_state(true)
end


function change_pattern_length(amount)

    set_pattern_length(
        pattern_length
        +
        amount
    )
end


function set_pattern_length_preset(pad_number)

    local presets = {1, 2, 4, 8, 16, 32, 64, 128}
    local value = presets[clamp(math.floor(pad_number), 1, 8)]

    if value then
        set_pattern_length(value)
    end
end


function restart_playback()

    -- RESTART always returns the playback phase to Step 1. The edit
    -- cursor is deliberately left untouched.
    playing = true

    reaper.gmem_write(6, 1)
    reaper.gmem_write(3, 0)
    reaper.gmem_write(0, 1)

    local trigger =
        math.floor(reaper.gmem_read(RESTART_TRIGGER_GMEM)) + 1

    reaper.gmem_write(RESTART_TRIGGER_GMEM, trigger)
end


function toggle_tempo_mode()

    tempo_mode = not tempo_mode

    if tempo_mode then
        start_tutorial("TEMPO")
        pattern_edit_mode = false
        pattern_select_mode = false
        arranger_mode = false
        arranger_cell_edit_mode = false
        browser_overview_mode = false
        reaper.gmem_write(PATTERN_MODE_GMEM, 0)
        reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, 0)
        reaper.gmem_write(ARRANGER_MODE_GMEM, 0)
reaper.gmem_write(BROWSER_MODE_GMEM, 0)
reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, 0)
        reaper.gmem_write(BROWSER_MODE_GMEM, 0)
    else
        tutorial_until = 0
    end

    reaper.gmem_write(TEMPO_MODE_GMEM, tempo_mode and 1 or 0)
end


function change_reaper_tempo(delta)

    local bpm = reaper.Master_GetTempo()
    bpm = clamp(math.floor(bpm + 0.5) + delta, 1, 960)

    reaper.SetCurrentBPM(0, bpm, false)
    reaper.gmem_write(1, bpm)
end


function set_note_division_mode(active)

    note_division_mode = active and true or false

    if note_division_mode then
        start_tutorial("NOTE DIVISION")
    else
        tutorial_until = 0
    end

    publish_mk3_led_modes()
end


function toggle_sensitivity_mode()

    sensitivity_mode = not sensitivity_mode

    if sensitivity_mode then
        start_tutorial("SENSITIVITY")
    else
        tutorial_until = 0
    end

    publish_mk3_led_modes()
end


function set_encoder_sensitivity(level)

    if not sensitivity_mode then return end

    level = clamp(math.floor(level), 1, 8)
    encoder_sensitivity[sensitivity_selected_encoder] = level
    mk3_knob_accumulator[sensitivity_selected_encoder] = 0
    dismiss_tutorial()
    save_user_preferences()
end


function toggle_scale_edit_mode()

    scale_edit_mode = not scale_edit_mode

    if scale_edit_mode then
        start_tutorial("SCALE LOCK")
    else
        tutorial_until = 0
    end

    publish_mk3_led_modes()
end


function toggle_scale_lock()

    scale_lock_enabled = not scale_lock_enabled
    dismiss_tutorial()
    publish_mk3_led_modes()
    save_meta_state(true)
end


function align_arranger_play_positions_on_entry()
    -- Do not switch patterns mid-pattern.  Instead align the stored arranger
    -- position with whatever each track is currently playing so the next
    -- boundary advances naturally.  If the latched pattern is not in the
    -- sequence, use -1 so the next boundary starts at sequence position 1.
    for track_index = 1, NUM_TRACKS do
        local sequence = arranger_sequences[track_index] or {}
        if #sequence > 0 then
            local active_slot = clamp(math.floor(reaper.gmem_read(TRACK_ACTIVE_PATTERN_BASE + track_index - 1)), 1, NUM_PATTERNS)
            local active_rest = reaper.gmem_read(TRACK_ACTIVE_REST_BASE + track_index - 1) > 0
            local current_pos = math.floor(reaper.gmem_read(ARRANGER_PLAY_POS_BASE + track_index - 1))
            local current_entry = (current_pos >= 0 and current_pos < #sequence) and sequence[current_pos + 1] or nil
            local keep = active_rest and current_entry ~= nil and current_entry <= 0 or ((not active_rest) and current_entry == active_slot)
            if not keep then
                local found = -1
                for pos = 1, #sequence do
                    if (active_rest and sequence[pos] ~= nil and sequence[pos] <= 0) or ((not active_rest) and sequence[pos] == active_slot) then
                        found = pos - 1
                        break
                    end
                end
                reaper.gmem_write(ARRANGER_PLAY_POS_BASE + track_index - 1, found)
            end
        end
    end
end


function toggle_arranger_mode()
    arranger_mode = not arranger_mode
    pattern_select_mode = false
    arranger_cell_edit_mode = false
    if arranger_mode then
        browser_overview_mode = false
        visualizer_mode = false
        tempo_mode = false
        pattern_edit_mode = false
        align_arranger_play_positions_on_entry()
        for track_index = 1, NUM_TRACKS do
            if #(arranger_sequences[track_index] or {}) == 0 then
                reaper.gmem_write(TRACK_ACTIVE_REST_BASE + track_index - 1, 1)
                reaper.gmem_write(ARRANGER_PLAY_POS_BASE + track_index - 1, -1)
            end
        end
        sync_arranger_edit_position(current_track)
        start_tutorial("SONG ARRANGER")
    else
        tutorial_until = 0
    end
    reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
    reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
    reaper.gmem_write(VISUALIZER_MODE_GMEM, visualizer_mode and 1 or 0)
    publish_mk3_led_modes()
end

function toggle_browser_overview_mode()
    browser_overview_mode = not browser_overview_mode
    if browser_overview_mode then
        arranger_mode = false
        arranger_cell_edit_mode = false
        visualizer_mode = false
        start_tutorial("OVERVIEW")
    else
        tutorial_until = 0
    end
    reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
    reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
    reaper.gmem_write(VISUALIZER_MODE_GMEM, visualizer_mode and 1 or 0)
    publish_mk3_led_modes()
end

function toggle_visualizer_mode()
    visualizer_mode = not visualizer_mode
    if visualizer_mode then
        arranger_mode = false
        arranger_cell_edit_mode = false
        browser_overview_mode = false
        visualizer_last_cycle = reaper.time_precise()
        start_tutorial("VISUALISER")
    else
        tutorial_until = 0
    end
    reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
    reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
    reaper.gmem_write(VISUALIZER_MODE_GMEM, visualizer_mode and 1 or 0)
    publish_mk3_led_modes()
end

function change_visualizer_style(direction)
    direction = direction < 0 and -1 or 1
    visualizer_style = ((visualizer_style - 1 + direction) % VISUALIZER_STYLE_COUNT) + 1
    visualizer_last_cycle = reaper.time_precise()
    reaper.gmem_write(VISUALIZER_STYLE_GMEM, visualizer_style)
end

function toggle_visualizer_auto()
    visualizer_auto = not visualizer_auto
    visualizer_last_cycle = reaper.time_precise()
    reaper.gmem_write(VISUALIZER_AUTO_GMEM, visualizer_auto and 1 or 0)
end

function update_visualizer_auto_cycle()
    if not visualizer_mode or not visualizer_auto then return end
    local now = reaper.time_precise()
    -- Roughly every four seconds; visual motion itself remains tempo/data driven.
    if now - visualizer_last_cycle >= 4.0 then
        change_visualizer_style(1)
    end
end

function start_playback()

    if playing then
        return
    end

    playing = true

    local start_step =
        math.min(
            current_step,
            pattern_length
        )

    reaper.gmem_write(
        6,
        0
    )

    reaper.gmem_write(
        3,
        start_step - 1
    )

    reaper.gmem_write(
        0,
        1
    )
end


function stop_playback()

    playing = false

    reaper.gmem_write(
        0,
        0
    )

    reaper.gmem_write(
        6,
        1
    )
end


function toggle_playback()

    if playing then
        stop_playback()
    else
        start_playback()
    end
end


function update_scroll()

    local visible =
        get_visible_steps()

    if current_step <
        scroll_step
    then

        scroll_step =
            current_step

    elseif current_step >
        scroll_step
        +
        visible
        -
        1
    then

        scroll_step =
            current_step
            -
            visible
            +
            1
    end

    local maximum =
        MAX_STEPS
        -
        visible
        +
        1

    if maximum < 1 then
        maximum = 1
    end

    scroll_step =
        clamp(
            scroll_step,
            1,
            maximum
        )
end


function move_step(amount)

    if step_wrapping then

        current_step =
            (
                (
                    current_step
                    -
                    1
                    +
                    amount
                )
                %
                pattern_length
            )
            +
            1

    else

        current_step =
            clamp(
                current_step
                +
                amount,
                1,
                MAX_STEPS
            )
    end

    update_scroll()
end


-- CC22 / CC23 bank navigation.
-- Relative Step Mode preserves the row within the bank.
-- First Step Mode always lands on 1, 9, 17, 25 ...
function move_display_bank(direction)

    local row_offset =
        bank_nav_relative
        and
        ((current_step - 1) % 8)
        or
        0

    local bank = math.floor((current_step - 1) / 8)
    local max_bank = math.floor((MAX_STEPS - 1) / 8)
    local new_bank = clamp(bank + direction, 0, max_bank)

    current_step = new_bank * 8 + row_offset + 1
    update_scroll()
end


-- ============================================================
-- MASCHINE JAM
--
-- 8x8 grid = one 64-step page of the CURRENT tracker track.
--
-- Column 1 = Steps 1-8, top to bottom
-- Column 2 = Steps 9-16
-- ...
-- Column 8 = Steps 57-64
--
-- Song CC16 toggles the Jam between two track banks:
--
--   Bank 1: Scene / Group / strips = Tracks 1-8
--   Bank 2: Scene / Group / strips = Tracks 9-16
--
-- Bottom arrows bank the matrix through Steps 1-64, 65-128, etc.
-- ============================================================

function jam_matrix_step(local_step)

    return
        jam_step_bank * 64
        +
        clamp(math.floor(local_step), 1, 64)
end


function change_jam_step_bank(direction)

    local max_bank = math.floor((MAX_STEPS - 1) / 64)
    jam_step_bank = clamp(jam_step_bank + direction, 0, max_bank)

    reaper.gmem_write(JAM_STEP_BANK_GMEM, jam_step_bank)
    save_meta_state(true)
end


function jam_button_to_track(button_index)

    return
        clamp(
            button_index,
            1,
            8
        )
        +
        jam_track_bank
        *
        8
end


function toggle_jam_step_note(step)

    step =
        clamp(
            math.floor(
                step
            ),
            1,
            MAX_STEPS
        )

    local c =
        tracker[
            current_track
        ][step]

    if c.note >= 0 then

        -- Toggle only the musical note itself. Other step parameters
        -- remain available if the user puts a note back later.
        c.note = -1

        write_cell(
            current_track,
            step
        )

        save_track_state(
            current_track,
            true
        )

        return
    end

    -- There is no reference pitch until at least one MK3 musical pad
    -- has been played since the tracker started.
    if last_mk3_pad_note < 0 then
        return
    end

    c.note =
        clamp(
            last_mk3_pad_note,
            0,
            127
        )

    initialise_new_note_parameters(
        c,
        step,
        fixed_velocity and FIXED_VELOCITY_VALUE or default_velocity
    )

    write_cell(
        current_track,
        step
    )

    save_track_state(
        current_track,
        true
    )
        c.velocity,
        current_track
    )

    save_track_state(
        current_track,
        true
    )
end


function transpose_current_pattern(semitones)

    semitones =
        math.floor(
            semitones
        )

    local active_length =
        track_pattern_length[
            current_track
        ]
        or
        pattern_length

    local changed =
        false

    for step = 1, active_length do

        local c =
            tracker[
                current_track
            ][step]

        if c.note >= 0 then

            local new_note =
                c.note
                +
                semitones

            if new_note >= 0
                and
                new_note <= 127
            then

                c.note =
                    new_note

                write_cell(
                    current_track,
                    step
                )

                changed =
                    true
            end
        end
    end

    current_column =
        COL_NOTE

    set_knob_focus(1)

    if changed then

        save_track_state(
            current_track,
            true
        )
    end
end


function toggle_note_audition()
    note_audition_enabled = not note_audition_enabled
    reaper.gmem_write(PAD_MODE_GMEM, note_audition_enabled and 1 or 0)
    reaper.gmem_write(SONG_MODE_GMEM, true_song_mode and 1 or 0)
    save_meta_state(true)
    publish_mk3_led_modes()
end


function toggle_jam_select_mode()

    jam_select_mode =
        not jam_select_mode

    reaper.gmem_write(
        JAM_SELECT_MODE_GMEM,
        jam_select_mode
        and
        1
        or
        0
    )

    save_meta_state(
        true
    )
end


function copy_cell_values(source_cell, destination_cell)

    destination_cell.note =
        source_cell.note

    destination_cell.velocity =
        source_cell.velocity

    destination_cell.pan = RESERVED_CELL3

    destination_cell.command =
        source_cell.command

    destination_cell.parameter =
        source_cell.parameter

    destination_cell.length =
        source_cell.length

    destination_cell.probability =
        source_cell.probability
end


function resize_current_pattern_duplicate(direction)

    direction =
        direction < 0
        and
        -1
        or
        1

    local old_length =
        clamp(
            math.floor(
                track_pattern_length[
                    current_track
                ]
                or
                pattern_length
            ),
            1,
            MAX_STEPS
        )

    if direction > 0 then

        local new_length =
            math.min(
                MAX_STEPS,
                old_length * 2
            )

        if new_length ==
            old_length
        then
            return
        end

        -- Duplicate the musical phrase into the newly-active range.
        for step = old_length + 1, new_length do

            local source_step =
                (
                    (
                        step
                        -
                        old_length
                        -
                        1
                    )
                    %
                    old_length
                )
                +
                1

            copy_cell_values(
                tracker[
                    current_track
                ][source_step],
                tracker[
                    current_track
                ][step]
            )

            write_cell(
                current_track,
                step
            )
        end

        set_pattern_length(
            new_length
        )

        save_track_state(
            current_track,
            true
        )

    else

        local new_length =
            math.max(
                1,
                math.floor(
                    old_length
                    /
                    2
                )
            )

        if new_length ==
            old_length
        then
            return
        end

        -- Halving is non-destructive. Hidden steps retain their data.
        set_pattern_length(
            new_length
        )
    end
end


function randomize_current_velocity(pattern_wide)

    local active_length =
        track_pattern_length[
            current_track
        ]
        or
        pattern_length

    local changed =
        false

    if pattern_wide then

        for step = 1, active_length do

            local c =
                tracker[
                    current_track
                ][step]

            if c.note >= 0 then

                c.velocity =
                    math.random(
                        1,
                        127
                    )

                write_cell(
                    current_track,
                    step
                )

                changed =
                    true
            end
        end

    else

        local c =
            tracker[
                current_track
            ][current_step]

        if c.note >= 0 then

            c.velocity =
                math.random(
                    1,
                    127
                )

            write_cell(
                current_track,
                current_step
            )

            changed =
                true
        end
    end

    current_column =
        COL_VOL

    set_knob_focus(3)

    if changed then

        save_track_state(
            current_track,
            true
        )
    end
end


function randomize_current_probability(pattern_wide)

    local active_length =
        track_pattern_length[
            current_track
        ]
        or
        pattern_length

    local changed =
        false

    if pattern_wide then

        for step = 1, active_length do

            local c =
                tracker[
                    current_track
                ][step]

            if c.note >= 0 then

                c.probability = math.random(0, 10) * 10

                write_cell(
                    current_track,
                    step
                )

                changed =
                    true
            end
        end

    else

        local c =
            tracker[
                current_track
            ][current_step]

        if c.note >= 0 then

            c.probability = math.random(0, 10) * 10


            write_cell(
                current_track,
                current_step
            )

            changed =
                true
        end
    end

    current_column =
        COL_PROB

    set_knob_focus(4)

    if changed then

        save_track_state(
            current_track,
            true
        )
    end
end


-- ============================================================
-- V0.45.5 LATCHED VARIATION MODE
-- Variation probability randomization is quantized to nearest 10%.
-- ============================================================

function set_variation_held(is_held)
    variation_held = is_held and true or false
    reaper.gmem_write(VARIATION_MODE_GMEM, variation_held and 1 or 0)
    publish_mk3_led_modes()
end


function variation_random_probability(minimum, maximum, pattern_wide)
    minimum = clamp(math.floor(minimum or 0), 0, 100)
    maximum = clamp(math.floor(maximum or 100), minimum, 100)
    local changed = false
    local active_length = track_pattern_length[current_track] or pattern_length

    if pattern_wide then
        for step = 1, active_length do
            local c = tracker[current_track][step]
            if c.note >= 0 then
                c.probability = quantize_probability(math.random(minimum, maximum))
                write_cell(current_track, step)
                changed = true
            end
        end
    else
        local c = tracker[current_track][current_step]
        if c.note >= 0 then
            c.probability = quantize_probability(math.random(minimum, maximum))
            write_cell(current_track, current_step)
            changed = true
        end
    end

    current_column = COL_PROB
    set_knob_focus(4)
    if changed then save_track_state(current_track, true) end
end


function variation_random_velocity(minimum, maximum, pattern_wide)
    minimum = clamp(math.floor(minimum or 1), 1, 127)
    maximum = clamp(math.floor(maximum or 127), minimum, 127)
    local changed = false
    local active_length = track_pattern_length[current_track] or pattern_length

    if pattern_wide then
        for step = 1, active_length do
            local c = tracker[current_track][step]
            if c.note >= 0 then
                c.velocity = math.random(minimum, maximum)
                write_cell(current_track, step)
                changed = true
            end
        end
    else
        local c = tracker[current_track][current_step]
        if c.note >= 0 then
            c.velocity = math.random(minimum, maximum)
            write_cell(current_track, current_step)
            changed = true
        end
    end

    current_column = COL_VOL
    set_knob_focus(3)
    if changed then save_track_state(current_track, true) end
end


function variation_note_pool()
    local pool = {}
    local base = octave_base_note()
    if scale_lock_enabled then base = base + scale_root end
    local finish = base + variation_octave_span * 12 - 1
    for note = base, finish do
        if note >= 0 and note <= 127 and (not scale_lock_enabled or is_note_in_scale(note)) then
            pool[#pool + 1] = note
        end
    end
    if #pool == 0 then pool[1] = clamp(base, 0, 127) end
    return pool
end


function variation_bias_pool(pool)
    if variation_bias_mode <= 0 then return {} end
    local wanted = {}
    local root_pc = scale_root % 12
    wanted[root_pc] = true
    if variation_bias_mode >= 2 then wanted[(root_pc + 7) % 12] = true end
    if variation_bias_mode >= 3 then wanted[(root_pc + 5) % 12] = true end

    local biased = {}
    for _, note in ipairs(pool) do
        if wanted[note % 12] then biased[#biased + 1] = note end
    end
    return biased
end


function variation_choose_note(pool, biased)
    if #biased > 0 and variation_bias_strength > 0 then
        if variation_bias_strength >= 100 or math.random(1, 100) <= variation_bias_strength then
            return biased[math.random(1, #biased)]
        end
    end
    return pool[math.random(1, #pool)]
end


function variation_generate_pattern(density)
    density = clamp(math.floor(density or 100), 0, 100)
    local active_length = track_pattern_length[current_track] or pattern_length
    local pool = variation_note_pool()
    local biased = variation_bias_pool(pool)

    -- Clear the complete stored pattern first, then populate only its active
    -- length. This matches CLEAR PATTERN semantics and is one Undo action.
    for step = 1, MAX_STEPS do
        tracker[current_track][step] = new_empty_cell()
        write_cell(current_track, step)
    end

    for step = 1, active_length do
        if density >= 100 or math.random(1, 100) <= density then
            local c = tracker[current_track][step]
            c.note = variation_choose_note(pool, biased)
            c.velocity = RESET_VELOCITY
            c.pan = RESERVED_CELL3
            c.command = 0
            c.parameter = 0
            c.length = RESET_LENGTH
            c.probability = RESET_PROBABILITY
            write_cell(current_track, step)
        end
    end

    current_step = 1
    update_scroll()
    save_track_state(current_track, true)
end


function cycle_variation_bias()
    variation_bias_mode = (variation_bias_mode + 1) % 4

    -- A Bias with 0% weighting has no musical effect, so never leave the
    -- controls in that accidental state. Entering any Bias from OFF starts
    -- at 25%; returning Bias to OFF also turns Weight fully OFF.
    if variation_bias_mode <= 0 then
        variation_bias_strength = 0
    elseif variation_bias_strength <= 0 then
        variation_bias_strength = 25
    end
end


function cycle_variation_strength()
    -- Weight is only meaningful while Bias is active. With Bias OFF, Pad 14
    -- remains OFF and cannot create a hidden weighting value.
    if variation_bias_mode <= 0 then
        variation_bias_strength = 0
        return
    end

    -- While Bias is active, 0% is deliberately omitted so the user cannot
    -- accidentally select a Bias that does nothing.
    local values = {25, 50, 75, 100}

    if variation_bias_strength <= 0 then
        variation_bias_strength = 25
        return
    end

    local index = 1
    for i = 1, #values do
        if values[i] == variation_bias_strength then index = i break end
    end
    index = (index % #values) + 1
    variation_bias_strength = values[index]
end


function handle_variation_pad(pad)
    pad = clamp(math.floor(pad or 1), 1, 16)
    if pad == 1 then variation_random_probability(0, 100, false)
    elseif pad == 2 then variation_random_probability(50, 100, false)
    elseif pad == 3 then variation_random_probability(0, 100, true)
    elseif pad == 4 then variation_random_probability(50, 100, true)
    elseif pad == 5 then variation_random_velocity(60, 127, false)
    elseif pad == 6 then variation_random_velocity(1, 59, false)
    elseif pad == 7 then variation_random_velocity(60, 127, true)
    elseif pad == 8 then variation_random_velocity(1, 59, true)
    elseif pad == 9 then variation_generate_pattern(25)
    elseif pad == 10 then variation_generate_pattern(50)
    elseif pad == 11 then variation_generate_pattern(75)
    elseif pad == 12 then variation_generate_pattern(100)
    elseif pad == 13 then cycle_variation_bias()
    elseif pad == 14 then cycle_variation_strength()
    elseif pad == 15 then variation_octave_span = clamp(variation_octave_span - 1, 1, 2)
    elseif pad == 16 then variation_octave_span = clamp(variation_octave_span + 1, 1, 2)
    end
    publish_mk3_led_modes()
end


function process_hardware_events()

    local read =
        math.floor(
            reaper.gmem_read(
                HW_READ
            )
        )

    local write =
        math.floor(
            reaper.gmem_read(
                HW_WRITE
            )
        )

    local safety = 0


    while read ~= write
        and
        safety < HW_SLOTS
    do

        local address =
            HW_BASE
            +
            read * HW_STRIDE

        local event_type =
            math.floor(
                reaper.gmem_read(
                    address + 0
                )
            )

        local a =
            reaper.gmem_read(
                address + 1
            )

        local b =
            reaper.gmem_read(
                address + 2
            )

        local c =
            reaper.gmem_read(
                address + 3
            )


        if event_type == 1 then

            local pad_index =
                clamp(
                    math.floor(a),
                    0,
                    15
                )

            local velocity =
                clamp(
                    math.floor(b),
                    1,
                    127
                )

            local page =
                clamp(
                    math.floor(c),
                    0,
                    7
                )

            if not mk3_page_initialized
                or
                page ~= mk3_page
            then

                mk3_page =
                    page

                mk3_page_initialized =
                    true

                set_octave_from_page(
                    page
                )
            end

            enter_mk3_pad(
                pad_index,
                velocity
            )


        elseif event_type == 2 then

            move_step(
                math.floor(a)
            )


        elseif event_type == 4 then

            playing = true


        elseif event_type == 5 then

            playing = false


        elseif event_type == 6 then

            apply_encoder(
                math.floor(a)
            )


        elseif event_type == 7 then

            toggle_step_mode()


        elseif event_type == 8 then

            toggle_fixed_velocity()


        elseif event_type == 9 then

            clear_current_step()


        elseif event_type == 10 then

            clear_all_steps_current_track()


        elseif event_type == 11 then

            move_display_bank(
                math.floor(a)
            )


        elseif event_type == 12 then

            move_column(
                math.floor(a)
            )


        elseif event_type == 13 then

            apply_mk3_parameter_knob(
                math.floor(a),
                math.floor(b)
            )


        elseif event_type == 15 then

            touch_mk3_parameter_knob(
                math.floor(a)
            )


        elseif event_type == 16 then

            pattern_edit_mode = math.floor(a) > 0

            if pattern_edit_mode then
                start_tutorial("PATTERN / DIVISION")
                tempo_mode = false
                arranger_mode = false
                reaper.gmem_write(TEMPO_MODE_GMEM, 0)
                reaper.gmem_write(ARRANGER_MODE_GMEM, 0)
reaper.gmem_write(BROWSER_MODE_GMEM, 0)
reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, 0)
            else
                tutorial_until = 0
            end


        elseif event_type == 17 then

            mk3_page =
                clamp(
                    math.floor(a),
                    0,
                    7
                )

            mk3_page_initialized =
                true

            set_octave_from_page(
                mk3_page
            )


        elseif event_type == 18 then

            toggle_inst_lock_mode()


        elseif event_type == 19 then

            set_current_pattern_instruments_to_track()


        elseif event_type == 20 then

            shift_current_pattern_instruments(
                math.floor(a)
            )


        elseif event_type == 21 then

            cycle_current_command(1)


        elseif event_type == 22 then

            transpose_current_note(
                math.floor(a)
            )


        elseif event_type == 23 then

            transpose_current_pattern(
                math.floor(a)
            )


        elseif event_type == 24 then

            follow_modifier =
                math.floor(a)
                >
                0


        elseif event_type == 25 then
            toggle_note_audition()


        elseif event_type == 26 then

            toggle_jam_select_mode()


        elseif event_type == 27 then

            resize_current_pattern_duplicate(
                math.floor(a)
            )


        elseif event_type == 28 then

            randomize_current_velocity(
                math.floor(a)
                >
                0
            )


        elseif event_type == 29 then

            randomize_current_probability(
                math.floor(a)
                >
                0
            )


        elseif event_type == 30 then

            toggle_four_d_mode()


        elseif event_type == 31 then

            undo_edit()


        elseif event_type == 32 then

            redo_edit()


        elseif event_type == 34 then

            toggle_settings_preference(
                math.floor(a)
                +
                1
            )


        elseif event_type == 35 then

            settings_held = math.floor(a) > 0
            reaper.gmem_write(SETTINGS_HELD_GMEM, settings_held and 1 or 0)

        elseif event_type == 36 then

            restart_playback()

        elseif event_type == 37 then

            toggle_tempo_mode()

        elseif event_type == 38 then

            toggle_track_mute(current_track)

        elseif event_type == 39 then

            toggle_track_solo(current_track)

        elseif event_type == 40 then
            toggle_arranger_mode()

        elseif event_type == 41 then

            set_pattern_length_preset(math.floor(a) + 1)
            dismiss_tutorial()

        elseif event_type == 42 then

            set_note_division_mode(math.floor(a) > 0)

        elseif event_type == 43 then

            local division_index = clamp(math.floor(a) + 1, 1, #STEP_DIVISIONS)
            set_current_step_division(STEP_DIVISIONS[division_index])
            dismiss_tutorial()

        elseif event_type == 44 then

            sensitivity_mode = math.floor(a) > 0
            if sensitivity_mode then start_tutorial("SENSITIVITY") else tutorial_until = 0 end
            publish_mk3_led_modes()

        elseif event_type == 45 then

            set_encoder_sensitivity(math.floor(a) + 1)

        elseif event_type == 46 then

            clear_all_mutes()

        elseif event_type == 47 then

            clear_all_solos()

        elseif event_type == 48 then

            scale_edit_mode = math.floor(a) > 0
            if scale_edit_mode then start_tutorial("SCALE LOCK") else tutorial_until = 0 end
            publish_mk3_led_modes()

        elseif event_type == 49 then

            toggle_scale_lock()

        elseif event_type == 50 then

            toggle_visualizer_mode()

        elseif event_type == 51 then
            reset_current_step_defaults()

        elseif event_type == 52 then
            cycle_current_command(-1)

        elseif event_type == 53 then
            select_pattern_slot(math.floor(a) + 1, true)

        elseif event_type == 54 then
            toggle_browser_overview_mode()

        elseif event_type == 55 then
            set_arranger_pattern(math.floor(a) + 1)

        elseif event_type == 56 then
            pattern_select_mode = math.floor(a) > 0
            if pattern_select_mode then
                arranger_mode = false
                arranger_cell_edit_mode = false
                browser_overview_mode = false
                visualizer_mode = false
                start_tutorial("PATTERN SELECT")
            else
                tutorial_until = 0
            end
            reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, pattern_select_mode and 1 or 0)
            reaper.gmem_write(ARRANGER_MODE_GMEM, arranger_mode and 1 or 0)
            reaper.gmem_write(BROWSER_MODE_GMEM, browser_overview_mode and 1 or 0)
            reaper.gmem_write(VISUALIZER_MODE_GMEM, visualizer_mode and 1 or 0)
            publish_mk3_led_modes()

        elseif event_type == 57 then
            move_arranger_grid(math.floor(a), math.floor(b))

        elseif event_type == 58 then
            toggle_arranger_cell_edit_mode()

        elseif event_type == 60 then
            make_selected_pattern_audible()

        elseif event_type == 61 then
            move_arranger_position(math.floor(a))

        elseif event_type == 62 then
            change_arranger_zoom(math.floor(a))

        elseif event_type == 63 then
            copy_arranger_cell()

        elseif event_type == 64 then
            paste_arranger_cell()

        elseif event_type == 65 then
            duplicate_arranger_cell()

        elseif event_type == 66 then
            insert_arranger_rest()

        elseif event_type == 67 then
            reset_current_pattern_defaults()

        elseif event_type == 68 then
            set_variation_held(math.floor(a) > 0)

        elseif event_type == 69 then
            handle_variation_pad(math.floor(a) + 1)
        end


        read =
            read + 1

        if read >= HW_SLOTS then
            read = 0
        end

        safety =
            safety + 1
    end


    reaper.gmem_write(
        HW_READ,
        read
    )
end


function edit_current_value(amount)

    local c =
        tracker[
            current_track
        ][current_step]


    if current_column ==
        COL_NOTE
    then

        if c.note < 0 then

            c.note =
                clamp(
                    octave_base_note(),
                    0,
                    127
                )
            initialise_new_note_parameters(
                c, current_step,
                fixed_velocity and FIXED_VELOCITY_VALUE or default_velocity
            )

        else

            c.note =
                clamp(
                    c.note
                    +
                    amount,
                    0,
                    127
                )
        end


    elseif current_column ==
        COL_VOL
    then

        c.velocity =
            clamp(
                c.velocity
                +
                amount,
                1,
                127
            )

        default_velocity =
            c.velocity


    elseif current_column ==
        COL_CMD
    then

        local new_command = clamp(c.command + amount, 0, 9)
        if new_command ~= c.command then
            c.command = new_command
            c.parameter = command_default_parameter(new_command)
        end


    elseif current_column ==
        COL_PARAM
    then

        c.parameter = command_parameter_clamp(c.command, c.parameter + amount)


    elseif current_column ==
        COL_LEN
    then

        c.length =
            clamp(
                c.length
                +
                amount,
                1,
                64
            )



    elseif current_column ==
        COL_PROB
    then

        c.probability =
            quantize_probability(
                c.probability
                +
                amount * 10
            )


    end

    write_cell(
        current_track,
        current_step
    )

    if current_column ==
        COL_NOTE
    then

        audition_note(
            c.note,
            c.velocity,
            current_track
        )
    end

    save_track_state(
        current_track,
        true
    )
end


function copy_cell()

    local c =
        tracker[
            current_track
        ][current_step]

    clipboard = {
        note = c.note,
        velocity = c.velocity,
        pan = c.pan,
        command = c.command,
        parameter = c.parameter,
        length = c.length,
        probability = c.probability
    }
end


function paste_cell()

    if not clipboard then
        return
    end

    local c =
        tracker[
            current_track
        ][current_step]

    c.note = clipboard.note
    c.velocity = clipboard.velocity

    c.pan = RESERVED_CELL3

    c.command = clipboard.command
    c.parameter = clipboard.parameter
    c.length = clipboard.length
    c.probability = quantize_probability(clipboard.probability)

    write_cell(
        current_track,
        current_step
    )

    save_track_state(
        current_track,
        true
    )
end


function set_command(command, parameter)
    local c = tracker[current_track][current_step]
    c.command = clamp(math.floor(command or 0), 0, 9)
    c.parameter = command_parameter_clamp(c.command, parameter or command_default_parameter(c.command))
    write_cell(current_track, current_step)
    save_track_state(current_track, true)
end

function write_pattern_to_midi()

    local target_track = reaper.GetSelectedTrack(0, 0)
    if not target_track then
        reaper.ShowMessageBox("Select a REAPER track first.", "Maschine Tracker", 0)
        return
    end

    local bpm = reaper.Master_GetTempo()
    local seconds_per_qn = 60 / bpm
    local total_qn = 0

    for track_index = 1, NUM_TRACKS do
        local track_qn = (track_pattern_length[track_index] or 16) *
            step_division_to_qn(track_step_division[track_index] or DEFAULT_STEP_DIVISION)
        total_qn = math.max(total_qn, track_qn)
    end

    total_qn = math.max(total_qn, 0.25)
    local total_seconds = total_qn * seconds_per_qn
    local item = reaper.CreateNewMIDIItemInProj(target_track, 0, total_seconds, false)
    if not item then return end
    local take = reaper.GetActiveTake(item)
    if not take then return end

    local function qn_to_ppq(qn)
        return reaper.MIDI_GetPPQPosFromProjTime(take, qn * seconds_per_qn)
    end

    local function insert_cc(qn, channel, cc, value)
        qn = math.max(0, qn)
        reaper.MIDI_InsertCC(take, false, false, qn_to_ppq(qn), 0xB0, channel, cc, clamp(math.floor(value), 0, 127))
    end

    local function insert_pitch(qn, channel, value)
        qn = math.max(0, qn)
        value = clamp(math.floor(value), 0, 127)
        local bend
        if value < 64 then
            bend = math.floor((value / 64) * 8192 + 0.5)
        elseif value > 64 then
            bend = 8192 + math.floor(((value - 64) / 63) * 8191 + 0.5)
        else
            bend = 8192
        end
        bend = clamp(bend, 0, 16383)
        reaper.MIDI_InsertCC(take, false, false, qn_to_ppq(qn), 0xE0, channel, bend & 0x7F, (bend >> 7) & 0x7F)
    end

    local function insert_note(qn, duration_qn, channel, note, velocity)
        if qn < 0 then qn = 0 end
        local end_qn = math.min(total_qn, qn + math.max(duration_qn, 1 / 128))
        if qn >= total_qn then return end
        reaper.MIDI_InsertNote(take, false, false, qn_to_ppq(qn), qn_to_ppq(end_qn), channel, note, clamp(velocity, 1, 127), true)
    end

    for track = 1, NUM_TRACKS do
        local channel = track - 1
        local track_length = track_pattern_length[track] or 16
        local step_qn = step_division_to_qn(track_step_division[track] or DEFAULT_STEP_DIVISION)
        local step_index = 0
        local start_qn = 0

        while start_qn < total_qn - 0.0000001 do
            local local_step = (step_index % track_length) + 1
            local c = tracker[track][local_step]

            if c.note >= 0 and c.velocity > 0 then
                local trigger = c.probability >= 100 or math.random(1, 100) <= c.probability
                if trigger then
                    local event_qn = start_qn
                    if c.command == 2 then
                        event_qn = event_qn + step_qn * (command_parameter_clamp(2, c.parameter) / 100)
                    elseif c.command == 5 then
                        event_qn = event_qn + step_qn * ((command_parameter_clamp(5, c.parameter) - 50) / 100)
                    end

                    -- Non-PITCH/MOD notes explicitly restore neutral pitch/mod so commands do not leak.
                    if c.command == 3 then insert_pitch(event_qn, channel, c.parameter)
                    else insert_pitch(event_qn, channel, 64) end
                    if c.command == 4 then insert_cc(event_qn, channel, 1, c.parameter)
                    else insert_cc(event_qn, channel, 1, 0) end

                    if c.command == 1 then
                        local repeats = command_parameter_clamp(1, c.parameter)
                        local spacing = step_qn / repeats
                        local gate = spacing * 0.68
                        for r = 0, repeats - 1 do
                            insert_note(event_qn + r * spacing, gate, channel, c.note, c.velocity)
                        end
                    elseif c.command >= 7 and c.command <= 9 then
                        local octave_count = c.command - 5
                        local divisions = command_parameter_clamp(c.command, c.parameter)
                        local spacing = step_qn / divisions
                        local gate = spacing * 0.68
                        for r = 0, octave_count - 1 do
                            local burst_note = c.note + r * 12
                            if burst_note <= 127 then
                                insert_note(event_qn + r * spacing, gate, channel, burst_note, c.velocity)
                            end
                        end
                    elseif c.command == 6 then
                        local duration_qn = c.length * 0.25
                        insert_note(event_qn, duration_qn, channel, c.note, c.velocity)
                        insert_note(event_qn + step_qn * (command_parameter_clamp(6, c.parameter) / 100), duration_qn, channel, c.note, c.velocity)
                    else
                        insert_note(event_qn, c.length * 0.25, channel, c.note, c.velocity)
                    end
                end
            end

            step_index = step_index + 1
            start_qn = step_index * step_qn
        end
    end

    reaper.MIDI_Sort(take)
end

function set_colour(r, g, b, a)

    gfx.set(
        r,
        g,
        b,
        a or 1
    )
end


function draw_rect(
    x,
    y,
    w,
    h,
    filled
)

    gfx.rect(
        x,
        y,
        w,
        h,
        filled and 1 or 0
    )
end


function draw_text(
    x,
    y,
    text
)

    gfx.x = x
    gfx.y = y

    gfx.drawstr(
        tostring(text)
    )
end


function draw_header()

    local tr, tg, tb =
        current_track_colour()

    -- Pale track-colour tint for the whole header.
    set_colour(
        0.78 + tr * 0.18,
        0.78 + tg * 0.18,
        0.78 + tb * 0.18,
        1
    )

    draw_rect(
        0,
        0,
        gfx.w,
        HEADER_HEIGHT,
        true
    )

    -- Strong identity stripe.
    set_colour(
        tr,
        tg,
        tb,
        1
    )

    draw_rect(
        0,
        0,
        gfx.w,
        7,
        true
    )

    set_colour(
        0.08,
        0.08,
        0.08,
        1
    )

    gfx.setfont(
        1,
        "Arial",
        22
    )

    draw_text(
        18,
        10,
        "MASCHINE TRACKER V"
        ..
        VERSION
    )

    gfx.setfont(
        1,
        "Arial",
        12
    )

    draw_text(
        770,
        17,
        project_state_loaded
        and
        "PROJECT STATE RESTORED"
        or
        "NEW PROJECT STATE"
    )

    gfx.setfont(
        1,
        "Arial",
        15
    )

    draw_text(
        18,
        43,
        "TRACK "
        ..
        string.format(
            "%02d",
            current_track
        )
        ..
        "  "
        ..
        string.sub(
            current_track_name(),
            1,
            22
        )
    )

    draw_text(
        275,
        43,
        (
            track_solo[current_track]
            and
            "SOLO"
            or
            (
                track_muted[current_track]
                and
                "MUTE"
                or
                "TRACK READY"
            )
        )
    )

    draw_text(
        445,
        43,
        "STEP "
        ..
        string.format(
            "%02d",
            current_step
        )
    )

    draw_text(
        530,
        43,
        "COLUMN "
        ..
        COLUMN_NAMES[
            current_column
        ]
    )

    draw_text(
        665,
        43,
        "PAT "
        ..
        tostring(
            pattern_length
        )
    )

    draw_text(
        755,
        43,
        (
            track_solo[current_track]
            and
            "SOLO  "
            or
            ""
        )
        ..
        "BANK "
        ..
        display_bank_start()
        ..
        "-"
        ..
        display_bank_end()
    )

    if playing then

        set_colour(
            0.05,
            0.55,
            0.12,
            1
        )

        draw_text(
            735,
            43,
            "PLAY"
        )

    else

        set_colour(
            0.70,
            0.10,
            0.10,
            1
        )

        draw_text(
            735,
            43,
            "STOP"
        )
    end


    if quick_step_mode then

        set_colour(
            0.72,
            0.85,
            1.00,
            1
        )

    else

        set_colour(
            0.82,
            0.82,
            0.82,
            1
        )
    end

    draw_rect(
        MODE_BUTTON_X,
        MODE_BUTTON_Y,
        MODE_BUTTON_W,
        MODE_BUTTON_H,
        true
    )

    set_colour(
        0.04,
        0.04,
        0.04,
        1
    )

    draw_text(
        MODE_BUTTON_X + 7,
        MODE_BUTTON_Y + 5,
        step_mode_name()
    )


    if fixed_velocity then

        set_colour(
            0.72,
            0.95,
            0.72,
            1
        )

    else

        set_colour(
            0.84,
            0.84,
            0.84,
            1
        )
    end

    draw_rect(
        FIXED_BUTTON_X,
        FIXED_BUTTON_Y,
        FIXED_BUTTON_W,
        FIXED_BUTTON_H,
        true
    )

    set_colour(
        0.04,
        0.04,
        0.04,
        1
    )

    draw_text(
        FIXED_BUTTON_X + 7,
        FIXED_BUTTON_Y + 5,
        velocity_mode_name()
    )


    if step_wrapping then

        set_colour(
            1.00,
            0.91,
            0.66,
            1
        )

    else

        set_colour(
            0.84,
            0.84,
            0.84,
            1
        )
    end

    draw_rect(
        WRAP_BUTTON_X,
        WRAP_BUTTON_Y,
        WRAP_BUTTON_W,
        WRAP_BUTTON_H,
        true
    )

    set_colour(
        0.04,
        0.04,
        0.04,
        1
    )

    draw_text(
        WRAP_BUTTON_X + 8,
        WRAP_BUTTON_Y + 5,
        wrapping_mode_name()
    )


    set_colour(
        0.08,
        0.08,
        0.08,
        1
    )

    local base =
        octave_base_note()

    draw_text(
        18,
        78,
        "MK3 PAGE "
        ..
        MK3_PAGE_NAMES[
            mk3_page + 1
        ]
    )

    draw_text(
        125,
        78,
        "ENTRY OCT "
        ..
        tostring(
            entry_octave
        )
    )

    draw_text(
        245,
        78,
        "PADS "
        ..
        note_name(base)
        ..
        " - "
        ..
        note_name(
            math.min(
                127,
                base + 11
            )
        )
    )

    local header_cell =
        tracker[
            current_track
        ][current_step]

    draw_text(
        18,
        105,
        "CMD "
        ..
        command_long_name(
            header_cell.command
        )
    )

    draw_text(
        170,
        105,
        command_value_text(
            header_cell
        )
    )

    draw_text(
        565,
        105,
        "KNOBS: TRACKER"
    )

    draw_text(
        690,
        105,
        pattern_edit_mode
        and
        "LARGE: PAT LEN"
        or
        "LARGE: STEP"
    )

    draw_text(
        820,
        105,
        "CC86"
    )
end


function draw_column_headers()

    set_colour(
        0.82,
        0.82,
        0.82,
        1
    )

    draw_rect(
        0,
        HEADER_Y,
        gfx.w,
        ROW_HEIGHT,
        true
    )

    set_colour(
        0.08,
        0.08,
        0.08,
        1
    )

    gfx.setfont(
        1,
        "Arial",
        15
    )

    draw_text(
        X_STEP,
        HEADER_Y + 5,
        "STEP"
    )

    draw_text(
        X_NOTE,
        HEADER_Y + 5,
        "NOTE"
    )

    draw_text(
        X_VOL,
        HEADER_Y + 5,
        "VOL"
    )

    draw_text(
        X_CMD,
        HEADER_Y + 5,
        "CMD"
    )

    draw_text(
        X_PARAM,
        HEADER_Y + 5,
        "PARAM"
    )

    draw_text(
        X_LEN,
        HEADER_Y + 5,
        "LEN"
    )

    draw_text(
        X_PROB,
        HEADER_Y + 5,
        "PROB"
    )
end


function draw_tracker()

    gfx.setfont(
        1,
        "Consolas",
        17
    )

    local playhead =
        math.floor(
            reaper.gmem_read(3)
        )
        +
        1

    local visible =
        get_visible_steps()

    for row = 0, visible - 1 do

        local step =
            scroll_step
            +
            row

        if step <= MAX_STEPS then

            local y =
                GRID_Y
                +
                row * ROW_HEIGHT

            local cell =
                tracker[
                    current_track
                ][step]

            local outside =
                step > pattern_length

            if outside then

                if step % 4 == 1 then

                    set_colour(
                        0.84,
                        0.84,
                        0.84,
                        1
                    )

                else

                    set_colour(
                        0.88,
                        0.88,
                        0.88,
                        1
                    )
                end

            elseif playing
                and
                step == playhead
            then

                set_colour(
                    1.00,
                    0.95,
                    0.73,
                    1
                )

            elseif step % 4 == 1 then

                set_colour(
                    0.91,
                    0.91,
                    0.91,
                    1
                )

            else

                set_colour(
                    0.97,
                    0.97,
                    0.97,
                    1
                )
            end

            draw_rect(
                0,
                y,
                gfx.w,
                ROW_HEIGHT - 1,
                true
            )

            if is_note_active(
                current_track,
refresh_reaper_track_names(
    true
)

sync_selected_pattern_length()

history_current_snapshot =
    capture_history_snapshot()

undo_history = {}
redo_history = {}

mk3_page =
    clamp(
        entry_octave + 1,
        0,
        7
    )

mk3_page_initialized =
    true

reaper.gmem_write(
    MK3_PAGE_GMEM,
    mk3_page
)

-- MK3 pads always remain tracker-entry pads. PAD MODE now reflects
-- the persisted NOTE AUDITION preference instead of a live-pad state.

-- The 4-D rotary always starts in Step Selector Mode.
four_d_note_mode = false

reaper.gmem_write(PAD_MODE_GMEM, note_audition_enabled and 1 or 0)
    reaper.gmem_write(SONG_MODE_GMEM, true_song_mode and 1 or 0)

reaper.gmem_write(
    JAM_SELECT_MODE_GMEM,
    jam_select_mode
    and
    1
    or
    0
)

reaper.gmem_write(
    JAM_GROUP_MODE_GMEM,
    jam_group_solo_mode
    and
    1
    or
    0
)

follow_modifier = false
tempo_mode = false
arranger_mode = false

reaper.gmem_write(FOLLOW_MOD_GMEM, 0)
reaper.gmem_write(TEMPO_MODE_GMEM, 0)
reaper.gmem_write(ARRANGER_MODE_GMEM, 0)
reaper.gmem_write(BROWSER_MODE_GMEM, 0)
reaper.gmem_write(PATTERN_SELECT_MODE_GMEM, 0)
reaper.gmem_write(NOTES_MODE_GMEM, 0)
reaper.gmem_write(SENSITIVITY_MODE_GMEM, 0)
reaper.gmem_write(SCALE_EDIT_GMEM, 0)
reaper.gmem_write(JAM_DIRECT_NOTE_MODE_GMEM, 0)
reaper.gmem_write(JAM_DIRECT_AUDITION_GMEM, 0)
reaper.gmem_write(JAM_TOUCH_MODE_GMEM, 0)
reaper.gmem_write(JAM_STEP_BANK_GMEM, jam_step_bank)

reaper.gmem_write(
    SETTINGS_HELD_GMEM,
    0
)

publish_mk3_led_modes()

write_solo_summary_gmem()
write_mute_summary_gmem()

reaper.gmem_write(
    JAM_TRACK_BANK_GMEM,
    jam_track_bank
)

reaper.gmem_write(
    0,
    0
)

reaper.gmem_write(
    6,
    1
)

reaper.gmem_write(
    HW_WRITE,
    0
)

reaper.gmem_write(
    HW_READ,
    0
)

-- Discard any Jam events that occurred before the tracker opened.
reaper.gmem_write(
    JAM_READ,
    reaper.gmem_read(
        JAM_WRITE
    )
)

clear_jam_led_state()

write_all_gmem()

reaper.gmem_write(
    6,
    0
)

write_display_state(
    true
)

main()