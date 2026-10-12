#!/usr/bin/env python3
# MASCHINE CONVERTER v0.20.5 — v0.20.4 STABLE ENGINE + CONVERSION-SCOPE TRANSPARENCY
# Generic VST3 automation routing validated with Reaktor/Razor and Massive; KK hosted-state reconstruction and parameter translation validated with Massive.
# No plug-in-specific MXPRJ target -> REAPER parameter fallback table is used.
"""
Maschine -> Komplete Kontrol / REAPER
Maschine Converter v0.20.5

Windows-friendly Tkinter interface around the proven v0.1 conversion core.

Current scope:
  - detects multiple one-zone Maschine internal Sampler Sounds
  - creates one REAPER track / Komplete Kontrol Sampler per detected Sampler Sound
  - restores external VST3 instruments natively, or inside Komplete Kontrol when
    a matching class-ID host shell is bundled/found
  - template RPP needs only one working Komplete Kontrol Sampler track
  - template can contain any sample; sample paths are replaced automatically
  - transfers the currently proven fields:
      sample start/end
      loop start/end/enabled/crossfade
      polyphony
      engine mode
      reverse
      tune
      amp envelope attack/hold/decay/sustain/release

v0.20.5 also reports major source/project categories that this stable build did
not attempt to convert, both in the completion dialog and automation JSON report.

No third-party Python packages are required.
"""

from pathlib import Path
import base64
import json
import re
import struct
import sys
import traceback
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Maschine → REAPER Converter v0.20.5"
SAMPLER_CLASS = b"NI::MASCHINE::DATA::Sampler"
SETTINGS_FILE = Path.home() / ".maschine_to_reaper_converter.json"


# ---------------------------------------------------------------------------
# NI serialization helpers
# ---------------------------------------------------------------------------

def enc_var(n: int) -> bytes:
    if n < 0:
        raise ValueError("Negative NI varints are not supported by this prototype.")
    if n == 0:
        return b"\x00"
    width = (n.bit_length() + 7) // 8
    return bytes([width]) + n.to_bytes(width, "little")


def rec_header(ids) -> bytes:
    return b"".join(enc_var(x) for x in ids) + b"\x00"


def find_one(data: bytes, ids):
    h = rec_header(ids)
    positions = []
    pos = 0
    while True:
        pos = data.find(h, pos)
        if pos < 0:
            break
        positions.append(pos)
        pos += 1
    if len(positions) != 1:
        raise RuntimeError(
            f"Expected one record header {tuple(hex(x) for x in ids)}, "
            f"found {len(positions)}."
        )
    return positions[0], len(h)


def payload_between(data: bytes, ids, next_ids) -> bytes:
    p, hlen = find_one(data, ids)
    q, _ = find_one(data, next_ids)
    if q <= p:
        raise RuntimeError("NI record order did not match the expected sampler layout.")
    return data[p + hlen:q]


def replace_payload(data: bytes, ids, next_ids, payload: bytes) -> bytes:
    p, hlen = find_one(data, ids)
    q, _ = find_one(data, next_ids)
    if q <= p:
        raise RuntimeError("NI record order did not match the expected sampler layout.")
    return data[:p + hlen] + payload + data[q:]


def read_n_varints(payload: bytes, count: int):
    vals = []
    pos = 0
    for _ in range(count):
        if pos >= len(payload):
            raise RuntimeError("Unexpected end of NI integer parameter payload.")
        width = payload[pos]
        pos += 1
        if width == 0:
            vals.append(0)
            continue
        if pos + width > len(payload):
            raise RuntimeError("Malformed NI integer parameter payload.")
        vals.append(int.from_bytes(payload[pos:pos + width], "little"))
        pos += width
    return vals, pos


def replace_first_varints(payload: bytes, values) -> bytes:
    _, pos = read_n_varints(payload, len(values))
    return b"".join(enc_var(v) for v in values) + payload[pos:]


def first_float(payload: bytes) -> float:
    if len(payload) < 4:
        raise RuntimeError("Malformed NI floating parameter payload.")
    return struct.unpack_from("<f", payload, 0)[0]


def first_varint(payload: bytes) -> int:
    return read_n_varints(payload, 1)[0][0]


def segment_after_header_until(data: bytes, ids, marker: bytes) -> bytes:
    p, hlen = find_one(data, ids)
    q = data.find(marker, p + hlen)
    if q < 0:
        raise RuntimeError("Could not locate the end of the duplicated NI zone record.")
    return data[p + hlen:q]


def replace_after_header_until(data: bytes, ids, marker: bytes, segment: bytes) -> bytes:
    p, hlen = find_one(data, ids)
    q = data.find(marker, p + hlen)
    if q < 0:
        raise RuntimeError("Could not locate the end of the duplicated NI zone record.")
    return data[:p + hlen] + segment + data[q:]


# ---------------------------------------------------------------------------
# REAPER / Komplete Kontrol state helpers
# ---------------------------------------------------------------------------

def extract_rpp_kk_state(text: str, require_sampler=True):
    """
    Parse REAPER's Komplete Kontrol VST block while keeping its three logical
    components separate:
      1) REAPER VST wrapper block
      2) NI / Komplete Kontrol state block
      3) REAPER trailing Program Change metadata block
    """
    lines = text.splitlines()

    vst_start = next(
        (i for i, line in enumerate(lines)
         if "<VST " in line and "Komplete Kontrol" in line),
        None,
    )
    if vst_start is None:
        raise RuntimeError("No Komplete Kontrol VST block found in the template RPP.")

    b64_indices = []
    for i in range(vst_start + 1, len(lines)):
        s = lines[i].strip()
        if s == ">":
            break
        if re.fullmatch(r"[A-Za-z0-9+/=]+", s):
            b64_indices.append(i)

    if len(b64_indices) < 3:
        raise RuntimeError("Could not parse the Komplete Kontrol VST state in the RPP.")

    # First padded base64 line ends REAPER's VST wrapper block.
    split = None
    for n, i in enumerate(b64_indices):
        if "=" in lines[i].strip():
            split = n + 1
            break
    if split is None or split >= len(b64_indices) - 1:
        raise RuntimeError("Could not locate the NI state segment in the RPP.")

    wrapper_indices = b64_indices[:split]
    candidates = b64_indices[split:]

    metadata_pos = None
    for n in range(len(candidates) - 1, -1, -1):
        raw = base64.b64decode(lines[candidates[n]].strip())
        if b"Program Change:" in raw:
            metadata_pos = n
            break

    if metadata_pos is None:
        raise RuntimeError(
            "Could not identify REAPER's trailing Program Change metadata block. "
            "Refusing to rewrite this template."
        )

    state_indices = candidates[:metadata_pos]
    metadata_indices = candidates[metadata_pos:]

    wrapper = bytearray(base64.b64decode(
        "".join(lines[i].strip() for i in wrapper_indices)
    ))
    state = bytearray(
        b"".join(base64.b64decode(lines[i].strip()) for i in state_indices)
    )

    if len(wrapper) < 276:
        raise RuntimeError("Unexpected REAPER VST wrapper size.")

    if require_sampler and state.count(SAMPLER_CLASS) != 1:
        raise RuntimeError(
            "v0.20.3 currently requires exactly one Komplete Kontrol Sampler "
            "object in the template."
        )

    return lines, wrapper_indices, state_indices, metadata_indices, wrapper, state

def _rewrite_base64_block(lines, indices, raw: bytes):
    """Rewrite one base64 block while preserving its original line-width pattern."""
    encoded = base64.b64encode(bytes(raw)).decode("ascii")
    indent = lines[indices[0]][:len(lines[indices[0]]) - len(lines[indices[0]].lstrip())]

    old_widths = [len(lines[i].strip()) for i in indices]
    rows = []
    pos = 0
    for width in old_widths[:-1]:
        if pos >= len(encoded):
            break
        rows.append(indent + encoded[pos:pos + width])
        pos += width
    if pos < len(encoded):
        # Continue with REAPER's normal 128-char wrapping if the block grew.
        while len(encoded) - pos > 128:
            rows.append(indent + encoded[pos:pos + 128])
            pos += 128
        rows.append(indent + encoded[pos:])

    start = indices[0]
    stop = indices[-1] + 1
    return lines[:start] + rows + lines[stop:], len(rows) - len(indices)


def install_rpp_state(lines, wrapper_indices, state_indices, wrapper: bytes, state: bytes):
    """
    Rewrite the REAPER wrapper and NI state as separate blocks.

    REAPER's wrapper contains the NI-state byte count at offset 272. If this is
    stale, REAPER can instantiate Komplete Kontrol but KK receives an invalid or
    truncated state and opens blank.
    """
    wrapper = bytearray(wrapper)
    struct.pack_into("<I", wrapper, 272, len(state))

    # Wrapper size does not change, so its line count should remain stable.
    wrapper_encoded = base64.b64encode(bytes(wrapper)).decode("ascii")
    wrapper_widths = [len(lines[i].strip()) for i in wrapper_indices]
    if sum(wrapper_widths) != len(wrapper_encoded):
        raise RuntimeError("Unexpected REAPER wrapper base64 size change.")

    pos = 0
    for idx, width in zip(wrapper_indices, wrapper_widths):
        indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
        lines[idx] = indent + wrapper_encoded[pos:pos + width]
        pos += width

    # Rewrite NI state only. Metadata after it remains untouched.
    encoded = base64.b64encode(bytes(state)).decode("ascii")
    indent = lines[state_indices[0]][:len(lines[state_indices[0]]) - len(lines[state_indices[0]].lstrip())]
    new_rows = [indent + encoded[i:i + 128] for i in range(0, len(encoded), 128)]

    start = state_indices[0]
    stop = state_indices[-1] + 1
    return lines[:start] + new_rows + lines[stop:]


# ---------------------------------------------------------------------------
# v0.20.1 Komplete Kontrol hosted VST3 state reconstruction
# ---------------------------------------------------------------------------

KK_HOST_SHELL_PREFIX = "KK_HostShell_"


def _kk_host_shell_filename(device_id: str):
    class_id = _normalize_probe_class_id(device_id)
    return f"{KK_HOST_SHELL_PREFIX}{class_id}.rppchunk" if class_id else ""


def _kk_host_shell_search_dirs(mxprj_path: Path, template_path: Path, output_path: Path):
    candidates = [
        Path(__file__).resolve().parent,
        template_path.parent,
        mxprj_path.parent,
        output_path.parent,
    ]
    result = []
    seen = set()
    for folder in candidates:
        try:
            key = str(folder.resolve()).lower()
        except Exception:
            key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        if folder.exists() and folder.is_dir():
            result.append(folder)
    return result


def _find_kk_host_shell(instance, mxprj_path: Path, template_path: Path, output_path: Path):
    """
    Locate a captured Komplete Kontrol host shell for the exact hosted VST3
    class ID. A shell is plug-in-family-specific, but its hosted preset/state is
    replaced from the MXPRJ, so it is not preset-specific.
    """
    filename = _kk_host_shell_filename(instance.get("device_id", ""))
    if not filename:
        return None
    for folder in _kk_host_shell_search_dirs(mxprj_path, template_path, output_path):
        path = folder / filename
        if path.is_file():
            return path
    return None


def _build_standard_vst3_preset(instance):
    """Rebuild the exact Steinberg VST3 preset container from parsed chunks."""
    class_id = _normalize_probe_class_id(instance.get("device_id"))
    if not class_id:
        raise RuntimeError("Hosted VST3 instance has no valid class ID.")

    header = bytearray(
        b"VST3"
        + struct.pack("<I", int(instance.get("preset_version", 1)))
        + class_id.encode("ascii")
        + b"\x00" * 8
    )
    data = bytearray()
    entries = []
    offset = 48

    for chunk_id, chunk in instance.get("chunks", []):
        raw_id = str(chunk_id).encode("ascii", errors="strict")
        if len(raw_id) != 4:
            raise RuntimeError(f"Unexpected VST3 chunk id: {chunk_id!r}")
        chunk = bytes(chunk)
        entries.append((raw_id, offset, len(chunk)))
        data += chunk
        offset += len(chunk)

    list_offset = 48 + len(data)
    struct.pack_into("<Q", header, 40, list_offset)

    chunk_list = bytearray(b"List" + struct.pack("<I", len(entries)))
    for raw_id, chunk_offset, chunk_size in entries:
        chunk_list += raw_id
        chunk_list += struct.pack("<Q", chunk_offset)
        chunk_list += struct.pack("<Q", chunk_size)

    return bytes(header + data + chunk_list)


def _standard_vst3_preset_bounds(state: bytes, class_id: str):
    """Return (start, end) for one embedded standard VST3 preset in KK state."""
    class_id = _normalize_probe_class_id(class_id)
    if not class_id:
        raise RuntimeError("Invalid VST3 class ID for KK host shell.")

    # Current Maschine/KK states use VST3 preset version 1, but find by class
    # rather than assuming a preset-specific byte position.
    marker = b"VST3"
    candidates = []
    pos = 0
    while True:
        p = state.find(marker, pos)
        if p < 0:
            break
        if p + 48 <= len(state):
            embedded_class = state[p + 8:p + 40].decode("ascii", errors="ignore").upper()
            if embedded_class == class_id:
                try:
                    list_offset = struct.unpack_from("<Q", state, p + 40)[0]
                    list_pos = p + int(list_offset)
                    if state[list_pos:list_pos + 4] == b"List":
                        count = struct.unpack_from("<I", state, list_pos + 4)[0]
                        end = list_pos + 8 + (20 * int(count))
                        if end <= len(state):
                            candidates.append((p, end))
                except Exception:
                    pass
        pos = p + 1

    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one embedded VST3 preset for {class_id} in the KK shell, "
            f"found {len(candidates)}."
        )
    return candidates[0]


def _replace_kk_hosted_vst3_preset(state: bytes, instance):
    """
    Replace the hosted plug-in's complete standard VST3 preset inside a captured
    Komplete Kontrol shell.

    KK wraps the preset with two size declarations immediately before it:
      NI varint = preset_size + 12
      uint32    = preset_size
    and the whole KK archive has two outer size fields at offsets 0 and 8.

    All four size layers must agree. Leaving the outer/NI sizes stale causes KK
    to reject an otherwise valid hosted state and open empty.
    """
    state = bytes(state)
    start, end = _standard_vst3_preset_bounds(state, instance.get("device_id", ""))
    old_preset = state[start:end]
    old_size = len(old_preset)
    new_preset = _build_standard_vst3_preset(instance)
    new_size = len(new_preset)

    old_prefix = enc_var(old_size + 12) + struct.pack("<III", 1, 1, old_size)
    search_start = max(0, start - 64)
    prefix_pos = state.rfind(old_prefix, search_start, start)
    if prefix_pos < 0 or prefix_pos + len(old_prefix) != start:
        raise RuntimeError(
            "Could not locate Komplete Kontrol's hosted VST3 size wrapper."
        )

    new_prefix = enc_var(new_size + 12) + struct.pack("<III", 1, 1, new_size)
    rebuilt = bytearray(
        state[:prefix_pos]
        + new_prefix
        + new_preset
        + state[end:]
    )

    if len(rebuilt) < 12:
        raise RuntimeError("Rebuilt Komplete Kontrol state is unexpectedly short.")

    # NI serialization archive outer sizes, validated with two independent
    # Massive states and required for KK to accept the transplanted state.
    struct.pack_into("<I", rebuilt, 0, len(rebuilt) - 16)
    struct.pack_into("<I", rebuilt, 8, len(rebuilt) - 20)

    # Defensive validation: the new embedded preset must be byte-for-byte the
    # standard VST3 preset reconstructed from the MXPRJ.
    verify_start, verify_end = _standard_vst3_preset_bounds(
        bytes(rebuilt), instance.get("device_id", "")
    )
    if bytes(rebuilt[verify_start:verify_end]) != new_preset:
        raise RuntimeError("Hosted VST3 state verification failed after KK transplant.")

    return bytes(rebuilt)


def _canonicalize_rpp_track_chunk(text: str):
    """
    REAPER GetTrackStateChunk output is unindented. Convert it to normal project
    indentation so the existing converter track helpers can edit it safely.
    """
    raw = [line.strip() for line in text.splitlines() if line.strip() != ""]
    if not raw or not raw[0].startswith("<TRACK"):
        raise RuntimeError("KK host shell is not a REAPER track-state chunk.")

    lines = []
    depth = 0
    for token in raw:
        if token == ">":
            depth -= 1
            if depth < 0:
                raise RuntimeError("Malformed KK host-shell track chunk.")
            lines.append("  " + ("  " * depth) + token)
            continue

        lines.append("  " + ("  " * depth) + token)
        if token.startswith("<"):
            depth += 1

    if depth != 0:
        raise RuntimeError("KK host-shell track chunk is not balanced.")
    return lines


def _remove_track_blocks(track_lines, prefixes):
    """Remove complete nested RPP blocks whose opening token matches prefixes."""
    result = []
    i = 0
    while i < len(track_lines):
        stripped = track_lines[i].lstrip()
        if any(stripped.startswith(prefix) for prefix in prefixes):
            depth = 0
            while i < len(track_lines):
                s = track_lines[i].lstrip()
                if s.startswith("<"):
                    depth += 1
                elif s == ">":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            continue
        result.append(track_lines[i])
        i += 1
    return result


def _clone_kk_host_track_for_vst3(shell_path: Path, instance, source_key, index):
    """
    Clone a captured KK host shell, replace its hosted VST3 state from the
    Maschine project, and return a normal generated REAPER track.

    Existing shell MIDI and parameter envelopes are removed; the converter adds
    the source project's MIDI/automation later.
    """
    shell_text = shell_path.read_text(encoding="utf-8", errors="replace")
    lines = _canonicalize_rpp_track_chunk(shell_text)
    lines = _remove_track_blocks(lines, ("<ITEM", "<PARMENV"))

    track_guid = _guid(
        f"maschine-kk-host-track:{source_key}:"
        f"{instance['preset_pos']}:{instance['device_id']}"
    )
    fx_guid = _guid(
        f"maschine-kk-host-fx:{source_key}:"
        f"{instance['preset_pos']}:{instance['device_id']}"
    )

    # State-chunk captures may omit a GUID on the opening TRACK line.
    lines[0] = f"  <TRACK {track_guid}"

    safe_name = instance["track_name"].replace('"', "'")
    found_name = False
    found_trackid = False
    found_fxid = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]

        if stripped.startswith("NAME ") and not found_name:
            lines[i] = indent + f'NAME "{safe_name}"'
            found_name = True
        elif stripped.startswith("TRACKID ") and not found_trackid:
            lines[i] = indent + f"TRACKID {track_guid}"
            found_trackid = True
        elif stripped.startswith("FXID ") and not found_fxid:
            lines[i] = indent + f"FXID {fx_guid}"
            found_fxid = True
        elif stripped.startswith("MUTESOLO "):
            lines[i] = indent + "MUTESOLO 0 0 0"
        elif stripped.startswith("SEL "):
            lines[i] = indent + "SEL 0"

    if not found_trackid:
        raise RuntimeError("KK host shell has no TRACKID.")
    if not found_fxid:
        raise RuntimeError("KK host shell has no FXID.")

    (
        parsed_lines,
        wrapper_indices,
        state_indices,
        _metadata_indices,
        wrapper,
        state,
    ) = extract_rpp_kk_state("\n".join(lines), require_sampler=False)

    class_id = _normalize_probe_class_id(instance.get("device_id"))
    descriptor = f":VST3:INSTR:{class_id}".encode("ascii")
    if descriptor not in bytes(state):
        raise RuntimeError(
            f"KK host shell does not describe the requested VST3 class {class_id}."
        )

    new_state = _replace_kk_hosted_vst3_preset(bytes(state), instance)
    result = install_rpp_state(
        parsed_lines,
        wrapper_indices,
        state_indices,
        wrapper,
        new_state,
    )

    instance["host_mode"] = "komplete_kontrol"
    instance["kk_host_shell"] = str(shell_path)
    instance["kk_hosted_state_bytes"] = sum(
        len(chunk) for _chunk_id, chunk in instance.get("chunks", [])
    )
    return result


# ---------------------------------------------------------------------------
# Sample reference conversion
# ---------------------------------------------------------------------------

def read_var_at(data: bytes, pos: int):
    if pos >= len(data):
        raise RuntimeError("Unexpected end of NI variable-length integer.")
    width = data[pos]
    pos += 1
    if width == 0:
        return 0, pos
    if pos + width > len(data):
        raise RuntimeError("Malformed NI variable-length integer.")
    value = int.from_bytes(data[pos:pos + width], "little")
    return value, pos + width


def read_ni_string(data: bytes, pos: int):
    length, pos = read_var_at(data, pos)
    if pos + length > len(data):
        raise RuntimeError("Malformed NI string.")
    raw = data[pos:pos + length]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text, pos + length


def sampler_payload_between(data: bytes, ids, next_ids) -> bytes:
    """Like payload_between(), but anchored inside the single Sampler object."""
    sampler_pos = data.find(SAMPLER_CLASS)
    if sampler_pos < 0:
        raise RuntimeError("Maschine Sampler object not found.")

    h = rec_header(ids)
    nh = rec_header(next_ids)

    p = data.find(h, sampler_pos)
    if p < 0:
        raise RuntimeError(
            f"Sampler record {tuple(hex(x) for x in ids)} not found."
        )

    q = data.find(nh, p + len(h))
    if q < 0 or q <= p:
        raise RuntimeError(
            f"Sampler next record {tuple(hex(x) for x in next_ids)} not found."
        )

    return data[p + len(h):q]


def extract_maschine_sample_reference(mx: bytes):
    """
    Read the single sample reference from the Maschine Sampler object.

    Tested Maschine 3.x layout:
      relative sample path
      library root
      library/product name
    """
    payload = sampler_payload_between(mx, (0x3B8,), (0x3B9,))

    if len(payload) < 4:
        raise RuntimeError("Maschine sampler sample-reference payload is too short.")

    if payload[:3] != b"\x00\x01\x02":
        raise RuntimeError(
            "Unsupported Maschine sample-reference layout. "
            "v0.3 currently supports the tested Maschine 3.x layout."
        )

    pos = 3
    relative_path, pos = read_ni_string(payload, pos)

    if payload[pos:pos + 2] != b"\x00\x00":
        raise RuntimeError("Unexpected separator in Maschine sample-reference data.")
    pos += 2

    library_root, pos = read_ni_string(payload, pos)
    library_name, pos = read_ni_string(payload, pos)

    rel = relative_path.replace("\\", "/")
    root = library_root.replace("\\", "/")

    absolute = bool(
        re.match(r"^[A-Za-z]:/", rel)
        or rel.startswith("//")
        or rel.startswith("/")
    )

    if absolute:
        full_path = rel
    elif root:
        full_path = root.rstrip("/") + "/" + rel.lstrip("/")
    else:
        full_path = rel

    return {
        "relative_path": relative_path,
        "library_root": library_root,
        "library_name": library_name,
        "full_path": full_path,
    }


def _kk_sample_record_bounds(state: bytes):
    sampler_pos = state.find(SAMPLER_CLASS)
    if sampler_pos < 0:
        raise RuntimeError("Komplete Kontrol Sampler object not found.")

    header = rec_header((0x53,))
    next_header = rec_header((0x54,))

    p = state.find(header, sampler_pos)
    q = state.find(next_header, p + len(header))

    if p < 0 or q < 0 or q <= p:
        raise RuntimeError("Could not locate Komplete Kontrol sample-path record.")

    return p, len(header), q


def get_kk_sample_path(state: bytes):
    p, hlen, q = _kk_sample_record_bounds(state)
    payload = state[p + hlen:q]

    if len(payload) < 4:
        raise RuntimeError("Komplete Kontrol sample-path payload is malformed.")

    old_len, text_pos = read_var_at(payload, 2)
    end = text_pos + old_len
    if end > len(payload):
        raise RuntimeError("Komplete Kontrol sample path exceeds its record.")

    try:
        return payload[text_pos:end].decode("utf-8")
    except UnicodeDecodeError:
        return payload[text_pos:end].decode("latin-1")


def replace_kk_sample_path(state: bytes, new_path: str):
    """
    Replace only the absolute sample-path string in the KK Sampler object.
    Prefix/suffix bytes in the NI record are preserved unchanged.
    """
    p, hlen, q = _kk_sample_record_bounds(state)
    payload_start = p + hlen
    payload = state[payload_start:q]

    old_len, old_text_pos = read_var_at(payload, 2)
    old_end = old_text_pos + old_len
    if old_end > len(payload):
        raise RuntimeError("Existing Komplete Kontrol sample path is malformed.")

    encoded_path = new_path.replace("\\", "/").encode("utf-8")
    new_payload = (
        payload[:2]
        + enc_var(len(encoded_path))
        + encoded_path
        + payload[old_end:]
    )

    return state[:payload_start] + new_payload + state[q:]

# ---------------------------------------------------------------------------
# v0.4 multi-Sampler discovery
# ---------------------------------------------------------------------------

BASE_SAMPLE_PATH_ROW_ID = 0x3B8

SOURCE_ZONE_MAPS = [
    ("sample_start",
     (0x3BA,0x3BB,0x3BC), (0x3BD,0x3BE,0x3BF),
     (0x55,0x56,0x57), (0x58,0x59,0x5A)),
    ("sample_end",
     (0x3BD,0x3BE,0x3BF), (0x3C0,0x3C1,0x3C2,0x3C3),
     (0x58,0x59,0x5A), (0x5B,0x5C,0x5D,0x5E)),
    ("loop_start",
     (0x3C0,0x3C1,0x3C2,0x3C3), (0x3C4,0x3C5,0x3C6),
     (0x5B,0x5C,0x5D,0x5E), (0x5F,0x60,0x61)),
    ("loop_end",
     (0x3C4,0x3C5,0x3C6), (0x3C7,0x3C8,0x3C9,0x3CA),
     (0x5F,0x60,0x61), (0x62,0x63,0x64,0x65)),
    ("loop_enabled",
     (0x3C7,0x3C8,0x3C9,0x3CA), (0x3CB,0x3CC,0x3CD,0x3CE),
     (0x62,0x63,0x64,0x65), (0x66,0x67,0x68,0x69)),
    ("loop_xfade",
     (0x3CB,0x3CC,0x3CD,0x3CE), (0x3CF,0x3D0,0x3D1,0x3D2),
     (0x66,0x67,0x68,0x69), (0x6A,0x6B,0x6C,0x6D)),
]

SOURCE_GLOBAL_MAPS = [
    ("polyphony",
     (0x40D,0x40E,0x40F,0x410,0x411), (0x412,0x413,0x414,0x415),
     (0xA8,0xA9,0xAA,0xAB,0xAC), (0xAD,0xAE,0xAF,0xB0), "int"),
    ("engine_mode",
     (0x41A,0x41B,0x41C,0x41D,0x41E), (0x41F,0x420,0x421,0x422,0x423),
     (0xB5,0xB6,0xB7,0xB8,0xB9), (0xBA,0xBB,0xBC,0xBD,0xBE), "int"),
    ("reverse",
     (0x424,0x425,0x426,0x427), (0x428,0x429,0x42A,0x42B),
     (0xBF,0xC0,0xC1,0xC2), (0xC3,0xC4,0xC5,0xC6), "int"),
    ("tune",
     (0x428,0x429,0x42A,0x42B), (0x42C,0x42D,0x42E,0x42F),
     (0xC3,0xC4,0xC5,0xC6), (0xC7,0xC8,0xC9,0xCA), "float"),
]

SOURCE_ENV_MAPS = [
    ("attack",
     (0x479,0x47A,0x47B,0x47C), (0x47D,0x47E,0x47F,0x480),
     (0x114,0x115,0x116,0x117), (0x118,0x119,0x11A,0x11B)),
    ("hold",
     (0x47D,0x47E,0x47F,0x480), (0x481,0x482,0x483,0x484),
     (0x118,0x119,0x11A,0x11B), (0x11C,0x11D,0x11E,0x11F)),
    ("decay",
     (0x481,0x482,0x483,0x484), (0x485,0x486,0x487,0x488),
     (0x11C,0x11D,0x11E,0x11F), (0x120,0x121,0x122,0x123)),
    ("sustain",
     (0x485,0x486,0x487,0x488), (0x489,0x48A,0x48B,0x48C),
     (0x120,0x121,0x122,0x123), (0x124,0x125,0x126,0x127)),
    ("release",
     (0x489,0x48A,0x48B,0x48C), (0x48D,0x48E,0x48F,0x490),
     (0x124,0x125,0x126,0x127), (0x128,0x129,0x12A,0x12B)),
]


def shifted(ids, delta):
    return tuple(x + delta for x in ids)


def _detect_sample_path_row_id(data: bytes, path_start: int, path_length: int):
    """
    Recover the serialized row/object ID immediately before a sample-path
    record. Later Sampler instances use different IDs; their whole parameter
    block is shifted by the same delta from the first tested Sampler.
    """
    encoded_length = enc_var(path_length)
    if data[path_start - len(encoded_length):path_start] != encoded_length:
        return None

    prefix_pos = path_start - len(encoded_length) - 2
    if prefix_pos < 0 or data[prefix_pos:prefix_pos + 2] != b"\x01\x02":
        return None

    candidates = []
    for gap in range(0, 4):
        end = prefix_pos - gap
        for width in (1, 2, 3, 4):
            start = end - (1 + width)
            if start < 0 or data[start] != width:
                continue
            value = int.from_bytes(data[start + 1:end], "little")
            if value >= BASE_SAMPLE_PATH_ROW_ID:
                candidates.append((gap, value))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


def _sample_reference_after_match(data: bytes, match):
    relative_path = match.group(0).decode("utf-8", errors="replace")
    pos = match.end()

    # The first registered Sampler instance has two extra zero bytes here;
    # later instances normally do not.
    skipped = 0
    while pos < len(data) and data[pos] == 0 and skipped < 2:
        pos += 1
        skipped += 1

    library_root = ""
    library_name = ""
    try:
        library_root, pos = read_ni_string(data, pos)
        library_name, pos = read_ni_string(data, pos)
    except Exception:
        pass

    rel = relative_path.replace("\\", "/")
    root = library_root.replace("\\", "/")
    is_absolute = bool(
        re.match(r"^[A-Za-z]:/", rel)
        or rel.startswith("//")
        or rel.startswith("/")
    )

    if is_absolute:
        full_path = rel
    elif root:
        full_path = root.rstrip("/") + "/" + rel.lstrip("/")
    else:
        full_path = rel

    return relative_path, library_root, library_name, full_path


def scan_maschine_samplers(mx: bytes):
    """
    Find supported one-zone internal Sampler Sounds.

    Candidate sample paths are validated by requiring the complete set of
    already-proven Sampler parameter records around them. This rejects unrelated
    sample/path strings elsewhere in the MXPRJ.
    """
    sample_pattern = re.compile(
        rb'(?:Samples|Sounds)/[ -~]{1,260}?\.(?:wav|aif|aiff|flac|ogg|mp3)',
        re.IGNORECASE,
    )

    results = []
    seen_offsets = set()

    for match in sample_pattern.finditer(mx):
        if match.start() in seen_offsets:
            continue

        row_id = _detect_sample_path_row_id(mx, match.start(), len(match.group(0)))
        if row_id is None:
            continue

        delta = row_id - BASE_SAMPLE_PATH_ROW_ID

        try:
            relative_path, library_root, library_name, full_path = \
                _sample_reference_after_match(mx, match)

            payloads = {}
            values = {}

            for name, src_ids, src_next, _dst_ids, _dst_next in SOURCE_ZONE_MAPS:
                payload = payload_between(
                    mx, shifted(src_ids, delta), shifted(src_next, delta)
                )
                payloads[name] = payload
                values[name] = first_varint(payload)

            for name, src_ids, src_next, _dst_ids, _dst_next, value_type in SOURCE_GLOBAL_MAPS:
                payload = payload_between(
                    mx, shifted(src_ids, delta), shifted(src_next, delta)
                )
                payloads[name] = payload
                values[name] = (
                    first_varint(payload)
                    if value_type == "int"
                    else first_float(payload)
                )

            for name, src_ids, src_next, _dst_ids, _dst_next in SOURCE_ENV_MAPS:
                payload = payload_between(
                    mx, shifted(src_ids, delta), shifted(src_next, delta)
                )
                payloads[name] = payload
                values[name] = first_float(payload)

            # Conservative validation to avoid mistaking unrelated serialized
            # records for a Sampler.
            if not (0 <= values["sample_start"] <= values["sample_end"] <= 20_000_000):
                raise ValueError("Implausible sample range.")
            if values["loop_enabled"] not in (0, 1):
                raise ValueError("Invalid loop flag.")
            if not (1 <= values["polyphony"] <= 128):
                raise ValueError("Invalid polyphony.")
            if not (0 <= values["engine_mode"] <= 32):
                raise ValueError("Invalid sampler engine.")
            if not (-1.1 <= values["tune"] <= 1.1):
                raise ValueError("Invalid tuning value.")
            for env_name in ("attack", "hold", "decay", "sustain", "release"):
                if not (-0.1 <= values[env_name] <= 1.1):
                    raise ValueError("Invalid envelope value.")

            track_name = Path(relative_path).stem or f"Sampler {len(results) + 1}"

            result = {
                "row_id": row_id,
                "delta": delta,
                "relative_path": relative_path,
                "library_root": library_root,
                "library_name": library_name,
                "full_path": full_path,
                "track_name": track_name,
                "payloads": payloads,
            }
            result.update(values)
            results.append(result)
            seen_offsets.add(match.start())

        except Exception:
            # A path string on its own is not enough: only accept it when the
            # complete Sampler record set validates.
            continue

    return results

def patch_kk_state_from_sampler(template_state: bytes, sampler):
    """
    Build one KK Sampler state from one parsed Maschine Sampler Sound.
    """
    state = bytes(template_state)

    # Sample reference
    state = replace_kk_sample_path(state, sampler["full_path"])

    # Main zone values
    for name, _src_ids, _src_next, dst_ids, dst_next in SOURCE_ZONE_MAPS:
        state = replace_payload(
            state, dst_ids, dst_next, sampler["payloads"][name]
        )

    # Two confirmed KK cache copies: sample start and sample end.
    # Preserve KK's previous/edit-cache field while changing both active copies.
    for dst_ids, dst_next, value in (
        ((0x98,0x99,0x9A,0x9B), (0x9C,0x9D,0x9E), sampler["sample_start"]),
        ((0x9C,0x9D,0x9E), (0x9F,0xA0,0xA1,0xA2), sampler["sample_end"]),
    ):
        payload = payload_between(state, dst_ids, dst_next)
        state = replace_payload(
            state,
            dst_ids,
            dst_next,
            replace_first_varints(payload, [int(value), int(value)]),
        )

    # Global values.
    for name, _src_ids, _src_next, dst_ids, dst_next, value_type in SOURCE_GLOBAL_MAPS:
        if value_type == "float":
            state = replace_payload(
                state, dst_ids, dst_next, sampler["payloads"][name]
            )
        else:
            value = int(sampler[name])
            payload = payload_between(state, dst_ids, dst_next)
            state = replace_payload(
                state,
                dst_ids,
                dst_next,
                replace_first_varints(payload, [value, value]),
            )

    # Amp-envelope values.
    for name, _src_ids, _src_next, dst_ids, dst_next in SOURCE_ENV_MAPS:
        state = replace_payload(
            state, dst_ids, dst_next, sampler["payloads"][name]
        )

    # NI state internal lengths.
    state = bytearray(state)
    struct.pack_into("<I", state, 0, len(state) - 16)
    struct.pack_into("<I", state, 8, len(state) - 20)

    return bytes(state)


def _find_first_top_level_track(lines):
    for start, line in enumerate(lines):
        if not line.startswith("  <TRACK "):
            continue

        indent = line[:len(line) - len(line.lstrip())]
        end_token = indent + ">"

        for end in range(start + 1, len(lines)):
            if lines[end] == end_token:
                return start, end

    raise RuntimeError("The template RPP does not contain a top-level track.")


def _clone_template_track(template_track_lines, sampler, index, source_key):
    """
    Clone the template KK track, give clones unique REAPER GUIDs, patch its
    Komplete Kontrol state, and name it after the sample.
    """
    text = "\n".join(template_track_lines)

    track_match = re.search(r'\{[0-9A-Fa-f-]{36}\}', template_track_lines[0])
    fx_match = re.search(r'^\s*FXID (\{[0-9A-Fa-f-]{36}\})', text, re.MULTILINE)

    if not track_match or not fx_match:
        raise RuntimeError("Could not find REAPER track/FX GUIDs in the template.")

    old_track_guid = track_match.group(0)
    old_fx_guid = fx_match.group(1)

    # Keep the first track's IDs so template extension metadata remains valid.
    # Additional clones get deterministic unique IDs.
    if index > 0:
        track_guid = "{" + str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"maschine-export-track:{source_key}:{index}:{sampler['full_path']}"
        )).upper() + "}"

        fx_guid = "{" + str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"maschine-export-fx:{source_key}:{index}:{sampler['full_path']}"
        )).upper() + "}"

        text = text.replace(old_track_guid, track_guid)
        text = text.replace(old_fx_guid, fx_guid)

    track_lines = text.splitlines()

    safe_name = sampler["track_name"].replace('"', "'")
    for i, line in enumerate(track_lines):
        if line.startswith("    NAME "):
            track_lines[i] = f'    NAME "{safe_name}"'
            break

    for i, line in enumerate(track_lines):
        if line.strip().startswith("SEL "):
            indent = line[:len(line) - len(line.lstrip())]
            track_lines[i] = indent + ("SEL 1" if index == 0 else "SEL 0")
            break

    (
        parsed_lines,
        wrapper_indices,
        state_indices,
        _metadata_indices,
        wrapper,
        state,
    ) = extract_rpp_kk_state("\n".join(track_lines))

    new_state = patch_kk_state_from_sampler(bytes(state), sampler)

    return install_rpp_state(
        parsed_lines,
        wrapper_indices,
        state_indices,
        wrapper,
        new_state,
    )

# ---------------------------------------------------------------------------
# v0.5 NoteEvent / Pattern reconstruction
# ---------------------------------------------------------------------------

NOTE_EVENT_CLASS = b"NI::MASCHINE::DATA::NoteEvent"


def _locate_note_event_class(data: bytes):
    p = data.find(NOTE_EVENT_CLASS)
    if p < 0:
        return None

    candidates = []
    for s in range(max(0, p - 12), p):
        try:
            class_id, q = read_var_at(data, s)
            text_len, q = read_var_at(data, q)
            if q == p and text_len == len(NOTE_EVENT_CLASS):
                candidates.append((s, class_id))
        except Exception:
            pass

    if not candidates:
        return None

    return p, candidates[-1][1]


def _parse_initial_note_vector(data: bytes, note_class_id: int):
    """
    The first serialized NoteEvent vector defines the Boost class, so its
    header differs slightly from subsequent vectors. The layout below is
    verified against both supplied Simple projects and the complex project.
    """
    class_pos = data.find(NOTE_EVENT_CLASS)
    if class_pos < 0:
        raise RuntimeError("NoteEvent class not found.")

    list_class_id = note_class_id - 5

    # Locate the vector header immediately before the NoteEvent class
    # definition. It contains:
    # list-class, metadata, object-id, count, count, class-id.
    window_start = max(0, class_pos - 64)
    window = data[window_start:class_pos]
    rel = window.rfind(enc_var(list_class_id))
    if rel < 0:
        raise RuntimeError("Initial NoteEvent vector header not found.")

    q = window_start + rel + len(enc_var(list_class_id))

    _meta1, q = read_var_at(data, q)
    _meta0, q = read_var_at(data, q)
    object_id, q = read_var_at(data, q)
    _zero, q = read_var_at(data, q)
    count_a, q = read_var_at(data, q)
    count_b, q = read_var_at(data, q)

    if count_a != count_b or count_a <= 0 or count_a > 4096:
        raise RuntimeError("Unexpected initial NoteEvent vector count.")

    q = class_pos + len(NOTE_EVENT_CLASS)

    # Class-definition metadata.
    _v, q = read_var_at(data, q)
    _v, q = read_var_at(data, q)

    # First NoteEvent defines three nested field records.
    _row, q = read_var_at(data, q)
    _v, q = read_var_at(data, q)
    _v, q = read_var_at(data, q)

    _row, q = read_var_at(data, q)
    _v, q = read_var_at(data, q)
    _v, q = read_var_at(data, q)

    _row, q = read_var_at(data, q)

    first = []
    for _ in range(7):
        v, q = read_var_at(data, q)
        first.append(v)

    events = [{
        "start": 0,
        "flag": first[0],
        "length": first[1],
        "note": first[-2],
        "velocity": first[-1],
    }]

    if count_a > 1:
        # One-time class metadata before the second event.
        _v, q = read_var_at(data, q)
        _v, q = read_var_at(data, q)

    for _index in range(1, count_a):
        _outer_row, q = read_var_at(data, q)
        start_tick, q = read_var_at(data, q)
        class_ref, q = read_var_at(data, q)

        if class_ref != note_class_id:
            raise RuntimeError("Unexpected NoteEvent class reference.")

        for _ in range(3):
            _row, q = read_var_at(data, q)

        core = []
        for _ in range(5):
            v, q = read_var_at(data, q)
            core.append(v)

        events.append({
            "start": start_tick,
            "flag": core[0],
            "length": core[1],
            "note": core[3],
            "velocity": core[4],
        })

    return object_id, events


def _parse_regular_note_vector(data: bytes, pos: int, list_class_id: int, note_class_id: int):
    q = pos

    value, q = read_var_at(data, q)
    if value != list_class_id:
        raise RuntimeError("Not a NoteEvent-list record.")

    object_id, q = read_var_at(data, q)
    count, q = read_var_at(data, q)
    zero, q = read_var_at(data, q)
    class_ref, q = read_var_at(data, q)

    if zero != 0 or class_ref != note_class_id or not (1 <= count <= 4096):
        raise RuntimeError("Invalid NoteEvent-list header.")

    events = []
    start_tick = 0

    for index in range(count):
        for _ in range(3):
            _row, q = read_var_at(data, q)

        core = []
        for _ in range(5):
            v, q = read_var_at(data, q)
            core.append(v)

        event = {
            "start": start_tick,
            "flag": core[0],
            "length": core[1],
            "note": core[3],
            "velocity": core[4],
        }

        if not (
            0 < event["length"] <= 1_000_000
            and 0 <= event["note"] <= 127
            and 1 <= event["velocity"] <= 127
        ):
            raise RuntimeError("Implausible NoteEvent payload.")

        events.append(event)

        if index < count - 1:
            _outer_row, q = read_var_at(data, q)
            start_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if class_ref != note_class_id:
                raise RuntimeError("Unexpected NoteEvent class reference.")

    # Confirm monotonic starts.
    previous = -1
    for event in events:
        if event["start"] < previous:
            raise RuntimeError("Non-monotonic NoteEvent vector.")
        previous = event["start"]

    return object_id, events


def extract_note_vectors(data: bytes):
    """
    Return {serialized_vector_object_id: [NoteEvent, ...]}.
    """
    located = _locate_note_event_class(data)
    if located is None:
        return None, None, {}

    _class_pos, note_class_id = located
    list_class_id = note_class_id - 5

    vectors = {}

    first_object_id, first_events = _parse_initial_note_vector(
        data, note_class_id
    )
    vectors[first_object_id] = first_events

    marker = enc_var(list_class_id)
    pos = 0

    while True:
        p = data.find(marker, pos)
        if p < 0:
            break

        try:
            object_id, events = _parse_regular_note_vector(
                data, p, list_class_id, note_class_id
            )
            vectors.setdefault(object_id, events)
        except Exception:
            pass

        pos = p + 1

    return note_class_id, list_class_id, vectors


def _find_sampler_sound_id(data: bytes, relative_path: str):
    """
    Resolve the Maschine Sound object ID belonging to a Sampler.

    Once the Sound class is defined, the relevant record is:
        class 23 -> sound object id -> field 2

    The first Sound in the project contains additional Boost class-definition
    metadata (1, 9) between class 23 and its object id.
    """
    path_bytes = relative_path.encode("utf-8")
    path_positions = [
        m.start() for m in re.finditer(re.escape(path_bytes), data)
    ]

    if not path_positions:
        return None

    class_23 = enc_var(23)
    definition_tail = enc_var(1) + enc_var(9)

    best = None

    for path_pos in path_positions:
        start = max(0, path_pos - 6000)
        pos = start

        while True:
            p = data.find(class_23, pos, path_pos)
            if p < 0:
                break

            after = p + len(class_23)
            variants = []

            # First Sound object: class-definition form.
            if data[after:after + len(definition_tail)] == definition_tail:
                try:
                    sound_id, q = read_var_at(
                        data, after + len(definition_tail)
                    )
                    next_value, _q = read_var_at(data, q)
                    if next_value == 2:
                        variants.append(sound_id)
                except Exception:
                    pass

            # Subsequent Sound objects: ordinary class-reference form.
            try:
                sound_id, q = read_var_at(data, after)
                next_value, _q = read_var_at(data, q)
                if next_value == 2:
                    variants.append(sound_id)
            except Exception:
                pass

            for sound_id in variants:
                if not (100 <= sound_id < 200000):
                    continue

                distance = path_pos - p
                if best is None or distance < best[0]:
                    best = (distance, sound_id)

            pos = p + 1

    return None if best is None else best[1]


def _parse_pattern_length(data: bytes, pos: int, name_bytes: bytes):
    q = pos + len(name_bytes)

    # Four object/field rows precede the timing fields.
    for _ in range(4):
        _row, q = read_var_at(data, q)

    zero, q = read_var_at(data, q)
    length_ticks, q = read_var_at(data, q)

    if zero != 0 or not (1 <= length_ticks <= 10_000_000):
        raise RuntimeError("Not a supported Maschine Pattern record.")

    return length_ticks


def extract_patterns_for_samplers(data: bytes, sampler_sound_ids):
    """
    Decode Pattern -> Sound -> NoteEvent-list associations for Sampler Sounds.

    Each Pattern contains a series of Sound slots. A populated slot contains a
    reference to one serialized NoteEvent vector followed by the Sound object
    reference.
    """
    _note_class, list_class_id, vectors = extract_note_vectors(data)

    if not vectors:
        return [], {}

    sampler_sound_ids = {
        sid for sid in sampler_sound_ids if sid is not None
    }

    patterns = []

    for match in re.finditer(b"Pattern", data):
        pos = match.start()

        name_match = re.match(rb"Pattern[ -~]*", data[pos:pos + 96])
        if not name_match:
            continue

        name_bytes = name_match.group()

        try:
            length_ticks = _parse_pattern_length(
                data, pos, name_bytes
            )
        except Exception:
            continue

        # Sound-slot tables are compact; 360 bytes safely covers the 16 slots
        # in the supplied Maschine 2/3 project structures.
        chunk = data[pos:pos + 360]
        slots = {}

        for sound_id in sampler_sound_ids:
            sound_marker = enc_var(23) + enc_var(sound_id)
            slot_pos = chunk.find(sound_marker)

            if slot_pos < 0:
                continue

            vector_id = 0

            # A populated slot stores:
            #   list-class reference + NoteEvent-vector object id
            # immediately before the Sound reference.
            for candidate_id in vectors:
                pointer = enc_var(list_class_id) + enc_var(candidate_id)
                if (
                    slot_pos >= len(pointer)
                    and chunk[slot_pos - len(pointer):slot_pos] == pointer
                ):
                    vector_id = candidate_id
                    break

            slots[sound_id] = vector_id

        if slots:
            patterns.append({
                "source_offset": pos,
                "name": name_bytes.decode("utf-8", errors="replace"),
                "length": length_ticks,
                "slots": slots,
            })

    return patterns, vectors



def _mxprj_tempo(data: bytes):
    """Recover Maschine project tempo from the stable root transport record.

    Observed root layout stores fields 7/8 followed by two identical float32
    BPM values. Restrict the search to the project header area to avoid
    matching plug-in state blobs.
    """
    marker = enc_var(7) + enc_var(8)
    pos = 0
    search_end = min(len(data), 64 * 1024)

    while True:
        p = data.find(marker, pos, search_end)
        if p < 0:
            return None
        q = p + len(marker)
        # A zero record delimiter precedes the two raw float32 BPM values.
        if q < len(data) and data[q] == 0:
            q += 1
        if q + 8 <= len(data):
            try:
                a = struct.unpack_from("<f", data, q)[0]
                b = struct.unpack_from("<f", data, q + 4)[0]
                if (
                    20.0 <= a <= 400.0
                    and abs(a - b) < 1e-5
                ):
                    return float(a)
            except Exception:
                pass
        pos = p + 1


def _set_project_tempo_line(lines, tempo: float):
    result = list(lines)
    for i, line in enumerate(result):
        if line.strip().startswith("TEMPO "):
            indent = line[:len(line) - len(line.lstrip())]
            result[i] = f"{indent}TEMPO {tempo:g} 4 4 0"
            break
    return result

def _template_tempo(rpp_text: str):
    m = re.search(
        r"(?m)^\s*TEMPO\s+([0-9]+(?:\.[0-9]+)?)\s+",
        rpp_text,
    )
    if not m:
        return 120.0
    return float(m.group(1))


def _ticks_to_seconds(ticks: int, tempo: float):
    # Maschine NoteEvents and REAPER MIDI source both use 960 PPQ here.
    return (ticks / 960.0) * (60.0 / tempo)


def _guid(namespace_text: str):
    return "{" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, namespace_text)
    ).upper() + "}"


def _build_midi_item_lines(pattern, events, position_tick, tempo, identity):
    """
    Build one inline REAPER MIDI item. Event timing remains at Maschine's
    native 960 PPQ.
    """
    pattern_length = int(pattern["length"])
    max_note_end = max(
        (int(e["start"]) + int(e["length"]) for e in events),
        default=0,
    )
    source_end_tick = max(pattern_length, max_note_end)

    midi_events = []

    for event in events:
        start = int(event["start"])
        end = start + int(event["length"])
        note = int(event["note"])
        velocity = max(1, min(127, int(event["velocity"])))

        # note-off before note-on when events share a tick
        midi_events.append((start, 1, 0x90, note, velocity))
        midi_events.append((end, 0, 0x80, note, 0))

    midi_events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    event_lines = []
    previous_tick = 0

    for tick, _priority, status, data1, data2 in midi_events:
        delta = tick - previous_tick
        event_lines.append(
            f"        E {delta} {status:02x} {data1:02x} {data2:02x}"
        )
        previous_tick = tick

    # Terminate the source cleanly at the Pattern boundary.
    final_delta = max(0, source_end_tick - previous_tick)
    event_lines.append(f"        E {final_delta} b0 7b 00")

    item_guid = _guid(identity + ":item")
    take_guid = _guid(identity + ":take")
    pool_guid = _guid(identity + ":pool")

    name = pattern["name"].replace('"', "'")
    position_seconds = _ticks_to_seconds(position_tick, tempo)
    length_seconds = _ticks_to_seconds(source_end_tick, tempo)

    lines = [
        "    <ITEM",
        f"      POSITION {position_seconds:.12f}",
        "      SNAPOFFS 0",
        f"      LENGTH {length_seconds:.12f}",
        "      LOOP 0",
        "      ALLTAKES 0",
        "      FADEIN 1 0 0 1 0 0 0",
        "      FADEOUT 1 0 0 1 0 0 0",
        "      MUTE 0 0",
        "      SEL 0",
        f"      IGUID {item_guid}",
        f'      NAME "{name}"',
        "      VOLPAN 1 0 1 -1",
        "      SOFFS 0 0",
        "      PLAYRATE 1 1 0 -1 0 0.0025",
        "      CHANMODE 0",
        f"      GUID {take_guid}",
        "      <SOURCE MIDI",
        "        HASDATA 1 960 QN",
        "        CCINTERP 32",
        f"        POOLEDEVTS {pool_guid}",
    ]

    lines.extend(event_lines)

    lines.extend([
        "        CCINTERP 32",
        "        CHASE_CC_TAKEOFFS 1",
        f"        GUID {_guid(identity + ':source')}",
        f"        IGNTEMPO 0 {tempo:g} 4 4",
        "      >",
        "    >",
    ])

    return lines


def _insert_midi_items(track_lines, items):
    if not items:
        return track_lines

    # _clone_template_track returns one complete top-level track.
    if not track_lines or track_lines[-1] != "  >":
        raise RuntimeError("Unexpected generated REAPER track structure.")

    flattened = []
    for item in items:
        flattened.extend(item)

    return track_lines[:-1] + flattened + [track_lines[-1]]



def _midi_event_priority(status, data1, injected_cc=None):
    """Stable ordering for merged MIDI events at the same source tick."""
    msg = int(status) & 0xF0
    if injected_cc is not None and msg == 0xB0 and int(data1) == int(injected_cc):
        return -20
    if msg == 0x80:
        return 0
    if msg == 0x90:
        return 10
    if msg == 0xB0 and int(data1) == 123:
        return 100
    return 20


def _inject_cc_curve_into_midi_item_lines(item_lines, cc_number, cc_points):
    """
    Merge one local-tick MIDI CC curve into an already-built REAPER MIDI item.

    cc_points: iterable of (local_tick, value_0_to_127).
    """
    result = list(item_lines)
    source_start = next(
        (i for i, line in enumerate(result) if line.strip() == "<SOURCE MIDI"),
        None,
    )
    if source_start is None:
        return result

    pool_index = next(
        (
            i for i in range(source_start + 1, len(result))
            if result[i].strip().startswith("POOLEDEVTS ")
        ),
        None,
    )
    if pool_index is None:
        return result

    event_start = pool_index + 1
    event_end = event_start
    while event_end < len(result) and result[event_end].lstrip().startswith("E "):
        event_end += 1

    parsed = []
    absolute_tick = 0
    sequence = 0
    for line in result[event_start:event_end]:
        parts = line.strip().split()
        if len(parts) < 5 or parts[0] != "E":
            continue
        try:
            delta = int(parts[1])
            status = int(parts[2], 16)
            data1 = int(parts[3], 16)
            data2 = int(parts[4], 16)
        except Exception:
            continue
        absolute_tick += delta
        parsed.append((
            absolute_tick,
            _midi_event_priority(status, data1),
            sequence,
            status,
            data1,
            data2,
        ))
        sequence += 1

    # Replace same-controller events if this helper is called repeatedly.
    parsed = [
        item for item in parsed
        if not ((item[3] & 0xF0) == 0xB0 and item[4] == int(cc_number))
    ]

    for tick, value in cc_points:
        tick = max(0, int(tick))
        value = max(0, min(127, int(value)))
        parsed.append((
            tick,
            _midi_event_priority(0xB0, cc_number, injected_cc=cc_number),
            sequence,
            0xB0,
            int(cc_number),
            value,
        ))
        sequence += 1

    parsed.sort(key=lambda item: (item[0], item[1], item[2]))

    rebuilt = []
    previous_tick = 0
    for tick, _priority, _sequence, status, data1, data2 in parsed:
        delta = int(tick) - int(previous_tick)
        rebuilt.append(
            f"        E {delta} {int(status):02x} {int(data1):02x} {int(data2):02x}"
        )
        previous_tick = int(tick)

    return result[:event_start] + rebuilt + result[event_end:]


def _canonical_pattern_cc_curves(events, target_meta):
    """
    Convert expanded Pattern automation back to one local MIDI-CC curve per
    Pattern id/name, including an explicit tick-0 reset when needed.
    """
    grouped = {}
    for event in events:
        if event.get("automation_expansion") != "pattern_occurrence":
            continue
        pattern_id = event.get("pattern_id")
        pattern_name = str(event.get("pattern_name") or "")
        local_tick = event.get("local_tick")
        if not isinstance(local_tick, int):
            continue
        grouped.setdefault((pattern_id, pattern_name), []).append(event)

    curves = {}
    for key, pattern_events in grouped.items():
        # Multiple Arranger occurrences serialize the same local Pattern curve.
        # Canonicalize by local tick/value so only one source curve is inserted
        # into each pooled MIDI item.
        local_points = {}
        for event in sorted(
            pattern_events,
            key=lambda item: (
                int(item.get("local_tick", 0)),
                int(item.get("absolute_tick", 0)),
            ),
        ):
            local_tick = int(event.get("local_tick", 0))
            unit = _maschine_mod_value_to_unit(
                event.get("value", 0.0),
                event.get("kind", "float"),
                target_meta,
            )
            local_points.setdefault(local_tick, float(unit))

        if not local_points:
            continue

        first_tick = min(local_points)
        if first_tick > 0:
            first_event = min(
                pattern_events,
                key=lambda item: int(item.get("local_tick", 0)),
            )
            local_points[0] = _automation_occurrence_start_unit(
                first_event.get("kind", "float"),
                target_meta,
                first_event,
            )

        points = []
        for tick in sorted(local_points):
            unit = max(0.0, min(1.0, float(local_points[tick])))
            # MIDI CC is 7-bit. Round to the nearest representable value.
            value = int(round(unit * 127.0))
            points.append((int(tick), value))
        curves[key] = points

    return curves


def _item_pattern_name(item_lines):
    for line in item_lines:
        m = re.match(r'\s*NAME\s+"(.+)"\s*$', line)
        if not m:
            continue
        name = m.group(1)
        if " | " in name:
            return name.rsplit(" | ", 1)[1]
        return name
    return ""


def _apply_midi_cc_automation_to_instrument_items(
    instruments,
    automation_events_by_sound,
    target_metadata,
):
    """
    AB04: embed profile-resolved MIDI-CC automation into the destination Pattern
    MIDI items and return a diagnostic list of applied routes.
    """
    applied = []
    for instrument in instruments:
        sound_id = instrument.get("sound_id")
        if sound_id is None or not instrument.get("midi_items"):
            continue
        sound_events = automation_events_by_sound.get(int(sound_id), [])
        if not sound_events:
            continue

        grouped = {}
        for event in sound_events:
            key = (event["kind"], int(event["target_id"]))
            meta = target_metadata.get(key, {})
            cc_number = meta.get("midi_cc_number")
            if not isinstance(cc_number, int):
                continue
            grouped.setdefault(key, []).append(event)

        for key, events in grouped.items():
            meta = target_metadata.get(key, {})
            cc_number = int(meta["midi_cc_number"])
            curves = _canonical_pattern_cc_curves(events, meta)
            if not curves:
                continue

            new_items = []
            injected_items = 0
            total_points = 0
            for item_lines in instrument["midi_items"]:
                pattern_name = _item_pattern_name(item_lines)
                candidates = [
                    points
                    for (_pattern_id, name), points in curves.items()
                    if name == pattern_name
                ]
                if len(candidates) == 1:
                    points = candidates[0]
                    item_lines = _inject_cc_curve_into_midi_item_lines(
                        item_lines,
                        cc_number,
                        points,
                    )
                    injected_items += 1
                    total_points += len(points)
                new_items.append(item_lines)

            instrument["midi_items"] = new_items
            applied.append({
                "sound_id": int(sound_id),
                "track_name": instrument.get("track_name", ""),
                "target_id": int(key[1]),
                "parameter_name": (
                    meta.get("midi_cc_parameter_name")
                    or meta.get("name")
                    or ""
                ),
                "cc_number": cc_number,
                "midi_items_injected": injected_items,
                "cc_points_written": total_points,
                "mapping": meta.get("midi_cc_mapping", ""),
            })

    return applied


def _build_vst_wake_midi_item(tempo: float, identity: str):
    """
    Build a tiny, low-velocity MIDI note item for an otherwise empty
    instrument track.

    Purpose: keep REAPER from leaving restored instruments offline/lazy-loaded.
    The containing track may remain muted, so the note is only a load trigger.
    """
    pattern = {
        "name": "__VST_WAKE__",
        "length": 7680,  # 2 bars of 4/4 at 960 PPQ
    }
    events = [{
        "start": 0,
        "length": 7680,
        "note": 60,      # C4
        "velocity": 1,
    }]
    return _build_midi_item_lines(
        pattern,
        events,
        0,
        tempo,
        identity + ":vst-wake",
    )

# ---------------------------------------------------------------------------
# v0.6 Scene / Section arrangement reconstruction
# ---------------------------------------------------------------------------


def _serialized_object_id_before_name(data: bytes, name_pos: int, class_id: int, definition_values):
    """
    Recover the serialized object id belonging to a named object.

    Boost writes a short class-definition tuple before the first object of a
    class; subsequent objects contain only the object id. For the structures
    used here:
      Pattern: class 128, first-object metadata [1, 5]
      Scene:   class 184, first-object metadata [1, 2]
    """
    marker = enc_var(class_id)
    start = max(0, name_pos - 64)
    window = data[start:name_pos]
    candidates = []
    offset = 0

    while True:
        rel = window.find(marker, offset)
        if rel < 0:
            break

        q = start + rel + len(marker)
        try:
            first, q1 = read_var_at(data, q)
            object_id = None

            if definition_values and first == definition_values[0]:
                values = [first]
                qq = q1
                for _ in range(len(definition_values) - 1):
                    value, qq = read_var_at(data, qq)
                    values.append(value)
                if values == list(definition_values):
                    object_id, _ = read_var_at(data, qq)

            if object_id is None:
                object_id = first

            if 0 < object_id < 10_000_000:
                candidates.append((start + rel, object_id))
        except Exception:
            pass

        offset = rel + 1

    return candidates[-1][1] if candidates else None


def _pattern_object_id(data: bytes, pattern):
    return _serialized_object_id_before_name(
        data,
        pattern['source_offset'],
        128,
        (1, 5),
    )


def extract_scene_map(data: bytes):
    """
    Decode Maschine Scene objects.

    Each Scene has eight Group slots. Each Group slot either contains no
    Pattern (ff 01) or a reference to one Pattern object (class 128).
    """
    matches = []

    for match in re.finditer(b'Scene ', data):
        pos = match.start()
        name_match = re.match(rb'Scene [ -~]*', data[pos:pos + 96])
        if name_match:
            matches.append((pos, name_match.group().decode('utf-8', errors='replace')))

    scenes = {}

    for index, (pos, name) in enumerate(matches):
        scene_id = _serialized_object_id_before_name(
            data,
            pos,
            184,
            (1, 2),
        )
        if scene_id is None:
            continue

        end = matches[index + 1][0] if index + 1 < len(matches) else min(len(data), pos + 360)
        chunk = data[pos + len(name.encode('utf-8')):end]

        slots = {}
        search_pos = 0
        group_marker = enc_var(22)

        while True:
            p = chunk.find(group_marker, search_pos)
            if p < 0:
                break

            try:
                group_id, q = read_var_at(chunk, p + len(group_marker))

                # First Scene / first Group contains one-time class metadata.
                if chunk[q:q + 3] == b'\x00\x01\x01':
                    q += 3

                slot_class, q = read_var_at(chunk, q)
                if slot_class != 189:
                    search_pos = p + 1
                    continue

                _slot_object_id, q = read_var_at(chunk, q)
                pattern_id = 0

                if chunk[q:q + 2] == b'\xff\x01':
                    q += 2
                elif chunk[q:q + len(enc_var(128))] == enc_var(128):
                    pattern_id, q = read_var_at(chunk, q + len(enc_var(128)))
                else:
                    search_pos = p + 1
                    continue

                slots[group_id] = pattern_id
            except Exception:
                pass

            search_pos = p + 1

        if slots:
            scenes[scene_id] = {
                'scene_id': scene_id,
                'name': name,
                'slots': slots,
                'source_offset': pos,
            }

    return scenes


def extract_arrangement_sections(data: bytes, scenes):
    """
    Decode the ordered Maschine Arranger Section list.

    Section class 195 references one Scene (class 184) and stores its active
    Section length twice, followed by a previous/edit-cache value. The final
    entry has no Scene and acts as the list terminator; it is not exported.
    """
    if not scenes:
        return []

    last_scene_pos = max(scene['source_offset'] for scene in scenes.values())
    marker = enc_var(195)
    scene_marker = enc_var(184)

    sections = []
    search_pos = last_scene_pos

    while True:
        p = data.find(marker, search_pos)
        if p < 0:
            break

        q = p + len(marker)

        try:
            # First Section defines the class: [1, 0] then object id.
            first, q1 = read_var_at(data, q)
            section_object_id = None

            if first == 1:
                second, qq = read_var_at(data, q1)
                if [first, second] == [1, 0]:
                    section_object_id, q = read_var_at(data, qq)

            if section_object_id is None:
                section_object_id = first
                q = q1

            scene_id = None

            if data[q:q + len(scene_marker)] == scene_marker:
                scene_id, q = read_var_at(data, q + len(scene_marker))
            elif data[q:q + 2] == b'\xff\x01':
                q += 2
            else:
                search_pos = p + 1
                continue

            # Four serialized row/object ids precede the timing fields.
            for _ in range(4):
                _value, q = read_var_at(data, q)

            timing = []
            for _ in range(6):
                value, q = read_var_at(data, q)
                timing.append(value)

            # [0, current_length, current_length, previous_length, 0, 0]
            if (
                timing[0] != 0
                or timing[1] != timing[2]
                or not (1 <= timing[1] <= 100_000_000)
            ):
                search_pos = p + 1
                continue

            # A no-Scene record is the Arranger terminator.
            if scene_id is None:
                break

            scene = scenes.get(scene_id)
            if scene is None:
                search_pos = p + 1
                continue

            sections.append({
                'section_object_id': section_object_id,
                'scene_id': scene_id,
                'name': scene['name'],
                'length': timing[1],
                'previous_length': timing[3],
                'source_offset': p,
            })

        except Exception:
            pass

        search_pos = p + 1

    return sections


def _build_arranged_midi_item_lines(section, pattern, events, position_tick, tempo, identity, sound_id):
    """
    Build one REAPER MIDI item representing a Pattern inside one Maschine
    Section.

    The MIDI source length is the Pattern length. The REAPER item length is the
    Section length and LOOP is enabled, so short Maschine Patterns repeat to
    fill a longer Section while long Patterns are naturally trimmed by a short
    Section. This mirrors Maschine's Arranger behavior without flattening the
    repeated pattern data.
    """
    pattern_length = int(pattern['length'])
    section_length = int(section['length'])

    midi_events = []

    for event in events:
        start = int(event['start'])
        if start >= pattern_length:
            continue

        end = min(pattern_length, start + int(event['length']))
        note = max(0, min(127, int(event['note'])))
        velocity = max(1, min(127, int(event['velocity'])))

        midi_events.append((start, 1, 0x90, note, velocity))
        midi_events.append((end, 0, 0x80, note, 0))

    midi_events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    event_lines = []
    previous_tick = 0

    for tick, _priority, status, data1, data2 in midi_events:
        delta = tick - previous_tick
        event_lines.append(
            f'        E {delta} {status:02x} {data1:02x} {data2:02x}'
        )
        previous_tick = tick

    final_delta = max(0, pattern_length - previous_tick)
    event_lines.append(f'        E {final_delta} b0 7b 00')

    item_guid = _guid(identity + ':item')
    take_guid = _guid(identity + ':take')

    # Reused Pattern/Sound combinations share a pool id across Sections.
    pool_guid = _guid(
        f'maschine-pattern-pool:{sound_id}:{pattern["pattern_id"]}'
    )

    name = f'{section["name"]} | {pattern["name"]}'.replace('"', "'")
    position_seconds = _ticks_to_seconds(position_tick, tempo)
    length_seconds = _ticks_to_seconds(section_length, tempo)

    lines = [
        '    <ITEM',
        f'      POSITION {position_seconds:.12f}',
        '      SNAPOFFS 0',
        f'      LENGTH {length_seconds:.12f}',
        '      LOOP 1',
        '      ALLTAKES 0',
        '      FADEIN 1 0 0 1 0 0 0',
        '      FADEOUT 1 0 0 1 0 0 0',
        '      MUTE 0 0',
        '      SEL 0',
        f'      IGUID {item_guid}',
        f'      NAME "{name}"',
        '      VOLPAN 1 0 1 -1',
        '      SOFFS 0 0',
        '      PLAYRATE 1 1 0 -1 0 0.0025',
        '      CHANMODE 0',
        f'      GUID {take_guid}',
        '      <SOURCE MIDI',
        '        HASDATA 1 960 QN',
        '        CCINTERP 32',
        f'        POOLEDEVTS {pool_guid}',
    ]

    lines.extend(event_lines)

    lines.extend([
        '        CCINTERP 32',
        '        CHASE_CC_TAKEOFFS 1',
        f'        GUID {_guid(identity + ":source")}',
        f'        IGNTEMPO 0 {tempo:g} 4 4',
        '      >',
        '    >',
    ])

    return lines

# ---------------------------------------------------------------------------
# v0.7 native VST3 reconstruction
# ---------------------------------------------------------------------------

def _flip_hex_bytes(text: str):
    return "".join(text[i:i + 2] for i in range(len(text) - 2, -1, -2))


def _reaper_vst3_hash(device_id: str):
    """
    REAPER's numeric VST3 identifier is the FNV-1a hash of the VST3 class UUID
    after Steinberg's byte-order conversion.
    """
    parts = [
        device_id[:8],
        device_id[8:12],
        device_id[12:16],
        device_id[16:20],
        device_id[20:32],
    ]

    converted = uuid.UUID(
        f"{_flip_hex_bytes(parts[0])}-"
        f"{_flip_hex_bytes(parts[1])}-"
        f"{_flip_hex_bytes(parts[2])}-"
        f"{parts[3]}-{parts[4]}"
    )

    h = 0x811C9DC5
    for byte in converted.bytes:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF

    return h & 0x7FFFFFFF


def _nearest_printable_name(data: bytes, descriptor_pos: int):
    """
    Recover the human preset/program label immediately before PluginHost.

    Use whole printable strings rather than word fragments so class names such
    as NI::MASCHINE::DATA::PluginHost cannot accidentally produce "DATA".
    """
    start = max(0, descriptor_pos - 700)
    candidates = []

    for match in re.finditer(rb"[ -~]{3,}", data[start:descriptor_pos]):
        text = match.group().decode("ascii", errors="ignore").strip()

        if not text:
            continue
        if "::" in text:
            continue
        if text in {"VST3", "VST", "INSTR", "FX"}:
            continue
        if text.startswith("Native Instruments"):
            continue

        candidates.append((start + match.end(), text))

    return candidates[-1][1] if candidates else ""


def _find_sound_id_before_position(data: bytes, position: int, window=12000):
    """
    Resolve the owning Maschine Sound object by finding the nearest Sound-class
    record before a plug-in descriptor.
    """
    start = max(0, position - window)
    marker = enc_var(23)
    definition_tail = enc_var(1) + enc_var(9)
    best = None
    p = start

    while True:
        p = data.find(marker, p, position)
        if p < 0:
            break

        after = p + len(marker)
        variants = []

        # First Sound object class-definition form.
        if data[after:after + len(definition_tail)] == definition_tail:
            try:
                sound_id, q = read_var_at(
                    data,
                    after + len(definition_tail),
                )
                next_value, _ = read_var_at(data, q)
                if next_value == 2:
                    variants.append(sound_id)
            except Exception:
                pass

        # Normal Sound class-reference form.
        try:
            sound_id, q = read_var_at(data, after)
            next_value, _ = read_var_at(data, q)
            if next_value == 2:
                variants.append(sound_id)
        except Exception:
            pass

        for sound_id in variants:
            if not (100 <= sound_id < 10_000_000):
                continue

            distance = position - p
            if best is None or distance < best[0]:
                best = (distance, sound_id)

        p += 1

    return None if best is None else best[1]


def scan_embedded_vst3_instruments(data: bytes):
    """
    Locate external VST3 instruments and read the complete standard VST3 preset
    structure embedded by Maschine.

    The preset contains the exact plug-in component/controller state written by
    the plug-in itself.
    """
    pattern = re.compile(
        rb"([A-Za-z0-9][A-Za-z0-9 .&+_-]{1,60})"
        rb":VST3:(INSTR|FX):([0-9A-Fa-f]{32})"
    )

    results = []
    seen_preset_positions = set()

    for match in pattern.finditer(data):
        vendor = match.group(1).decode("ascii", errors="replace").strip()
        role = match.group(2).decode("ascii")
        device_id = match.group(3).decode("ascii").upper()

        # v0.7 exports instrument plug-ins only. FX support will be a separate
        # routing/FX-chain phase.
        if role != "INSTR":
            continue

        try:
            plugin_name, name_end = read_ni_string(data, match.end())
        except Exception:
            continue

        preset_signature = (
            b"VST3"
            + b"\x01\x00\x00\x00"
            + device_id.encode("ascii")
        )

        preset_pos = data.find(
            preset_signature,
            name_end,
            name_end + 512,
        )

        if preset_pos < 0 or preset_pos in seen_preset_positions:
            continue

        version = struct.unpack_from("<I", data, preset_pos + 4)[0]
        class_id = data[preset_pos + 8:preset_pos + 40].decode(
            "ascii",
            errors="replace",
        )
        list_offset = struct.unpack_from("<Q", data, preset_pos + 40)[0]
        list_pos = preset_pos + list_offset

        if data[list_pos:list_pos + 4] != b"List":
            continue

        num_chunks = struct.unpack_from("<I", data, list_pos + 4)[0]
        q = list_pos + 8
        chunks = []

        for _ in range(num_chunks):
            chunk_id = data[q:q + 4].decode("ascii", errors="replace")
            chunk_offset = struct.unpack_from("<Q", data, q + 4)[0]
            chunk_size = struct.unpack_from("<Q", data, q + 12)[0]

            chunk_start = preset_pos + chunk_offset
            chunk_end = chunk_start + chunk_size

            if chunk_end > len(data):
                raise RuntimeError(
                    f"Embedded VST3 state for {plugin_name} exceeds the MXPRJ."
                )

            chunks.append(
                (chunk_id, bytes(data[chunk_start:chunk_end]))
            )
            q += 20

        sound_id = _find_sound_id_before_position(
            data,
            match.start(),
        )

        preset_name = _nearest_printable_name(
            data,
            match.start(),
        )

        track_name = plugin_name
        if preset_name:
            track_name += f" - {preset_name}"

        results.append({
            "kind": "vst3",
            "vendor": vendor,
            "role": role,
            "device_id": class_id,
            "plugin_name": plugin_name,
            "preset_name": preset_name,
            "track_name": track_name,
            "preset_version": version,
            "preset_pos": preset_pos,
            "chunks": chunks,
            "sound_id": sound_id,
            "midi_items": [],
            "midi_note_count": 0,
        })

        seen_preset_positions.add(preset_pos)

    return results


def scan_embedded_vst3_fx_devices(data: bytes):
    """
    Diagnostic-only scan for external VST3 FX devices.

    v0.20.5 deliberately does NOT convert these devices; this function exists
    only so the completion dialog can tell the user when such devices were
    detected in the source project. It does not modify conversion behavior.
    """
    pattern = re.compile(
        rb"([A-Za-z0-9][A-Za-z0-9 .&+_-]{1,60})"
        rb":VST3:(INSTR|FX):([0-9A-Fa-f]{32})"
    )

    results = []
    seen_preset_positions = set()

    for match in pattern.finditer(data):
        role = match.group(2).decode("ascii", errors="replace")
        if role != "FX":
            continue

        vendor = match.group(1).decode("ascii", errors="replace").strip()
        device_id = match.group(3).decode("ascii", errors="replace").upper()

        try:
            plugin_name, name_end = read_ni_string(data, match.end())
        except Exception:
            plugin_name = vendor or "Unknown VST3 FX"
            name_end = match.end()

        preset_signature = (
            b"VST3"
            + b"\x01\x00\x00\x00"
            + device_id.encode("ascii")
        )
        preset_pos = data.find(preset_signature, name_end, name_end + 512)

        # Prefer preset-position de-duplication, matching the instrument scan.
        # If no nearby preset is found, retain one descriptor at this match so
        # the user still receives a conservative warning.
        dedupe_key = preset_pos if preset_pos >= 0 else ("descriptor", match.start())
        if dedupe_key in seen_preset_positions:
            continue
        seen_preset_positions.add(dedupe_key)

        results.append({
            "vendor": vendor,
            "role": role,
            "device_id": device_id,
            "plugin_name": plugin_name,
            "preset_pos": preset_pos if preset_pos >= 0 else None,
        })

    return results


def _build_not_attempted_report(mx: bytes, external_vst3_fx, routing_profile):
    """Return explicit scope exclusions for the completion dialog/report."""
    items = []

    if external_vst3_fx:
        names = []
        for fx in external_vst3_fx:
            name = str(fx.get("plugin_name") or "Unknown VST3 FX")
            if name not in names:
                names.append(name)
        shown = ", ".join(names[:8])
        if len(names) > 8:
            shown += f", +{len(names) - 8} more"
        items.append({
            "category": "External VST3 effects",
            "detected": True,
            "count": len(external_vst3_fx),
            "detail": (
                f"Detected {len(external_vst3_fx)} VST3 FX device(s): {shown}. "
                "Their insert/group/master FX instances and chains were not reconstructed."
            ),
        })
    else:
        items.append({
            "category": "External VST3 effects",
            "detected": False,
            "count": 0,
            "detail": "External VST3 insert/group/master FX chains are outside this build's conversion scope.",
        })

    perform_fx_detected = b"NI::MASCHINE::DATA::PerformanceFX" in mx
    items.extend([
        {
            "category": "Maschine internal instruments other than Sampler",
            "detected": None,
            "detail": "Drum Synth/Bass Synth and other Maschine-native instruments are not reconstructed.",
        },
        {
            "category": "Maschine internal effects",
            "detected": None,
            "detail": "Native Sound/Group/Master insert effects are not reconstructed as effect devices.",
        },
        {
            "category": "Perform FX",
            "detected": perform_fx_detected,
            "detail": (
                ("Perform FX data was detected; " if perform_fx_detected else "")
                + "the Perform FX device/touch-strip behavior is not reconstructed."
            ),
        },
        {
            "category": "Audio Clips / audio-loop arrangement content",
            "detected": None,
            "detail": "Audio Clips and their audio content are not imported as REAPER media items.",
        },
        {
            "category": "Clip automation",
            "detected": None,
            "detail": "Clip automation is not imported by this build.",
        },
        {
            "category": "Advanced Sampler slice/chop/multi-zone layouts",
            "detected": None,
            "detail": (
                "The stable Sampler path transfers the currently proven one-zone state fields; "
                "slice/chop and general multi-zone reconstruction are not attempted."
            ),
        },
        {
            "category": "General mixer / sends / sidechains / arbitrary routing",
            "detected": None,
            "detail": (
                "Mixer state and arbitrary routing are not comprehensively reconstructed. "
                + (
                    "The validated Maltodextrin2 routing profile was recognized for this project."
                    if routing_profile is not None
                    else "Only the converter's validated/default routing behavior is applied."
                )
            ),
        },
        {
            "category": "Maschine Host Automation assignments",
            "detected": None,
            "detail": "Maschine Host/Auto parameter assignments are not created or relied upon.",
        },
        {
            "category": "Non-VST3 plug-ins",
            "detected": None,
            "detail": "VST2 and other non-VST3 external instruments/effects are not reconstructed.",
        },
    ])

    return items


def _parse_regular_note_vector_v2(data: bytes, pos: int, list_class_id: int, note_class_id: int):
    """
    NoteEvent vectors may begin after tick zero. v0.5/v0.6 assumed the first
    event always began at zero; VST-instrument Patterns demonstrate that the
    fourth header value is actually the first event's start tick.
    """
    q = pos

    value, q = read_var_at(data, q)
    if value != list_class_id:
        raise RuntimeError("Not a NoteEvent-list record.")

    object_id, q = read_var_at(data, q)
    count, q = read_var_at(data, q)
    first_start_tick, q = read_var_at(data, q)
    class_ref, q = read_var_at(data, q)

    if (
        class_ref != note_class_id
        or not (1 <= count <= 4096)
        or not (0 <= first_start_tick <= 10_000_000)
    ):
        raise RuntimeError("Invalid NoteEvent-list header.")

    events = []
    start_tick = first_start_tick

    for index in range(count):
        for _ in range(3):
            _row, q = read_var_at(data, q)

        core = []
        for _ in range(5):
            value, q = read_var_at(data, q)
            core.append(value)

        event = {
            "start": start_tick,
            "flag": core[0],
            "length": core[1],
            "note": core[3],
            "velocity": core[4],
        }

        if not (
            0 < event["length"] <= 1_000_000
            and 0 <= event["note"] <= 127
            and 1 <= event["velocity"] <= 127
        ):
            raise RuntimeError("Implausible NoteEvent payload.")

        events.append(event)

        if index < count - 1:
            _outer_row, q = read_var_at(data, q)
            start_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)

            if class_ref != note_class_id:
                raise RuntimeError(
                    "Unexpected NoteEvent class reference."
                )

    return object_id, events


def extract_note_vectors_v2(data: bytes):
    located = _locate_note_event_class(data)
    if located is None:
        return None, None, {}

    _class_pos, note_class_id = located
    list_class_id = note_class_id - 5
    vectors = {}

    # First vector has Boost class-definition metadata. Newer Maschine
    # saves interleave modulation records and use a different initial-vector
    # header; fall back to the v3 scanner when the legacy parser rejects it.
    try:
        first_object_id, first_events = _parse_initial_note_vector(
            data,
            note_class_id,
        )
        vectors[first_object_id] = first_events
    except Exception:
        return extract_note_vectors_v3(data)

    marker = enc_var(list_class_id)
    pos = 0

    while True:
        p = data.find(marker, pos)
        if p < 0:
            break

        try:
            object_id, events = _parse_regular_note_vector_v2(
                data,
                p,
                list_class_id,
                note_class_id,
            )
            vectors.setdefault(object_id, events)
        except Exception:
            pass

        pos = p + 1

    return note_class_id, list_class_id, vectors


def extract_patterns_for_sound_ids_direct(data: bytes, sound_ids):
    """
    Decode Pattern Sound slots using the actual slot ordering:

        Sound reference -> slot object -> NoteEvent-list reference / empty

    This direct form is used for external instrument Sounds in v0.7.
    """
    _note_class, list_class_id, vectors = extract_note_vectors_v2(data)

    if not vectors:
        return [], {}

    wanted = {
        sid for sid in sound_ids
        if sid is not None
    }

    patterns = []

    for match in re.finditer(b"Pattern", data):
        pos = match.start()

        name_match = re.match(
            rb"Pattern[ -~]*",
            data[pos:pos + 96],
        )
        if not name_match:
            continue

        try:
            length_ticks = _parse_pattern_length(
                data,
                pos,
                name_match.group(),
            )
        except Exception:
            continue

        chunk = data[pos:pos + 330]
        slots = {}
        search_pos = 0
        sound_marker = enc_var(23)

        while True:
            p = chunk.find(sound_marker, search_pos)
            if p < 0:
                break

            try:
                sound_id, q = read_var_at(
                    chunk,
                    p + len(sound_marker),
                )

                if sound_id not in wanted:
                    search_pos = p + 1
                    continue

                slot_class, q = read_var_at(chunk, q)
                if slot_class != 155:
                    search_pos = p + 1
                    continue

                _slot_object_id, q = read_var_at(chunk, q)
                vector_id = 0

                if chunk[q:q + 2] == b"\xff\x01":
                    q += 2
                else:
                    class_ref, q2 = read_var_at(chunk, q)
                    if class_ref == list_class_id:
                        vector_id, q = read_var_at(chunk, q2)

                slots[sound_id] = vector_id

            except Exception:
                pass

            search_pos = p + 1

        if slots:
            patterns.append({
                "source_offset": pos,
                "name": name_match.group().decode(
                    "utf-8",
                    errors="replace",
                ),
                "length": length_ticks,
                "slots": slots,
            })

    return patterns, vectors


_VST3_CONNECTIONS = bytes([
    1, 0, 0, 0, 0, 0, 0, 0,
    2, 0, 0, 0, 0, 0, 0, 0,
])


def _make_reaper_vst3_state(instance):
    """
    Convert standard VST3 Comp/Cont chunks to REAPER's opaque VST3 state.
    """
    vst_data = bytearray()

    for index, (_chunk_id, chunk) in enumerate(instance["chunks"]):
        vst_data += struct.pack(
            "<II",
            len(chunk),
            0x01000000 if index == 0 else 0,
        )
        vst_data += chunk

    wrapper = bytearray()
    wrapper += struct.pack("<I", 0)
    wrapper += struct.pack("<I", 0xFEED5EEE)

    wrapper += struct.pack("<I", 2)
    wrapper += _VST3_CONNECTIONS

    wrapper += struct.pack("<I", 2)
    wrapper += _VST3_CONNECTIONS

    wrapper += struct.pack("<I", len(vst_data))
    wrapper += struct.pack("<I", 1)
    wrapper += struct.pack("<I", 0x100000)

    footer = b"\x00\x00\x10\x00\x00\x00"

    return bytes(wrapper), bytes(vst_data), footer


def _base64_rows(raw: bytes, indent="        ", width=128):
    encoded = base64.b64encode(raw).decode("ascii")
    return [
        indent + encoded[i:i + width]
        for i in range(0, len(encoded), width)
    ]


def _find_fxchain_bounds(track_lines):
    start = next(
        (
            i for i, line in enumerate(track_lines)
            if line.startswith("    <FXCHAIN")
        ),
        None,
    )

    if start is None:
        raise RuntimeError("Template track has no FXCHAIN.")

    end = start + 1
    while end < len(track_lines):
        if track_lines[end] == "    >":
            return start, end
        end += 1

    raise RuntimeError("Template FXCHAIN is not closed.")


def _clone_template_track_for_vst3(template_track_lines, instance, source_key, index):
    """
    Clone the template track shell, replace Komplete Kontrol with the native
    external VST3 and restore the exact VST3 state extracted from Maschine.
    """
    text = "\n".join(template_track_lines)

    track_match = re.search(
        r"\{[0-9A-Fa-f-]{36}\}",
        template_track_lines[0],
    )
    if not track_match:
        raise RuntimeError("Template track GUID not found.")

    old_track_guid = track_match.group(0)

    track_guid = _guid(
        f"maschine-vst3-track:{source_key}:"
        f"{instance['preset_pos']}:{instance['device_id']}"
    )

    text = text.replace(old_track_guid, track_guid)
    lines = text.splitlines()

    safe_name = instance["track_name"].replace('"', "'")
    for i, line in enumerate(lines):
        if line.startswith("    NAME "):
            lines[i] = f'    NAME "{safe_name}"'
            break

    for i, line in enumerate(lines):
        if line.strip().startswith("SEL "):
            indent = line[:len(line) - len(line.lstrip())]
            lines[i] = indent + "SEL 0"
            break

    fx_start, fx_end = _find_fxchain_bounds(lines)

    wrapper, vst_data, footer = _make_reaper_vst3_state(instance)
    reaper_id = _reaper_vst3_hash(instance["device_id"])

    role_prefix = "VST3i" if instance["role"] == "INSTR" else "VST3"
    display = (
        f'{role_prefix}: {instance["plugin_name"]} '
        f'({instance["vendor"]})'
    )

    vst_header = (
        f'      <VST "{display}" "" 0 "" '
        f'{reaper_id}'
        + "{"
        + instance["device_id"]
        + '}" ""'
    )
    fx_guid = _guid(
        f"maschine-vst3-fx:{source_key}:"
        f"{instance['preset_pos']}:{instance['device_id']}"
    )

    fx_lines = [
        "    <FXCHAIN",
        "      SHOW 0",
        "      LASTSEL 0",
        "      DOCKED 0",
        "      BYPASS 0 0 0",
        vst_header,
    ]

    fx_lines.extend(_base64_rows(wrapper))
    fx_lines.extend(_base64_rows(vst_data))
    fx_lines.extend(_base64_rows(footer))

    fx_lines.extend([
        "      >",
        "      MULTIFXLINK 0",
        '      PRESETNAME ""',
        "      FLOATPOS 0 0 0 0",
        f"      FXID {fx_guid}",
        "      WAK 0 0",
        "    >",
    ])

    return lines[:fx_start] + fx_lines + lines[fx_end + 1:]

# ---------------------------------------------------------------------------
# v0.9 Maschine Group -> REAPER folder mapping
# ---------------------------------------------------------------------------

def _group_slot_label(index: int):
    """
    Maschine Groups are arranged A-H in banks. The supplied project contains
    A1-H1, but this also gives sensible labels if later projects contain more.
    """
    letter = chr(ord("A") + (index % 8))
    bank = 1 + (index // 8)
    return f"Group {letter}{bank}"


def _group_ids_from_scenes(scenes):
    group_ids = set()
    for scene in scenes.values():
        group_ids.update(scene.get("slots", {}).keys())
    return sorted(group_ids)


def _group_for_sound(sound_id, group_ids):
    """
    Maschine serializes each Group object before the Sound objects belonging
    to it. Object IDs therefore form monotonic Group ranges in the projects
    tested so far:
        Group ID < its Sound IDs < next Group ID.
    """
    if sound_id is None or not group_ids:
        return None

    selected = None
    for group_id in group_ids:
        if group_id < sound_id:
            selected = group_id
        else:
            break
    return selected


def _discover_first_group_name(data: bytes):
    """
    The first loaded Group preset in the supplied project exposes its display
    name immediately before the one-time PerformanceFX class definition.
    Later Groups in this project use their default Group slot names.

    This deliberately falls back to the Group slot label if no custom name can
    be recovered rather than guessing from instrument preset names.
    """
    marker = b"NI::MASCHINE::DATA::PerformanceFX"
    p = data.find(marker)
    if p < 0:
        return ""

    window_start = max(0, p - 256)
    window = data[window_start:p]
    candidates = []

    for match in re.finditer(rb"[A-Za-z0-9][A-Za-z0-9 ._+&()'-]{2,60}", window):
        text = match.group().decode("ascii", errors="ignore").strip()
        if not text:
            continue
        if text in {"Default", "None"}:
            continue
        candidates.append(text)

    return candidates[-1] if candidates else ""


def _set_track_muted(track_lines, muted: bool):
    target = "1" if muted else "0"
    result = list(track_lines)

    for i, line in enumerate(result):
        if line.startswith("    MUTESOLO "):
            parts = line.split()
            if len(parts) >= 4:
                parts[1] = target
                result[i] = "    " + " ".join(parts)
            else:
                result[i] = f"    MUTESOLO {target} 0 0"
            return result

    raise RuntimeError("Generated track has no MUTESOLO line.")


def _set_track_folder_end(track_lines, is_last_child: bool):
    result = list(track_lines)
    replacement = "    ISBUS 2 -1" if is_last_child else "    ISBUS 0 0"

    for i, line in enumerate(result):
        if line.startswith("    ISBUS "):
            result[i] = replacement
            return result

    raise RuntimeError("Generated track has no ISBUS line.")


def _set_first_fx_bypass(track_lines, bypassed: bool):
    """
    REAPER stores the plug-in power/bypass state in the BYPASS line directly
    before the VST chunk. 1 = bypassed, 0 = active.
    """
    result = list(track_lines)
    value = "1" if bypassed else "0"

    fxchain_seen = False
    for i, line in enumerate(result):
        if line.startswith("    <FXCHAIN"):
            fxchain_seen = True
            continue
        if fxchain_seen and line.startswith("      BYPASS "):
            parts = line.split()
            if len(parts) >= 4:
                parts[1] = value
                result[i] = "      " + " ".join(parts)
            else:
                result[i] = f"      BYPASS {value} 0 0"
            return result

    raise RuntimeError("Generated instrument track has no FX BYPASS line.")


def _build_group_folder_track(folder_name: str, source_key: str, group_id: int):
    """
    Create a clean REAPER folder-parent track. Children keep their normal
    MAINSEND setting, so REAPER naturally routes them through the folder.
    """
    guid = _guid(f"maschine-group-folder:{source_key}:{group_id}")
    safe_name = folder_name.replace('"', "'")

    return [
        f"  <TRACK {guid}",
        f'    NAME "{safe_name}"',
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 0",
        "    PANLAWFLAGS 3",
        "    VOLPAN 1 0 -1 -1 1",
        "    MUTESOLO 0 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 1 1",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    SEL 0",
        "    REC 0 0 0 0 0 0 0 0",
        "    VU 2",
        "    TRACKHEIGHT 0 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 0",
        f"    TRACKID {guid}",
        "    PERF 0",
        "    MIDIOUT -1 -1",
        "    MAINSEND 1 0",
        "  >",
    ]


# ---------------------------------------------------------------------------
# v0.18.4 Maschine automation: decode, resolve owner Group, route to that track
# ---------------------------------------------------------------------------

FLOAT_MOD_EVENT_CLASS = b"NI::MASCHINE::DATA::FloatModulationEvent"
BOOL_MOD_EVENT_CLASS = b"NI::MASCHINE::DATA::BoolModulationEvent"
AUTOMATION_CARRIER_NAME = "Maschine_Automation_Carrier.jsfx"
AUTOMATION_CARRIER_MAX_LANES = 64


def _locate_serialized_class_id(data: bytes, class_name: bytes):
    """Return the Boost/NI serialization class id immediately preceding a class name."""
    p = data.find(class_name)
    if p < 0:
        return None

    candidates = []
    for s in range(max(0, p - 12), p):
        try:
            class_id, q = read_var_at(data, s)
            text_len, q = read_var_at(data, q)
            if q == p and text_len == len(class_name):
                candidates.append(class_id)
        except Exception:
            pass

    return candidates[-1] if candidates else None


def _infer_named_object_class_and_id(data: bytes, name_pos: int, class_range=range(96, 224)):
    """
    Locate a serialized object immediately before a visible object name.

    Maschine serialization ids shift between project versions. Rather than
    hard-code one Pattern/Scene class number, try the small metadata layouts
    observed in supplied projects. Restricting the class range avoids treating
    ordinary field ids as object classes.
    """
    metadata_families = (
        (1, 5),
        (1, 2),
        (1, 1),
        (1, 3),
    )

    for class_id in class_range:
        for metadata in metadata_families:
            try:
                object_id = _serialized_object_id_before_name(
                    data,
                    name_pos,
                    class_id,
                    metadata,
                )
            except Exception:
                object_id = None

            if object_id not in (None, 0, 1):
                return class_id, object_id

    return None, None


def _parse_modulation_payload(data: bytes, class_ref_pos: int, end_pos: int, kind: str):
    """
    Decode one Float/Bool modulation-event payload.

    Both event types contain a target parameter object id and two serialized
    float records. The second float is the recorded modulation value.
    """
    field_code = 13 if kind == "float" else 25
    field_marker = enc_var(field_code)

    search_end = min(end_pos, class_ref_pos + 180)
    field_pos = data.find(field_marker, class_ref_pos, search_end)
    if field_pos < 0:
        return None

    try:
        target_id, after_target = read_var_at(
            data,
            field_pos + len(field_marker),
        )
    except Exception:
        return None

    float_markers = []
    p = after_target
    while len(float_markers) < 2:
        z = data.find(b"\x01\x04", p, min(search_end, after_target + 80))
        if z < 0:
            break
        if z + 6 <= end_pos:
            float_markers.append(z)
        p = z + 2

    if len(float_markers) < 2:
        return None

    value_pos = float_markers[1] + 2
    if value_pos + 4 > end_pos:
        return None

    value = struct.unpack_from("<f", data, value_pos)[0]

    if not (-16.0 <= value <= 16.0):
        return None

    return {
        "target_id": target_id,
        "value": float(value),
        "kind": kind,
    }


def _extract_pattern_modulation_events(data: bytes, start: int, end: int, length_ticks: int):
    """Decode FloatModulationEvent and BoolModulationEvent records in one Pattern."""
    float_class = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    bool_class = _locate_serialized_class_id(data, BOOL_MOD_EVENT_CLASS)

    class_kind = {}
    if float_class is not None:
        class_kind[float_class] = "float"
    if bool_class is not None:
        class_kind[bool_class] = "bool"

    if not class_kind:
        return []

    events = []
    p = start
    while p < end:
        try:
            tick, q = read_var_at(data, p)
            if tick < 0 or tick > length_ticks:
                p += 1
                continue

            class_id, q2 = read_var_at(data, q)
            kind = class_kind.get(class_id)
            if kind is None:
                p += 1
                continue

            payload = _parse_modulation_payload(data, q, end, kind)
            if payload:
                payload["tick"] = int(tick)
                events.append(payload)

        except Exception:
            pass

        p += 1

    unique = {}
    for event in events:
        key = (
            int(event["tick"]),
            event["kind"],
            int(event["target_id"]),
            round(float(event["value"]), 7),
        )
        unique[key] = event

    return sorted(
        unique.values(),
        key=lambda item: (
            item["tick"],
            item["target_id"],
            item["kind"],
        ),
    )


def _find_named_patterns(data: bytes):
    """Return all named Pattern objects, including projects whose automation vector is serialized globally."""
    matches = _find_named_patterns(data)
    return matches


def _decode_global_float_modulation_vector(data: bytes):
    """
    Decode the alternate Maschine FloatModulationEvent layout used by VST
    parameter automation. In these saves the event vector is serialized in the
    global object graph rather than physically inside the Pattern block.

    The class-definition event is implicit at tick zero; following records use
    [event-object-id, tick, FloatModulationEvent-class, ...]. The stable target
    field observed in this family is field 70.
    """
    class_id = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    if class_id is None:
        return []
    class_name_pos = data.find(FLOAT_MOD_EVENT_CLASS)
    if class_name_pos < 0:
        return []
    class_marker = enc_var(class_id)
    target_field = enc_var(70)
    events = []

    # First class-defining event follows the class name. Its tick is zero.
    first_search = class_name_pos + len(FLOAT_MOD_EVENT_CLASS)
    first_target = data.find(target_field, first_search, first_search + 96)
    if first_target >= 0:
        try:
            target_id, q = read_var_at(data, first_target + len(target_field))
            floats = []
            p = q
            while len(floats) < 2:
                z = data.find(b"\x01\x04", p, min(len(data), q + 72))
                if z < 0:
                    break
                floats.append(struct.unpack_from("<f", data, z + 2)[0])
                p = z + 6
            if len(floats) >= 2:
                events.append({"tick": 0, "target_id": int(target_id), "value": float(floats[1]), "kind": "float", "storage": "global_vst"})
        except Exception:
            pass

    # Subsequent event class references. Restrict to the contiguous vector
    # immediately following the class definition; unrelated uses of the same
    # numeric class id elsewhere in the file are ignored by structural checks.
    p = first_search
    last_good = first_search
    while True:
        ref = data.find(class_marker, p)
        if ref < 0:
            break
        p = ref + len(class_marker)
        if ref <= class_name_pos + len(FLOAT_MOD_EVENT_CLASS):
            continue
        if ref - last_good > 256 and events:
            # The modulation vector records are tightly packed (~56 bytes each).
            break

        tick = None
        for start in range(max(first_search, ref - 12), ref):
            try:
                _object_id, q1 = read_var_at(data, start)
                candidate_tick, q2 = read_var_at(data, q1)
                if q2 == ref and 0 <= candidate_tick <= 10_000_000:
                    tick = int(candidate_tick)
            except Exception:
                pass
        if tick is None:
            continue

        field_pos = data.find(target_field, ref, min(len(data), ref + 28))
        if field_pos < 0:
            continue
        try:
            target_id, q = read_var_at(data, field_pos + len(target_field))
            floats = []
            fp = q
            while len(floats) < 2:
                z = data.find(b"\x01\x04", fp, min(len(data), ref + 56))
                if z < 0:
                    break
                floats.append(struct.unpack_from("<f", data, z + 2)[0])
                fp = z + 6
            if len(floats) < 2:
                continue
            value = float(floats[1])
            if not (-16.0 <= value <= 16.0):
                continue
            events.append({"tick": tick, "target_id": int(target_id), "value": value, "kind": "float", "storage": "global_vst"})
            last_good = ref
        except Exception:
            continue

    unique = {}
    for event in events:
        key = (event["tick"], event["kind"], event["target_id"], round(event["value"], 7))
        unique[key] = event
    return sorted(unique.values(), key=lambda e: (e["tick"], e["target_id"]))


def extract_pattern_automation(data: bytes):
    """
    Return every named Maschine Pattern containing modulation automation.

    This parser remains independent of instrument/Sampler decoding so Perform
    FX automation survives even when the Group contains unsupported devices.
    """
    matches = []
    for match in re.finditer(b"Pattern", data):
        pos = match.start()
        name_match = re.match(rb"Pattern[ -~]*", data[pos:pos + 96])
        if not name_match:
            continue
        try:
            length_ticks = _parse_pattern_length(data, pos, name_match.group())
        except Exception:
            continue
        class_id, pattern_id = _infer_named_object_class_and_id(data, pos)
        matches.append({
            "source_offset": pos,
            "name": name_match.group().decode("utf-8", errors="replace"),
            "length": int(length_ticks),
            "pattern_class": class_id,
            "pattern_id": pattern_id,
        })

    out = []
    for index, pattern in enumerate(matches):
        start = pattern["source_offset"]
        candidates = [len(data)]
        if index + 1 < len(matches):
            candidates.append(matches[index + 1]["source_offset"])
        scene_pos = data.find(b"Scene ", start + 1)
        if scene_pos >= 0:
            candidates.append(scene_pos)
        end = min(candidates)

        events = _extract_pattern_modulation_events(
            data,
            start,
            end,
            pattern["length"],
        )
        if not events:
            continue

        pattern = dict(pattern)
        pattern["automation_events"] = events
        out.append(pattern)

    # VST parameter automation uses an alternate global vector layout in some
    # Maschine saves. If no Pattern-local vector was found and there is one
    # unambiguous named Pattern, attach that vector to it. This is deliberately
    # conservative; multi-Pattern global-vector ownership will be decoded when
    # encountered in a test project.
    if not out:
        global_events = _decode_global_float_modulation_vector(data)
        if global_events and len(matches) == 1:
            pattern = dict(matches[0])
            pattern["automation_events"] = [
                event for event in global_events
                if int(event["tick"]) <= int(pattern["length"])
            ]
            if pattern["automation_events"]:
                out.append(pattern)

    return out


def extract_automation_scene_map(data: bytes, pattern_automation=None):
    """
    Decode Scene -> Group -> Pattern ownership without hard-coded Scene ids.

    The older converter had two known serialization families. The Perform-FX
    diagnostic project uses a third family (Pattern class 121, Scene class 150,
    slot class 155). This decoder infers the Pattern and Scene classes from the
    named objects and only relies on the stable Group reference class (22).

    This is deliberately used for automation ownership even when the main MIDI
    arranger decoder already succeeded, because it preserves all Group slots
    including Groups whose devices are unsupported by the converter.
    """
    pattern_classes = {
        int(item["pattern_class"])
        for item in (pattern_automation or [])
        if item.get("pattern_class") is not None
    }

    # If no automated Pattern supplied a class id, infer classes from all named
    # Patterns so Scene parsing can still establish the complete Group order.
    if not pattern_classes:
        for match in re.finditer(b"Pattern", data):
            pos = match.start()
            name_match = re.match(rb"Pattern[ -~]*", data[pos:pos + 96])
            if not name_match:
                continue
            class_id, object_id = _infer_named_object_class_and_id(data, pos)
            if class_id is not None and object_id is not None:
                pattern_classes.add(int(class_id))

    if not pattern_classes:
        return {}

    matches = []
    for match in re.finditer(b"Scene ", data):
        pos = match.start()
        name_match = re.match(rb"Scene [ -~]*", data[pos:pos + 96])
        if name_match:
            matches.append((
                pos,
                name_match.group().decode("utf-8", errors="replace"),
            ))

    scenes = {}
    group_marker = enc_var(22)

    for index, (pos, name) in enumerate(matches):
        scene_class, scene_id = _infer_named_object_class_and_id(data, pos)
        if scene_id is None:
            continue

        end = (
            matches[index + 1][0]
            if index + 1 < len(matches)
            else min(len(data), pos + 1400)
        )
        chunk = data[pos + len(name.encode("utf-8")):end]
        slots = {}
        search_pos = 0

        while True:
            p = chunk.find(group_marker, search_pos)
            if p < 0:
                break

            try:
                group_id, q = read_var_at(chunk, p + len(group_marker))

                # First Group slot in a Scene carries a short one-time prefix.
                if chunk[q:q + 3] == b"\x00\x01\x01":
                    q += 3
                elif chunk[q:q + 1] == b"\x00":
                    q += 1

                slot_class, q = read_var_at(chunk, q)
                if not (32 <= slot_class <= 512):
                    search_pos = p + 1
                    continue

                # The first instance of the slot class is class-defining and
                # contains metadata [1,2] before its object id. Subsequent
                # instances contain the object id directly.
                first, q1 = read_var_at(chunk, q)
                if first == 1:
                    second, q2 = read_var_at(chunk, q1)
                    if second in (0, 1, 2, 3, 5):
                        _slot_object_id, q = read_var_at(chunk, q2)
                    else:
                        _slot_object_id, q = first, q1
                else:
                    _slot_object_id, q = first, q1

                pattern_id = 0
                if chunk[q:q + 2] == b"\xff\x01":
                    q += 2
                else:
                    pattern_class, q2 = read_var_at(chunk, q)
                    if pattern_class not in pattern_classes:
                        search_pos = p + 1
                        continue
                    pattern_id, q = read_var_at(chunk, q2)

                if 0 < group_id < 10_000_000:
                    slots[int(group_id)] = int(pattern_id)

            except Exception:
                pass

            search_pos = p + 1

        if slots:
            scenes[int(scene_id)] = {
                "scene_id": int(scene_id),
                "scene_class": scene_class,
                "name": name,
                "slots": slots,
                "source_offset": pos,
            }

    return scenes


def _automation_pattern_group_map(pattern_automation, scenes):
    """Map each automated Pattern object id to the Maschine Group(s) using it."""
    automated_ids = {
        int(item["pattern_id"])
        for item in pattern_automation
        if item.get("pattern_id") is not None
    }
    owners = {pattern_id: set() for pattern_id in automated_ids}

    for scene in scenes.values():
        for group_id, pattern_id in scene.get("slots", {}).items():
            if pattern_id in owners:
                owners[pattern_id].add(int(group_id))

    return {
        pattern_id: sorted(group_ids)
        for pattern_id, group_ids in owners.items()
    }


def _find_parameter_definition(data: bytes, target_id: int, kind: str):
    """Resolve a modulation target back to its serialized NI parameter object."""
    marker = enc_var(int(target_id))
    search_pos = 0
    candidates = []

    while True:
        p = data.find(marker, search_pos)
        if p < 0:
            break
        search_pos = p + 1
        try:
            q = p
            ids = []
            for _ in range(5):
                value, q = read_var_at(data, q)
                ids.append(value)
            sequential = all(ids[i] == target_id + i for i in range(len(ids)))
            if not sequential:
                continue
            if data[q:q + 1] != b"\x00":
                continue
            q += 1
            state = {"definition_offset": p, "target_id": int(target_id), "kind": kind}
            if kind == "float" and q + 8 <= len(data):
                base_a, base_b = struct.unpack_from("<ff", data, q)
                if -1.0e9 < base_a < 1.0e9 and -1.0e9 < base_b < 1.0e9:
                    state["base_value"] = float(base_a)
                    state["stored_value"] = float(base_b)

            # v0.19.3: Maschine's hosted VST wrapper stores the plug-in's
            # native VST3 parameter ID close to the parameter value record, but
            # NI plug-ins do not all serialize the following label metadata in
            # exactly the same shape. Reaktor/Razor uses:
            #     native ID -> label length -> label
            # while Massive uses:
            #     native ID -> wrapper field -> label length -> label
            # Search the small metadata window and allow up to two short wrapper
            # varints between the ID and the printable control label. The chosen
            # candidate is scored rather than tied to a plug-in name or target ID.
            scan_start = q + 12
            scan_end = min(len(data), q + 40)
            identity_candidates = []
            for candidate_pos in range(scan_start, scan_end):
                try:
                    native_param_id, after_id = read_var_at(data, candidate_pos)
                except Exception:
                    continue
                if not (0 <= int(native_param_id) <= 0xFFFFFFFF):
                    continue
                cursor = after_id
                for wrapper_fields in range(3):
                    try:
                        label_len, label_pos = read_var_at(data, cursor)
                    except Exception:
                        break
                    if 1 <= int(label_len) <= 64:
                        label_end = label_pos + int(label_len)
                        if label_end <= len(data):
                            label_bytes = data[label_pos:label_end]
                            if label_bytes and all(0x20 <= b <= 0x7E for b in label_bytes):
                                label = label_bytes.decode("ascii", errors="ignore").strip()
                                if label and re.search(r"[A-Za-z]", label):
                                    # NI wrapper page/category headings can sit in
                                    # exactly the same compact metadata window as a
                                    # real control label.  They are not native VST3
                                    # parameter identities.  Treating e.g.
                                    # "Macros" or "EQ / Master" as the label for
                                    # native ID 0 can collapse unrelated lanes onto
                                    # the same host parameter.
                                    if label.strip().lower() in {
                                        "macros", "layers", "master", "eq / master"
                                    }:
                                        continue
                                    score = 0
                                    if re.match(r"(?i)^knob\s*\d+$", label):
                                        score += 6
                                    elif re.match(r"(?i)^(macro|slider|button)\s*\d+$", label):
                                        score += 5
                                    elif len(label) <= 24:
                                        score += 2
                                    if wrapper_fields == 0:
                                        score += 2
                                    elif wrapper_fields == 1:
                                        score += 1
                                    if 0 <= int(native_param_id) <= 65535:
                                        score += 1
                                    identity_candidates.append((
                                        score, -wrapper_fields, -candidate_pos,
                                        int(native_param_id), label, wrapper_fields
                                    ))
                    try:
                        wrapper_value, next_cursor = read_var_at(data, cursor)
                    except Exception:
                        break
                    # Wrapper metadata around these records is compact. Avoid
                    # skipping arbitrary large values that could make a later
                    # ASCII run look like a false parameter identity.
                    if not (0 <= int(wrapper_value) <= 255):
                        break
                    cursor = next_cursor
            if identity_candidates:
                identity_candidates.sort(reverse=True)
                _score, _neg_wrappers, _neg_pos, native_param_id, label, wrappers = identity_candidates[0]
                state["vst3_parameter_id"] = native_param_id
                state["serialized_control_label"] = label
                state["vst3_identity_wrapper_fields"] = wrappers
            candidates.append(state)
        except Exception:
            continue

    if not candidates:
        return None
    return min(candidates, key=lambda item: item["definition_offset"])

def resolve_automation_targets(data: bytes, pattern_automation):
    """Return metadata for every unique Maschine automation target."""
    targets = sorted({
        (event["kind"], int(event["target_id"]))
        for pattern in pattern_automation
        for event in pattern.get("automation_events", [])
    }, key=lambda item: (item[1], item[0]))

    performancefx_pos = data.find(b"NI::MASCHINE::DATA::PerformanceFX")
    first_pattern_pos = (
        data.find(b"Pattern", performancefx_pos + 1)
        if performancefx_pos >= 0
        else -1
    )

    resolved = {}
    for kind, target_id in targets:
        state = _find_parameter_definition(data, target_id, kind) or {
            "target_id": target_id,
            "kind": kind,
        }

        definition_offset = state.get("definition_offset")
        owner = "Unknown"
        name = f"NI {kind.upper()} target {target_id} (0x{target_id:04X})"

        if (
            performancefx_pos >= 0
            and definition_offset is not None
            and definition_offset > performancefx_pos
            and (first_pattern_pos < 0 or definition_offset < first_pattern_pos)
        ):
            owner = "PerformanceFX"
            distance = definition_offset - performancefx_pos
            if kind == "float" and distance < 180:
                name = "Perform FX Touchstrip"
                state["role"] = "touchstrip"
            elif kind == "bool" and distance < 220:
                name = "Perform FX Engage"
                state["role"] = "engage"
            else:
                name = f"Perform FX target {target_id} (0x{target_id:04X})"

        # VST/Sound automation is serialized in a global modulation vector in
        # the supplied Massive test. Resolve its owner from the parameter
        # definition's nearest Maschine Sound object.
        is_global_vst = any(
            event.get("storage") == "global_vst"
            and event["kind"] == kind
            and int(event["target_id"]) == target_id
            for pattern in pattern_automation
            for event in pattern.get("automation_events", [])
        )
        if is_global_vst:
            owner = "SoundPlugin"
            definition_offset = state.get("definition_offset")
            if definition_offset is not None:
                sound_id = _find_sound_id_before_position(data, definition_offset, 30000)
                if sound_id is not None:
                    state["sound_id"] = int(sound_id)
            # VST/Sound automation is not always stored in the same value mode.
            # Massive's tested cutoff lane contains absolute 0..1 values, while
            # the controlled Razor lane contains signed offsets around its 0.5
            # base value. A negative or >1 event proves that the lane cannot be
            # absolute normalized automation, so reconstruct base + modulation.
            lane_values = [
                float(event.get("value", 0.0))
                for pattern in pattern_automation
                for event in pattern.get("automation_events", [])
                if event.get("kind") == kind
                and int(event.get("target_id", -1)) == target_id
            ]
            if lane_values:
                state["lane_value_min"] = float(min(lane_values))
                state["lane_value_max"] = float(max(lane_values))
            if kind == "float" and lane_values and (
                min(lane_values) < -1.0e-7 or max(lane_values) > 1.0000001
            ):
                state["value_mode"] = "offset"
            else:
                state["value_mode"] = "absolute"

            # Native Instruments plug-in parameter pages are stored immediately
            # before these wrapper parameters. Resolve the common numbered-page
            # case (e.g. Massive Macros 1..8) without depending on the VST DSP.
            if definition_offset is not None:
                page_start = max(0, definition_offset - 160)
                page_chunk = data[page_start:definition_offset]
                printable = list(re.finditer(rb"[A-Za-z][A-Za-z0-9 _-]{2,30}", page_chunk))
                if printable:
                    page = printable[-1].group().decode("ascii", errors="ignore").strip()
                    # Five serialized object ids are allocated per parameter in
                    # this wrapper family, so the index is stable within a page.
                    page_pos = page_start + printable[-1].end()
                    first_field = data.find(enc_var(70), page_pos, definition_offset + 1)
                    if first_field >= 0:
                        try:
                            first_target, _ = read_var_at(data, first_field + len(enc_var(70)))
                            index = ((target_id - int(first_target)) // 5) + 2
                            if 1 <= index <= 64:
                                state["page"] = page
                                state["parameter_index"] = index
                                name = f"{page} {index}"
                        except Exception:
                            pass

            # Recover the NI/NKS page-control label serialized after the
            # parameter record. This is metadata such as "Knob 1", not a REAPER
            # host parameter name, but it is stable enough to identify validated
            # page/control mappings.
            if definition_offset is not None:
                label_chunk = data[definition_offset + 20:definition_offset + 80]
                labels = list(re.finditer(rb"[A-Za-z][A-Za-z0-9 /_-]{2,24}", label_chunk))
                if labels:
                    control_label = labels[0].group().decode("ascii", errors="ignore").strip()
                    state["control_label"] = control_label

            # v0.19: direct routing is no longer hard-coded by Razor target ID.
            # _find_parameter_definition() captures the native VST3 parameter ID
            # from the MXPRJ wrapper. A plug-in identity probe later translates
            # that stable ID to the REAPER host parameter index for the restored
            # VST3 instance. If no matching probe is available, the existing
            # generic carrier fallback remains in use.

            if name.startswith("NI FLOAT") or name.startswith("NI "):
                name = f"Sound plug-in target {target_id} (0x{target_id:04X})"

        state["owner"] = owner
        state["name"] = name
        resolved[(kind, target_id)] = state

    return resolved


def _maschine_mod_value_to_unit(value: float, kind: str, target_meta=None):
    """Convert a Maschine modulation event to a REAPER 0..1 envelope value."""
    if kind == "bool":
        return 1.0 if value > 0.0 else 0.0

    if target_meta is not None:
        if target_meta.get("value_mode") == "absolute":
            return max(0.0, min(1.0, float(value)))
        if target_meta.get("value_mode") == "carrier_centered":
            return max(0.0, min(1.0, (float(value) + 1.0) * 0.5))
        base = target_meta.get("base_value")
        if isinstance(base, (int, float)) and -0.001 <= float(base) <= 1.001:
            return max(0.0, min(1.0, float(base) + float(value)))

    return max(0.0, min(1.0, (float(value) + 1.0) * 0.5))


def _automation_patterns_by_id(pattern_automation):
    return {
        item["pattern_id"]: item
        for item in pattern_automation
        if item.get("pattern_id") is not None
    }


def _expand_pattern_automation_to_arrangement(
    pattern_automation,
    scenes,
    sections,
    tempo,
    pattern_group_map=None,
):
    """
    Expand Pattern-local automation through Arranger Sections while preserving
    the owning Maschine Group id on every event.
    """
    by_id = _automation_patterns_by_id(pattern_automation)
    pattern_group_map = pattern_group_map or {}
    expanded = []

    if by_id and scenes and sections:
        timeline_tick = 0
        for section in sections:
            scene = scenes.get(section.get("scene_id"))
            section_length = int(section.get("length", 0))

            if scene is not None:
                for group_id, pattern_id in scene.get("slots", {}).items():
                    if not pattern_id:
                        continue
                    pattern = by_id.get(pattern_id)
                    if pattern is None:
                        continue

                    pattern_length = max(1, int(pattern["length"]))
                    repeat_offset = 0
                    while repeat_offset < section_length:
                        for event in pattern["automation_events"]:
                            local_tick = repeat_offset + int(event["tick"])
                            if local_tick >= section_length:
                                continue
                            absolute_tick = timeline_tick + local_tick
                            expanded.append({
                                **event,
                                "pattern_id": pattern_id,
                                "pattern_name": pattern["name"],
                                "group_id": int(group_id),
                                "local_tick": int(event["tick"]),
                                "absolute_tick": absolute_tick,
                                "time": _ticks_to_seconds(absolute_tick, tempo),
                                "automation_expansion": "pattern_occurrence",                            })
                        repeat_offset += pattern_length

            timeline_tick += section_length

    if expanded:
        return expanded

    # Pattern-only fallback. Use Scene references solely to recover ownership.
    timeline_tick = 0
    for pattern in pattern_automation:
        pattern_id = pattern.get("pattern_id")
        owners = list(pattern_group_map.get(pattern_id, []))
        if not owners:
            owners = [None]

        for owner_group_id in owners:
            for event in pattern["automation_events"]:
                absolute_tick = timeline_tick + int(event["tick"])
                expanded.append({
                    **event,
                    "pattern_id": pattern_id,
                    "pattern_name": pattern["name"],
                    "group_id": owner_group_id,
                    "local_tick": int(event["tick"]),
                    "absolute_tick": absolute_tick,
                    "time": _ticks_to_seconds(absolute_tick, tempo),
                    "automation_expansion": "pattern_occurrence",
                })
        timeline_tick += int(pattern["length"])

    return expanded



def _expand_global_vst_automation_to_pattern_occurrences(
    global_events,
    target_metadata,
    scenes,
    sections,
    patterns_by_id,
    tempo,
):
    """
    Recover pattern-local automation from the global FloatModulationEvent
    vectors used by the 124/155/166 serialization family, then instantiate the
    curve at every Arranger occurrence of that Pattern.

    Some Maschine saves serialize one or more already-expanded copies of a
    Pattern's automation in the global event pool. Those copies are *evidence*
    for the Pattern-local curve, not the complete song automation. Treating the
    serialized ticks as authoritative song time therefore leaves holes whenever
    the same Pattern is reused in another Scene.

    The MIDI arranger already knows the authoritative Pattern occurrences. This
    function projects each raw event back into the local Pattern that occupied
    its serialized tick, deduplicates identical local copies, and expands that
    canonical curve across every occurrence of the same Pattern for the owning
    Group.
    """
    if not global_events or not scenes or not sections or not patterns_by_id:
        return []

    group_ids = sorted(_group_ids_from_scenes(scenes))
    occurrences = []
    timeline_tick = 0

    for section_index, section in enumerate(sections):
        section_length = max(0, int(section.get("length", 0)))
        scene = scenes.get(section.get("scene_id"))
        if scene is not None and section_length > 0:
            for group_id, pattern_id in scene.get("slots", {}).items():
                if not pattern_id:
                    continue
                pattern = patterns_by_id.get(pattern_id)
                if pattern is None:
                    continue
                pattern_length = max(1, int(pattern.get("length", section_length or 1)))

                repeat_offset = 0
                repeat_index = 0
                while repeat_offset < section_length:
                    occurrence_length = min(pattern_length, section_length - repeat_offset)
                    occurrences.append({
                        "section_index": int(section_index),
                        "scene_id": int(section.get("scene_id", 0)),
                        "group_id": int(group_id),
                        "pattern_id": int(pattern_id),
                        "pattern_name": pattern.get("name", f"Pattern {pattern_id}"),
                        "pattern_length": int(pattern_length),
                        "start_tick": int(timeline_tick + repeat_offset),
                        "length": int(occurrence_length),
                        "repeat_index": int(repeat_index),
                    })
                    repeat_offset += pattern_length
                    repeat_index += 1
        timeline_tick += section_length

    occurrences_by_group = {}
    for occurrence in occurrences:
        occurrences_by_group.setdefault(occurrence["group_id"], []).append(occurrence)

    raw_by_target = {}
    for event in global_events:
        key = (event["kind"], int(event["target_id"]))
        raw_by_target.setdefault(key, []).append(event)

    expanded = []
    consumed_source_offsets = set()

    for key, lane_events in raw_by_target.items():
        meta = target_metadata.get(key, {})
        sound_id = meta.get("sound_id")
        group_id = _group_for_sound(sound_id, group_ids) if sound_id is not None else None
        group_occurrences = occurrences_by_group.get(group_id, []) if group_id is not None else []

        # Recover one canonical local curve per Pattern id from whichever
        # serialized occurrence(s) happen to contain the raw global events.
        canonical_by_pattern = {}
        for event in lane_events:
            tick = int(event["tick"])
            matched = None
            for occurrence in group_occurrences:
                start = occurrence["start_tick"]
                end = start + occurrence["length"]
                if start <= tick < end:
                    matched = occurrence
                    break
            if matched is None:
                continue

            local_tick = tick - matched["start_tick"]
            if not (0 <= local_tick < matched["pattern_length"]):
                continue

            pattern_id = matched["pattern_id"]
            local_event = {
                **event,
                "local_tick": int(local_tick),
                "source_occurrence_start": int(matched["start_tick"]),
                "source_scene_id": int(matched["scene_id"]),
            }
            canonical_by_pattern.setdefault(pattern_id, []).append(local_event)
            consumed_source_offsets.add(event.get("source_offset"))

        for pattern_id, candidates in canonical_by_pattern.items():
            # The same Pattern may have been serialized in more than one Scene.
            # Exact local duplicates are therefore expected. If two copies
            # disagree at the same local tick, preserve the earliest serialized
            # copy as the canonical Pattern state.
            candidates.sort(key=lambda e: (
                int(e["local_tick"]),
                int(e.get("source_occurrence_start", 0)),
                int(e.get("source_offset", 0)),
            ))
            canonical = {}
            for event in candidates:
                local_tick = int(event["local_tick"])
                canonical.setdefault(local_tick, event)

            target_occurrences = [
                occurrence
                for occurrence in group_occurrences
                if occurrence["pattern_id"] == pattern_id
            ]
            for occurrence in target_occurrences:
                for local_tick, event in sorted(canonical.items()):
                    if local_tick >= occurrence["length"]:
                        continue
                    absolute_tick = occurrence["start_tick"] + local_tick
                    expanded.append({
                        **event,
                        "tick": int(local_tick),
                        "pattern_id": int(pattern_id),
                        "pattern_name": occurrence["pattern_name"],
                        "group_id": int(group_id),
                        "absolute_tick": int(absolute_tick),
                        "time": _ticks_to_seconds(absolute_tick, tempo),
                        "automation_expansion": "pattern_occurrence",
                        **({"sound_id": int(sound_id)} if sound_id is not None else {}),
                    })

    # Preserve genuinely unassignable events in their serialized song position.
    # This prevents data loss for future serialization variants while ensuring
    # lanes that can be proven Pattern-local use the correct repeating model.
    for event in global_events:
        if event.get("source_offset") in consumed_source_offsets:
            continue
        meta = target_metadata.get((event["kind"], int(event["target_id"])), {})
        sound_id = meta.get("sound_id")
        group_id = _group_for_sound(sound_id, group_ids) if sound_id is not None else None
        absolute_tick = int(event["tick"])
        expanded.append({
            **event,
            "pattern_id": None,
            "pattern_name": "Unassigned global VST automation",
            "group_id": group_id,
            "absolute_tick": absolute_tick,
            "time": _ticks_to_seconds(absolute_tick, tempo),
            "automation_expansion": "absolute_fallback",
            **({"sound_id": int(sound_id)} if sound_id is not None else {}),
        })

    unique = {}
    for event in expanded:
        key = (
            int(event["absolute_tick"]),
            event["kind"],
            int(event["target_id"]),
            round(float(event["value"]), 9),
            event.get("sound_id"),
        )
        unique[key] = event
    return sorted(
        unique.values(),
        key=lambda e: (
            int(e["absolute_tick"]),
            int(e["target_id"]),
            int(e.get("source_offset", 0)),
        ),
    )

def _automation_lane_map(expanded_events):
    targets = []
    seen = set()
    for event in expanded_events:
        key = (event["kind"], int(event["target_id"]))
        if key not in seen:
            seen.add(key)
            targets.append(key)
    targets.sort(key=lambda item: (item[1], item[0]))
    return {
        key: index + 1
        for index, key in enumerate(targets[:AUTOMATION_CARRIER_MAX_LANES])
    }


def _build_automation_carrier_fxchain(
    expanded_events,
    source_key: str,
    owner_key: str,
    target_metadata=None,
):
    """Build one pass-through JSFX and its envelopes for a single destination track."""
    target_metadata = target_metadata or {}
    lane_map = _automation_lane_map(expanded_events)
    if not lane_map:
        return [], lane_map

    fx_guid = "{" + str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"maschine-automation-fx:{source_key}:{owner_key}",
    )).upper() + "}"

    by_lane = {}
    for event in expanded_events:
        key = (event["kind"], int(event["target_id"]))
        lane = lane_map.get(key)
        if lane is not None:
            by_lane.setdefault(lane, []).append(event)

    lines = [
        "    <FXCHAIN",
        "      SHOW 0",
        "      LASTSEL 0",
        "      DOCKED 0",
        "      BYPASS 0 0 0",
        f'      <JS "{AUTOMATION_CARRIER_NAME}" ""',
        "        " + " ".join(["-"] * AUTOMATION_CARRIER_MAX_LANES),
        "      >",
        "      FLOATPOS 0 0 0 0",
        f"      FXID {fx_guid}",
    ]

    for lane in sorted(by_lane):
        events = sorted(
            by_lane[lane],
            key=lambda item: (item["time"], item["absolute_tick"]),
        )
        exemplar = events[0]
        _key = (exemplar["kind"], int(exemplar["target_id"]))
        _meta_for_filter = target_metadata.get(_key, {})
        if not _events_have_meaningful_movement(events, _meta_for_filter):
            continue
        target_id = int(exemplar["target_id"])
        kind = exemplar["kind"]
        env_guid = "{" + str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"maschine-automation-env:{source_key}:{owner_key}:{kind}:{target_id}",
        )).upper() + "}"
        meta = target_metadata.get((kind, target_id), {})
        label = meta.get(
            "name",
            f"NI {kind.upper()} target {target_id} (0x{target_id:04X})",
        )
        if (
            kind == "float"
            and meta.get("value_mode") != "absolute"
            and isinstance(meta.get("base_value"), (int, float))
        ):
            label += f" [base {float(meta['base_value']):.6f}]"

        lines.extend([
            f'      <PARMENV {lane} 0 1 0.5 "{label}"',
            f"        EGUID {env_guid}",
            "        ACT 1 -1",
            "        VIS 1 1 1",
            "        LANEHEIGHT 0 0",
            "        ARM 0",
            "        DEFSHAPE 0 -1 -1",
        ])

        for point_time, point_value, shape in _prepared_automation_points(events, meta):
            lines.append(f"        PT {point_time:.12g} {point_value:.9g} {shape}")

        lines.append("      >")

    lines.extend([
        "      WAK 0 0",
        "    >",
    ])
    return lines, lane_map



def _automation_occurrence_start_unit(kind, target_meta, first_event=None):
    """Return the effective parameter value at local Pattern tick 0.

    Maschine Pattern automation is modulation layered on top of the Sound's
    stored parameter value. If the first recorded modulation event occurs later
    than tick 0, every repeated Pattern occurrence must still begin from that
    stored/base value rather than inheriting the previous occurrence's end.
    """
    target_meta = target_meta or {}
    base = target_meta.get("base_value")
    mode = target_meta.get("value_mode")

    if kind == "bool":
        if isinstance(base, (int, float)):
            return 1.0 if float(base) > 0.0 else 0.0
        if first_event is not None:
            return _maschine_mod_value_to_unit(first_event.get("value", 0.0), kind, target_meta)
        return 0.0

    # Carrier-centred lanes represent zero modulation at the midpoint even
    # when the underlying static parameter base cannot be routed directly.
    if mode == "carrier_centered":
        return 0.5

    # Absolute and offset lanes both begin from the serialized static/base
    # parameter value before their first Pattern modulation event.
    if isinstance(base, (int, float)) and -0.001 <= float(base) <= 1.001:
        return max(0.0, min(1.0, float(base)))

    # Last-resort preservation for future formats lacking an explicit base.
    if first_event is not None:
        return _maschine_mod_value_to_unit(first_event.get("value", 0.0), kind, target_meta)
    return 0.5


def _prepared_automation_points(events, target_meta=None):
    """Build REAPER points with explicit Pattern start/reset and end/hold boundaries.

    REAPER envelopes are continuous, whereas Maschine Pattern automation is
    re-instantiated every time the Pattern/clip is placed. Two boundary rules
    are therefore required for every expanded occurrence:

      * if the first recorded event is later than local tick 0, write the
        Pattern's effective starting/base value at the exact occurrence start;
      * make the final point square/hold so REAPER cannot interpolate across
        the gap into the next Pattern occurrence.
    """
    target_meta = target_meta or {}
    ordered = sorted(events, key=lambda item: (item["time"], item["absolute_tick"]))

    # Group expanded events by Pattern occurrence while retaining absolute
    # fallback events unchanged.
    occurrence_events = {}
    fallback_events = []
    for event in ordered:
        occurrence_start_tick = None
        if event.get("automation_expansion") == "pattern_occurrence":
            local_tick = event.get("local_tick")
            abs_tick = event.get("absolute_tick")
            if isinstance(local_tick, int) and isinstance(abs_tick, int):
                occurrence_start_tick = int(abs_tick) - int(local_tick)
        if occurrence_start_tick is None:
            fallback_events.append(event)
        else:
            occurrence_events.setdefault(occurrence_start_tick, []).append(event)

    prepared = []

    for occurrence_start_tick in sorted(occurrence_events):
        occ = sorted(
            occurrence_events[occurrence_start_tick],
            key=lambda item: (int(item.get("local_tick", 0)), item["time"], item["absolute_tick"]),
        )
        first = occ[0]
        first_local_tick = int(first.get("local_tick", 0))

        # Derive the exact occurrence start time from this event's known
        # absolute tick/time relation. Tempo is constant within these decoded
        # stress-test projects, and the first occurrence naturally resolves to
        # time zero.
        if occurrence_start_tick == 0:
            occurrence_start_time = 0.0
        else:
            abs_tick = int(first.get("absolute_tick", 0))
            if abs_tick > 0:
                occurrence_start_time = float(first["time"]) * (float(occurrence_start_tick) / float(abs_tick))
            else:
                occurrence_start_time = float(first["time"])

        if first_local_tick > 0:
            start_value = _automation_occurrence_start_unit(first["kind"], target_meta, first)
            prepared.append({
                "event": first,
                "time": round(float(occurrence_start_time), 12),
                "value": round(float(start_value), 9),
                "occurrence_start": occurrence_start_tick,
                "synthetic_start": True,
            })

        last_key = None
        for event in occ:
            point_time = round(float(event["time"]), 12)
            point_value = round(
                _maschine_mod_value_to_unit(event["value"], event["kind"], target_meta),
                9,
            )
            key = (point_time, point_value)
            if key == last_key:
                continue
            last_key = key
            prepared.append({
                "event": event,
                "time": point_time,
                "value": point_value,
                "occurrence_start": occurrence_start_tick,
                "synthetic_start": False,
            })

    # Preserve absolute/future-format events without imposing Pattern reset
    # semantics on data whose occurrence ownership could not be proven.
    last_fallback = None
    for event in fallback_events:
        point_time = round(float(event["time"]), 12)
        point_value = round(
            _maschine_mod_value_to_unit(event["value"], event["kind"], target_meta),
            9,
        )
        key = (point_time, point_value)
        if key == last_fallback:
            continue
        last_fallback = key
        prepared.append({
            "event": event,
            "time": point_time,
            "value": point_value,
            "occurrence_start": None,
            "synthetic_start": False,
        })

    prepared.sort(key=lambda p: (p["time"], 0 if p["synthetic_start"] else 1))

    result = []
    for i, point in enumerate(prepared):
        event = point["event"]
        shape = 1 if event["kind"] == "bool" else 0
        # v0.20.4: an inserted Pattern-start reset is a boundary value, not
        # the start of a ramp toward the first recorded automation point. Hold
        # it flat until that first real event arrives.
        if point.get("synthetic_start"):
            shape = 1
        occurrence_start = point["occurrence_start"]
        if occurrence_start is not None:
            # Final point of this occurrence holds flat until the next point.
            next_occurrence = prepared[i + 1]["occurrence_start"] if i + 1 < len(prepared) else None
            if i + 1 == len(prepared) or next_occurrence != occurrence_start:
                shape = 1
        result.append((point["time"], point["value"], shape))
    return result


def _events_have_meaningful_movement(events, target_meta=None):
    """Suppress one-point/default-only lanes while preserving real automation."""
    target_meta = target_meta or {}
    vals = [
        round(_maschine_mod_value_to_unit(e["value"], e["kind"], target_meta), 9)
        for e in events
    ]
    return len(vals) >= 2 and len(set(vals)) >= 2


def _build_direct_plugin_parmenv(events, param_index, source_key, owner_key, label, target_meta=None):
    """Build a REAPER PARMENV for the currently preceding FX in an FXCHAIN."""
    target_meta = target_meta or {}
    events = sorted(events, key=lambda item: (item["time"], item["absolute_tick"]))
    env_guid = "{" + str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"maschine-direct-env:{source_key}:{owner_key}:{param_index}:{label}",
    )).upper() + "}"
    lines = [
        f'      <PARMENV {int(param_index)} 0 1 0.5 "{label}"',
        f"        EGUID {env_guid}",
        "        ACT 1 -1",
        "        VIS 1 1 1",
        "        LANEHEIGHT 0 0",
        "        ARM 0",
        "        DEFSHAPE 0 -1 -1",
    ]
    for point_time, point_value, shape in _prepared_automation_points(events, target_meta):
        lines.append(f"        PT {point_time:.12g} {point_value:.9g} {shape}")
    lines.append("      >")
    return lines


def _insert_plugin_direct_automation(track_lines, expanded_events, source_key, owner_key, target_metadata, plugin_name=""):
    """
    Route automation with a validated native REAPER parameter destination
    directly to the destination instrument. v0.20.1 uses the generic VST3
    identity resolver for hosted plug-in parameters; unresolved lanes remain on
    the generic carrier fallback rather than using per-plug-in target tables.

    Returns (track_lines, consumed_target_keys).
    """
    target_metadata = target_metadata or {}
    grouped = {}
    for event in expanded_events:
        key = (event["kind"], int(event["target_id"]))
        grouped.setdefault(key, []).append(event)

    direct = []
    consumed = set()
    for key, events in grouped.items():
        meta = target_metadata.get(key, {})
        page = str(meta.get("page", "")).strip().lower()
        # v0.19 generic VST3 direct mapping. The MXPRJ wrapper supplies a native
        # VST3 parameter ID; a matching identity probe translates that stable ID
        # to REAPER's host parameter index. Keep the carrier fallback when no
        # matching identity map is available rather than guessing.
        # AB04 route-profile layer: a verified instrument-internal MIDI-CC mapping
        # supersedes the host-parameter envelope for this lane. The automation
        # has already been embedded into the Pattern MIDI item(s), so consume
        # the lane here to prevent both PARMENV and carrier duplication.
        if isinstance(meta.get("midi_cc_number"), int):
            if _events_have_meaningful_movement(events, meta):
                consumed.add(key)
            continue

        track_is_kk = any("Komplete Kontrol" in line for line in track_lines)
        if track_is_kk and isinstance(meta.get("komplete_kontrol_parameter_index"), int):
            mapped_index = int(meta["komplete_kontrol_parameter_index"])
        else:
            mapped_index = meta.get("reaper_parameter_index")
        if (
            meta.get("owner") == "SoundPlugin"
            and isinstance(mapped_index, int)
            and mapped_index >= 0
            and _events_have_meaningful_movement(events, meta)
        ):
            label = "MASCHINE: " + str(meta.get("name") or f"VST parameter {mapped_index}")
            direct.extend(_build_direct_plugin_parmenv(
                events, mapped_index, source_key, owner_key, label, meta
            ))
            consumed.add(key)
            continue

        # No per-plug-in hard-coded automation targets remain here. If the
        # native VST3 identity could not be resolved, leave the lane unconsumed
        # so the generic automation carrier preserves it for later recovery.

    if not direct:
        return list(track_lines), consumed

    result = list(track_lines)
    # The restored instrument is the first FX in these generated tracks.
    # Insert its PARMENV blocks after its FXID and before WAK / any later FX.
    fxid_index = next(
        (i for i, line in enumerate(result) if line.startswith("      FXID ")),
        None,
    )
    if fxid_index is None:
        return result, set()

    result[fxid_index + 1:fxid_index + 1] = direct
    for i, line in enumerate(result):
        if line.startswith("    AUTOMODE "):
            result[i] = "    AUTOMODE 1"
            break
    return result, consumed


def _insert_automation_carrier_into_track(
    track_lines,
    expanded_events,
    source_key: str,
    owner_key: str,
    target_metadata=None,
):
    """Put the automation carrier directly on an already-generated Group/bus track."""
    fx_lines, lane_map = _build_automation_carrier_fxchain(
        expanded_events,
        source_key,
        owner_key,
        target_metadata,
    )
    if not fx_lines:
        return list(track_lines), lane_map

    result = list(track_lines)

    # Instrument tracks already contain their VST FXCHAIN. In that case merge
    # the generated carrier JSFX into the existing chain immediately before its
    # final WAK/closing lines; Group/bus tracks still receive a new FXCHAIN.
    if any(line.startswith("    <FXCHAIN") for line in result):
        inner = fx_lines[4:-2]  # omit <FXCHAIN header and its final WAK/close
        close_index = None
        for i in range(len(result) - 1, -1, -1):
            if result[i].startswith("      WAK "):
                close_index = i
                break
        if close_index is None:
            raise RuntimeError(f"Could not locate FXCHAIN close for {owner_key!r}.")
        result[close_index:close_index] = inner
        return result, lane_map

    for i, line in enumerate(result):
        if line.startswith("    AUTOMODE "):
            result[i] = "    AUTOMODE 1"
        elif line.startswith("    FX "):
            result[i] = "    FX 1"

    if not result or result[-1] != "  >":
        raise RuntimeError("Malformed generated destination track.")

    result[-1:-1] = fx_lines
    return result, lane_map


def _build_automation_carrier_track(
    expanded_events,
    source_key: str,
    target_metadata=None,
    name="MASCHINE AUTOMATION (unresolved)",
):
    """Fallback carrier for events whose owning Maschine Group cannot be resolved."""
    lane_map = _automation_lane_map(expanded_events)
    if not lane_map:
        return [], lane_map

    track_guid = "{" + str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"maschine-automation-track:{source_key}:unresolved",
    )).upper() + "}"
    safe_name = name.replace('"', "'")
    fx_lines, lane_map = _build_automation_carrier_fxchain(
        expanded_events,
        source_key,
        "unresolved",
        target_metadata,
    )

    lines = [
        f"  <TRACK {track_guid}",
        f'    NAME "{safe_name}"',
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 1",
        "    PANLAWFLAGS 3",
        "    VOLPAN 1 0 -1 -1 1",
        "    MUTESOLO 0 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 0 0",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    SEL 0",
        "    REC 0 0 0 0 0 0 0 0",
        "    VU 2",
        "    TRACKHEIGHT 0 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 1",
        f"    TRACKID {track_guid}",
        "    PERF 0",
        "    MIDIOUT -1 -1",
        "    MAINSEND 0 0",
    ]
    lines.extend(fx_lines)
    lines.append("  >")
    return lines, lane_map


def _write_automation_carrier_jsfx(path: Path):
    """Write the generic 64-lane pass-through JSFX used as an automation holder."""
    rows = [
        "desc:Maschine Automation Carrier (64 lanes)",
        "// Generated by Maschine -> REAPER Converter v0.18.4",
        "// Audio passes through unchanged; sliders are portable automation targets.",
        "",
    ]
    for lane in range(1, AUTOMATION_CARRIER_MAX_LANES + 1):
        rows.append(f"slider{lane}:0<0,1,0.000001>Automation Lane {lane:02d}")
    rows.extend([
        "",
        "@sample",
        "// No DSP required: spl0/spl1 are left untouched.",
    ])
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# v0.10 REAPER defaults, routing buses and MXPRJ compatibility
# ---------------------------------------------------------------------------

INSTRUMENT_VOL_MINUS_3DB = 10.0 ** (-3.0 / 20.0)


def _set_track_volume(track_lines, linear_volume: float):
    result = list(track_lines)

    for i, line in enumerate(result):
        if line.startswith("    VOLPAN "):
            parts = line.split()
            if len(parts) < 2:
                raise RuntimeError("Malformed VOLPAN line.")
            parts[1] = f"{linear_volume:.14f}"
            result[i] = "    " + " ".join(parts)
            return result

    raise RuntimeError("Generated track has no VOLPAN line.")


def _set_track_record_disarmed(track_lines):
    """
    REAPER REC field 1 is the record-arm state. Preserve the remainder of the
    template's recording/input configuration but force record arm off.
    """
    result = list(track_lines)

    for i, line in enumerate(result):
        if line.startswith("    REC "):
            parts = line.split()
            if len(parts) < 2:
                raise RuntimeError("Malformed REC line.")
            parts[1] = "0"
            result[i] = "    " + " ".join(parts)
            return result

    raise RuntimeError("Generated track has no REC line.")


def _set_track_main_send(track_lines, enabled: bool):
    result = list(track_lines)
    value = "1" if enabled else "0"

    for i, line in enumerate(result):
        if line.startswith("    MAINSEND "):
            parts = line.split()
            if len(parts) >= 3:
                parts[1] = value
                result[i] = "    " + " ".join(parts)
            else:
                result[i] = f"    MAINSEND {value} 0"
            return result

    # Insert before the track-closing line if the template lacks MAINSEND.
    if result and result[-1] == "  >":
        result.insert(-1, f"    MAINSEND {value} 0")
        return result

    raise RuntimeError("Could not set track MAINSEND.")


def _set_first_fx_offline(track_lines, offline: bool):
    """
    REAPER's BYPASS line fields are:
      field 1 = bypass
      field 2 = offline

    Keep bypass false and toggle only the Offline flag so the plug-in state
    remains stored while REAPER does not instantiate/process the FX.
    """
    result = list(track_lines)
    value = "1" if offline else "0"

    fxchain_seen = False
    for i, line in enumerate(result):
        if line.startswith("    <FXCHAIN"):
            fxchain_seen = True
            continue

        if fxchain_seen and line.startswith("      BYPASS "):
            parts = line.split()

            while len(parts) < 4:
                parts.append("0")

            parts[1] = "0"
            parts[2] = value
            result[i] = "      " + " ".join(parts)
            return result

    raise RuntimeError("Generated instrument track has no FX BYPASS line.")


def _insert_auxrecv(track_lines, source_track_index: int):
    """
    Add a unity-gain stereo post-fader receive from source_track_index.

    AUXRECV fields used here:
      source index, mode=0(post-fader), volume=1, pan=0,
      mute=0, mono=0, phase=0, src audio=0(1+2), dst audio=0(1+2),
      panlaw=-1, MIDI=0(none), automode=-1(use track mode).
    """
    result = list(track_lines)
    receive = (
        f"    AUXRECV {int(source_track_index)} "
        "0 1 0 0 0 0 0 0 -1 0 -1"
    )

    # Receives conventionally sit before MIDIOUT / MAINSEND.
    for i, line in enumerate(result):
        if line.startswith("    MIDIOUT "):
            result.insert(i, receive)
            return result

    if result and result[-1] == "  >":
        result.insert(-1, receive)
        return result

    raise RuntimeError("Could not insert AUXRECV.")


def _build_bus_track(name: str, source_key: str, bus_key: str, main_send: bool):
    guid = _guid(f"maschine-routing-bus:{source_key}:{bus_key}")
    safe_name = name.replace('"', "'")

    return [
        f"  <TRACK {guid}",
        f'    NAME "{safe_name}"',
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 0",
        "    PANLAWFLAGS 3",
        "    VOLPAN 1 0 -1 -1 1",
        "    MUTESOLO 0 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 0 0",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    SEL 0",
        "    REC 0 0 0 0 0 0 0 0",
        "    VU 2",
        "    TRACKHEIGHT 0 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 0",
        f"    TRACKID {guid}",
        "    PERF 0",
        "    MIDIOUT -1 -1",
        f"    MAINSEND {1 if main_send else 0} 0",
        "  >",
    ]


def _maltodextrin2_routing_profile(data: bytes, populated_group_count: int):
    """
    Recognize the Group-routing topology documented in Maltodextrin2.

    The custom Group names stored in this project plus the user's confirmed
    routing define:
        A1 Kit   -> A2 Kit
        B1 Thump -> B2 ThumpFX
        C1 Lead  -> C2 LeadFX
        D1 Pings -> D2 PingsFX
        E1 Bass  -> E2 Bass E2
        A2-E2    -> H2 SUBMix -> Master

    FX themselves and Clip automation are intentionally not imported yet.
    """
    signatures = (
        b"ThumpFX",
        b"LeadFX",
        b"PingsFX",
        b"Bass E2",
        b"SUBMix",
    )

    if populated_group_count != 5:
        return None

    if not all(signature in data for signature in signatures):
        return None

    return {
        "source_groups": [
            ("A1", "Kit"),
            ("B1", "Thump"),
            ("C1", "Lead"),
            ("D1", "Pings"),
            ("E1", "Bass"),
        ],
        "fx_buses": [
            ("A2", "Kit"),
            ("B2", "ThumpFX"),
            ("C2", "LeadFX"),
            ("D2", "PingsFX"),
            ("E2", "Bass E2"),
        ],
        "submix": ("H2", "SUBMix"),
    }


# ----- Compatibility fallback for newer Maschine serialization IDs -----

def _compat_pattern_object_id_129(data: bytes, pattern):
    return _serialized_object_id_before_name(
        data,
        pattern["source_offset"],
        129,
        (1, 5),
    )


def _compat_extract_note_vectors_offset6(data: bytes):
    """Compatibility reader for saves where the EventList class is NoteEvent-class - 6.

    This family uses ordinary count-based NoteEvent vectors for simpler
    Patterns, but can interleave modulation records inside VST-instrument
    vectors.  Decode exact v2 vectors when possible, then fall back to the v3
    structural NoteEvent scanner for interleaved vectors.  The first vector has
    a one-off class-definition header used by this family.
    """
    located = _locate_note_event_class(data)
    if located is None:
        return None, None, {}
    class_pos, note_class_id = located
    list_class_id = note_class_id - 6
    float_class_id = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    marker = enc_var(list_class_id)
    vectors = {}

    # First vector: list-class, object-id, count, first-start, 0, 0,
    # followed by the NoteEvent class definition.  The NoteEvent payload after
    # the class name uses the same nested-row/core layout as earlier families.
    first_pos = data.rfind(marker, max(0, class_pos - 80), class_pos)
    if first_pos >= 0:
        try:
            q = first_pos
            value, q = read_var_at(data, q)
            object_id, q = read_var_at(data, q)
            count, q = read_var_at(data, q)
            first_start_tick, q = read_var_at(data, q)
            zero_a, q = read_var_at(data, q)
            zero_b, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            name_len, q = read_var_at(data, q)
            if (
                value != list_class_id
                or class_ref != note_class_id
                or data[q:q + name_len] != NOTE_EVENT_CLASS
                or not (1 <= count <= 4096)
                or not (0 <= first_start_tick <= 10_000_000)
                or zero_a != 0 or zero_b != 0
            ):
                raise RuntimeError

            q = class_pos + len(NOTE_EVENT_CLASS)
            _v, q = read_var_at(data, q)
            _v, q = read_var_at(data, q)
            for _ in range(2):
                _row, q = read_var_at(data, q)
                _v, q = read_var_at(data, q)
                _v, q = read_var_at(data, q)
            _row, q = read_var_at(data, q)
            core = []
            for _ in range(7):
                value, q = read_var_at(data, q)
                core.append(value)
            first_event = {
                "start": int(first_start_tick),
                "flag": int(core[0]),
                "length": int(core[1]),
                "note": int(core[-2]),
                "velocity": int(core[-1]),
            }
            if not (
                0 < first_event["length"] <= 1_000_000
                and 0 <= first_event["note"] <= 127
                and 1 <= first_event["velocity"] <= 127
            ):
                raise RuntimeError
            events = [first_event]
            for _index in range(1, int(count)):
                _outer_row, q = read_var_at(data, q)
                start_tick, q = read_var_at(data, q)
                class_ref, q = read_var_at(data, q)                if class_ref != note_class_id:
                    raise RuntimeError
                event, q = _parse_note_event_core_v3(
                    data, q, int(start_tick), note_class_id
                )
                events.append(event)
            vectors[int(object_id)] = events
        except Exception:
            pass

    # Regular vectors.  First try the exact count-based parser.  VST-hosted
    # vectors can contain FloatModulationEvent records between notes, in which
    # case the exact parser rejects the vector and the structural scanner is
    # used over the span ending at the next occurrence of the same vector id.
    pos = 0
    while True:
        p = data.find(marker, pos)
        if p < 0:
            break
        pos = p + 1
        try:
            q = p
            value, q = read_var_at(data, q)
            object_id, q = read_var_at(data, q)
            vector_meta, q = read_var_at(data, q)
            first_start_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if (
                value != list_class_id
                or class_ref not in tuple(x for x in (note_class_id, float_class_id) if x is not None)
                or not (0 < object_id < 10_000_000)
                or not (0 <= first_start_tick <= 10_000_000)
            ):
                continue

            ref_marker = marker + enc_var(int(object_id))
            boundary = data.find(ref_marker, p + 1)
            if boundary < 0:
                boundary = min(len(data), p + 250_000)

            if class_ref == note_class_id:
                try:
                    parsed_object_id, events = _parse_regular_note_vector_v2(
                        data, p, list_class_id, note_class_id
                    )
                    if events:
                        vectors.setdefault(int(parsed_object_id), events)
                        continue
                except Exception:
                    pass

                first_event, first_end = _parse_note_event_core_v3(
                    data, q, int(first_start_tick), note_class_id
                )
                events = _scan_note_events_v3(
                    data,
                    p,
                    boundary,
                    note_class_id,
                    first_event=first_event,
                    first_end=first_end,
                )
            else:
                # A Sound's EventList may start with FloatModulationEvent yet
                # also contain NoteEvents later in the same vector.  Scan the
                # bounded list for those notes rather than treating it as
                # automation-only.
                events = _scan_note_events_v3(
                    data, p, boundary, note_class_id
                )

            if events:
                vectors.setdefault(int(object_id), events)
        except Exception:
            pass

    return note_class_id, list_class_id, vectors


def _compat_eventlist_vector_starts_offset6(data: bytes):
    """Return structurally valid class-6-shift EventList vector starts.

    The same EventList object ID can occur again later as a registry/reference
    record, so using only the next same-object marker is not a safe byte bound.
    This catalog lets mixed note/automation vectors stop at the next *real*
    EventList vector of any Sound.
    """
    located = _locate_note_event_class(data)
    if located is None:
        return []
    _class_pos, note_class_id = located
    float_class_id = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    list_class_id = note_class_id - 6
    allowed = tuple(x for x in (note_class_id, float_class_id) if x is not None)
    marker = enc_var(list_class_id)
    starts = []
    pos = 0
    while True:
        p = data.find(marker, pos)
        if p < 0:
            break
        pos = p + 1
        try:
            q = p
            value, q = read_var_at(data, q)
            object_id, q = read_var_at(data, q)
            vector_meta, q = read_var_at(data, q)
            first_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if (
                value == list_class_id
                and 0 < object_id < 10_000_000
                and 0 < vector_meta < 1_000_000
                and 0 <= first_tick <= 10_000_000
                and class_ref in allowed
            ):
                starts.append(int(p))
        except Exception:
            pass
    return sorted(set(starts))


def _compat_eventlist_vector_record_offset6(data: bytes, object_id: int):
    """Locate one real class-6-shift EventList vector and its bounded byte span."""
    located = _locate_note_event_class(data)
    if located is None:
        return None
    _class_pos, note_class_id = located
    float_class_id = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    list_class_id = note_class_id - 6
    marker = enc_var(list_class_id) + enc_var(int(object_id))
    positions = []
    pos = 0
    while True:
        p = data.find(marker, pos)
        if p < 0:
            break
        positions.append(p)
        pos = p + 1

    vector_starts = _compat_eventlist_vector_starts_offset6(data)

    for p in positions:
        try:
            q = p
            value, q = read_var_at(data, q)
            parsed_object_id, q = read_var_at(data, q)
            vector_meta, q = read_var_at(data, q)
            first_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if (
                value != list_class_id
                or parsed_object_id != int(object_id)
                or class_ref not in tuple(x for x in (note_class_id, float_class_id) if x is not None)
                or not (0 < vector_meta < 1_000_000)
                or not (0 <= first_tick <= 10_000_000)
            ):
                continue

            # End at whichever comes first: the next same-object marker
            # (often a registry/reference record) or the next structurally valid
            # EventList vector for any Sound.  This prevents a note-only vector
            # from swallowing automation that belongs to a later Sound.
            candidates = []
            next_same = next((x for x in positions if x > p), None)
            if next_same is not None:
                candidates.append(int(next_same))
            next_vector = next((x for x in vector_starts if x > p), None)
            if next_vector is not None:
                candidates.append(int(next_vector))
            candidates.append(min(len(data), p + 250_000))
            end = min(candidates)

            return {
                "start": int(p),
                "end": int(end),
                "object_id": int(object_id),
                "vector_meta": int(vector_meta),
                "first_tick": int(first_tick),
                "class_ref": int(class_ref),
                "header_end": int(q),
                "note_class_id": int(note_class_id),
                "float_class_id": int(float_class_id) if float_class_id is not None else None,
                "list_class_id": int(list_class_id),
            }
        except Exception:
            pass
    return None


def _compat_parse_float_mod_payload_offset6(data: bytes, pos: int, end: int):
    """Decode the stable row/target/two-float payload after a FloatModulationEvent ref."""
    try:
        row_a, q = read_var_at(data, pos)
        row_b, q = read_var_at(data, q)
        zero, q = read_var_at(data, q)
        target_field, q = read_var_at(data, q)
        target_id, q = read_var_at(data, q)
        if (
            zero != 0
            or not (0 < row_a < 10_000_000)
            or not (0 < row_b < 10_000_000)
            or not (1 <= target_field <= 512)
            or not (0 < target_id < 10_000_000)
        ):
            return None

        floats = []
        fp = q
        while len(floats) < 2:
            z = data.find(b"\x01\x04", fp, min(end, q + 96))
            if z < 0 or z + 6 > end:
                return None
            floats.append(struct.unpack_from("<f", data, z + 2)[0])
            fp = z + 6
        value = float(floats[1])
        if not (-16.0 <= value <= 16.0):
            return None
        return {
            "target_id": int(target_id),
            "value": value,
            "target_field": int(target_field),
        }
    except Exception:
        return None


def _compat_decode_eventlist_float_automation_offset6(data: bytes, vector_id: int):
    """Decode FloatModulationEvents embedded in one per-Sound EventList."""
    record = _compat_eventlist_vector_record_offset6(data, vector_id)
    if not record or record.get("float_class_id") is None:
        return []
    start = int(record["start"])
    end = int(record["end"])
    float_class_id = int(record["float_class_id"])
    events = []

    # If this vector defines FloatModulationEvent, its first event has no
    # ordinary outer-row/tick/class-ref record after the class name.  The tick
    # lives in the EventList header.
    if int(record.get("class_ref", -1)) == float_class_id:
        try:
            q = int(record["header_end"])
            name_len, q = read_var_at(data, q)
            if data[q:q + name_len] == FLOAT_MOD_EVENT_CLASS:
                q += name_len
                _class_meta_a, q = read_var_at(data, q)
                _class_meta_b, q = read_var_at(data, q)
                payload = _compat_parse_float_mod_payload_offset6(data, q, end)
                if payload:
                    events.append({
                        "tick": int(record["first_tick"]),
                        "kind": "float",
                        "storage": "global_vst",
                        "source_offset": start,
                        **payload,
                    })
        except Exception:
            pass

    # Subsequent modulation records use:
    # outer-row, local-tick, FloatModulationEvent-class, payload.
    for s in range(start, end):
        try:
            outer_row, q = read_var_at(data, s)
            tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if (
                class_ref != float_class_id
                or not (0 < outer_row < 10_000_000)
                or not (0 <= tick <= 10_000_000)
            ):
                continue
            payload = _compat_parse_float_mod_payload_offset6(data, q, end)
            if payload:
                events.append({
                    "tick": int(tick),
                    "kind": "float",
                    "storage": "global_vst",
                    "source_offset": int(s),
                    **payload,
                })
        except Exception:
            pass

    unique = {}
    for event in events:
        key = (
            int(event["tick"]),
            int(event["target_id"]),
            round(float(event["value"]), 7),
        )
        unique[key] = event
    return sorted(unique.values(), key=lambda e: (e["tick"], e["target_id"], e["source_offset"]))


def _compat_extract_pattern_eventlist_automation_offset6(data: bytes, patterns_by_id):
    """Build Pattern-local automation directly from each Sound's mixed EventList."""
    out = []
    for pattern_id, pattern in (patterns_by_id or {}).items():
        events = []
        for sound_id, vector_id in pattern.get("slots", {}).items():
            if not vector_id:
                continue
            for event in _compat_decode_eventlist_float_automation_offset6(data, int(vector_id)):
                if int(event["tick"]) >= int(pattern.get("length", 0)):
                    continue
                events.append({
                    **event,
                    "sound_id_hint": int(sound_id),
                    "vector_id": int(vector_id),
                })
        if events:
            out.append({
                "source_offset": int(pattern.get("source_offset", 0)),
                "name": pattern.get("name", f"Pattern {pattern_id}"),
                "length": int(pattern.get("length", 0)),
                "pattern_class": pattern.get("pattern_class"),
                "pattern_id": int(pattern_id),
                "automation_events": events,
                "storage": "mixed_event_list",
            })
    return out


def _compat_extract_patterns_129(data: bytes, sound_ids):
    _note_class, list_class_id, vectors = extract_note_vectors_v2(data)
    if not vectors:
        _note_class, list_class_id, vectors = _compat_extract_note_vectors_offset6(data)
    if not vectors:
        return [], {}

    wanted = {sid for sid in sound_ids if sid is not None}
    patterns = []

    for match in re.finditer(b"Pattern", data):
        pos = match.start()
        name_match = re.match(rb"Pattern[ -~]*", data[pos:pos + 96])
        if not name_match:
            continue

        try:
            length_ticks = _parse_pattern_length(
                data,
                pos,
                name_match.group(),
            )
        except Exception:
            continue

        chunk = data[pos:pos + 380]
        slots = {}
        search_pos = 0
        sound_marker = enc_var(23)

        while True:
            p = chunk.find(sound_marker, search_pos)
            if p < 0:
                break

            try:
                sound_id, q = read_var_at(
                    chunk,
                    p + len(sound_marker),
                )

                if sound_id not in wanted:
                    search_pos = p + 1
                    continue

                # Slot-class IDs are version-dependent; the following object
                # id + NoteEvent-list reference layout is stable.
                _slot_class, q = read_var_at(chunk, q)
                _slot_object_id, q = read_var_at(chunk, q)

                vector_id = 0

                if chunk[q:q + 2] == b"\xff\x01":
                    q += 2
                else:
                    class_ref, q2 = read_var_at(chunk, q)
                    if class_ref != list_class_id:
                        search_pos = p + 1
                        continue
                    vector_id, q = read_var_at(chunk, q2)

                slots[sound_id] = vector_id

            except Exception:
                pass

            search_pos = p + 1

        if slots:
            patterns.append({
                "source_offset": pos,
                "name": name_match.group().decode(
                    "utf-8",
                    errors="replace",
                ),
                "length": length_ticks,
                "slots": slots,
            })

    return patterns, vectors


def _compat_extract_scenes_188(data: bytes):
    matches = []

    for match in re.finditer(b"Scene ", data):
        pos = match.start()
        name_match = re.match(rb"Scene [ -~]*", data[pos:pos + 96])
        if name_match:
            matches.append((
                pos,
                name_match.group().decode(
                    "utf-8",
                    errors="replace",
                ),
            ))

    scenes = {}

    for index, (pos, name) in enumerate(matches):
        scene_id = _serialized_object_id_before_name(
            data,
            pos,
            188,
            (1, 2),
        )
        if scene_id is None:
            continue

        end = (
            matches[index + 1][0]
            if index + 1 < len(matches)
            else min(len(data), pos + 420)
        )

        chunk = data[pos + len(name.encode("utf-8")):end]
        slots = {}
        search_pos = 0
        group_marker = enc_var(22)

        while True:
            p = chunk.find(group_marker, search_pos)
            if p < 0:
                break

            try:
                group_id, q = read_var_at(
                    chunk,
                    p + len(group_marker),
                )

                if chunk[q:q + 3] == b"\x00\x01\x01":
                    q += 3

                _slot_class, q = read_var_at(chunk, q)
                _slot_object_id, q = read_var_at(chunk, q)

                pattern_id = 0

                if chunk[q:q + 2] == b"\xff\x01":
                    q += 2
                else:
                    pattern_class, q2 = read_var_at(chunk, q)
                    if pattern_class != 129:
                        search_pos = p + 1
                        continue
                    pattern_id, q = read_var_at(chunk, q2)

                slots[group_id] = pattern_id

            except Exception:
                pass

            search_pos = p + 1

        if slots:
            scenes[scene_id] = {
                "scene_id": scene_id,
                "name": name,
                "slots": slots,
                "source_offset": pos,
            }

    return scenes


def _compat_extract_sections_199(data: bytes, scenes):
    if not scenes:
        return []

    last_scene_pos = max(
        scene["source_offset"]
        for scene in scenes.values()
    )

    section_marker = enc_var(199)
    scene_marker = enc_var(188)
    sections = []
    search_pos = last_scene_pos

    while True:
        p = data.find(section_marker, search_pos)
        if p < 0:
            break

        q = p + len(section_marker)

        try:
            first, q1 = read_var_at(data, q)
            section_object_id = None

            if first == 1:
                second, qq = read_var_at(data, q1)
                if [first, second] == [1, 0]:
                    section_object_id, q = read_var_at(data, qq)

            if section_object_id is None:
                section_object_id = first
                q = q1

            scene_id = None

            if data[q:q + len(scene_marker)] == scene_marker:
                scene_id, q = read_var_at(
                    data,
                    q + len(scene_marker),
                )
            elif data[q:q + 2] == b"\xff\x01":
                q += 2
            else:
                search_pos = p + 1
                continue

            for _ in range(4):
                _value, q = read_var_at(data, q)

            timing = []
            for _ in range(6):
                value, q = read_var_at(data, q)
                timing.append(value)

            if (
                timing[0] != 0
                or timing[1] != timing[2]
                or not (1 <= timing[1] <= 100_000_000)
            ):
                search_pos = p + 1
                continue

            if scene_id is None:
                break

            scene = scenes.get(scene_id)
            if scene is None:
                search_pos = p + 1
                continue

            sections.append({
                "section_object_id": section_object_id,
                "scene_id": scene_id,
                "name": scene["name"],
                "length": timing[1],
                "previous_length": timing[3],
                "source_offset": p,
            })

        except Exception:
            pass

        search_pos = p + 1

    return sections



# ----- Compatibility fallback for the 124/154/165 serialization family -----

def _parse_note_event_core_v3(data: bytes, pos: int, start_tick: int, note_class_id: int):
    """Parse one NoteEvent payload after its class reference.

    This newer serialization family inserts variable-size per-note modulation
    records between NoteEvents. The NoteEvent's own nested rows/core remain
    stable, so we parse only that stable payload and let the vector scanner
    skip unrelated nested records between events.
    """
    q = pos
    row_ids = []
    for _ in range(3):
        value, q = read_var_at(data, q)
        if not (0 < value < 1_000_000):
            raise RuntimeError("Implausible NoteEvent row id.")
        row_ids.append(value)

    core = []
    for _ in range(5):
        value, q = read_var_at(data, q)
        core.append(value)

    event = {
        "start": int(start_tick),
        "flag": int(core[0]),
        "length": int(core[1]),
        "note": int(core[3]),
        "velocity": int(core[4]),
    }

    if not (
        0 < event["length"] <= 1_000_000
        and 0 <= event["note"] <= 127
        and 1 <= event["velocity"] <= 127
        and 0 <= event["start"] <= 10_000_000
    ):
        raise RuntimeError("Implausible NoteEvent payload.")

    return event, q


def _scan_note_events_v3(data: bytes, start_pos: int, end_pos: int,
                         note_class_id: int, first_event=None, first_end=None):
    """Collect stable NoteEvent records inside one serialized vector span."""
    candidates = []

    if first_event is not None and first_end is not None:
        candidates.append((start_pos, first_event, first_end))
        scan_start = first_end
    else:
        scan_start = start_pos

    for s in range(scan_start, end_pos):
        try:
            outer_row, q = read_var_at(data, s)
            if not (0 < outer_row < 1_000_000):
                continue

            start_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)
            if class_ref != note_class_id:
                continue
            if not (0 <= start_tick <= 10_000_000):
                continue

            event, q = _parse_note_event_core_v3(
                data,
                q,
                start_tick,
                note_class_id,
            )
            candidates.append((s, event, q))
        except Exception:
            pass

    # Byte-level scanning can rediscover the same payload from inside a large
    # unrelated varint. Retain only non-overlapping, monotonic records.
    events = []
    last_end = -1
    last_start = -1
    for pos, event, end in sorted(candidates, key=lambda item: item[0]):
        if pos < last_end:
            continue
        if event["start"] < last_start:
            continue
        events.append(event)
        last_end = end
        last_start = event["start"]

    return events


def extract_note_vectors_v3(data: bytes):
    """Decode NoteEvent vectors used by the 124/154/165 project family.

    The list class and NoteEvent class IDs remain the same as the earlier
    family, but the vector metadata/count field is no longer a literal event
    count and FloatModulationEvent records are interleaved between notes.
    """
    located = _locate_note_event_class(data)
    if located is None:
        return None, None, {}

    class_pos, note_class_id = located
    list_class_id = note_class_id - 5
    list_marker = enc_var(list_class_id)
    vectors = {}

    # Initial vector: the Boost class definition appears inside the first
    # vector, so its header is special. Observed stable layout:
    # list-class, 1, 0, object-id, 0, metadata, first-start-tick, ... class def.
    window_start = max(0, class_pos - 96)
    first_pos = data.rfind(list_marker, window_start, class_pos)
    if first_pos >= 0:
        try:
            q = first_pos
            value, q = read_var_at(data, q)
            if value != list_class_id:
                raise RuntimeError
            _meta1, q = read_var_at(data, q)
            _meta0, q = read_var_at(data, q)
            object_id, q = read_var_at(data, q)
            _zero, q = read_var_at(data, q)
            _vector_meta, q = read_var_at(data, q)
            first_start_tick, q = read_var_at(data, q)

            if not (0 < object_id < 10_000_000):
                raise RuntimeError
            if not (0 <= first_start_tick <= 10_000_000):
                raise RuntimeError

            q = class_pos + len(NOTE_EVENT_CLASS)
            # Stable one-time class metadata/nested rows before first core.
            _v, q = read_var_at(data, q)
            _v, q = read_var_at(data, q)
            for _ in range(2):
                _row, q = read_var_at(data, q)
                _v, q = read_var_at(data, q)
                _v, q = read_var_at(data, q)
            _row, q = read_var_at(data, q)

            core = []
            for _ in range(7):
                value, q = read_var_at(data, q)
                core.append(value)

            first_event = {
                "start": int(first_start_tick),
                "flag": int(core[0]),
                "length": int(core[1]),
                "note": int(core[-2]),
                "velocity": int(core[-1]),
            }
            if not (
                0 < first_event["length"] <= 1_000_000
                and 0 <= first_event["note"] <= 127
                and 1 <= first_event["velocity"] <= 127
            ):
                raise RuntimeError

            ref_marker = enc_var(list_class_id) + enc_var(object_id)
            boundary = data.find(ref_marker, first_pos + 1)
            if boundary < 0:
                boundary = min(len(data), first_pos + 250_000)

            events = _scan_note_events_v3(
                data,
                first_pos,
                boundary,
                note_class_id,
                first_event=first_event,
                first_end=q,
            )
            if events:
                vectors[object_id] = events
        except Exception:
            pass

    # Regular vectors have a compact stable header:
    # list-class, object-id, metadata, first-start-tick, NoteEvent-class.
    pos = 0
    while True:
        p = data.find(list_marker, pos)
        if p < 0:
            break
        pos = p + 1

        try:
            q = p
            value, q = read_var_at(data, q)
            if value != list_class_id:
                continue
            object_id, q = read_var_at(data, q)
            _vector_meta, q = read_var_at(data, q)
            first_start_tick, q = read_var_at(data, q)
            class_ref, q = read_var_at(data, q)

            if class_ref != note_class_id:
                continue
            if not (0 < object_id < 10_000_000):
                continue
            if not (0 <= first_start_tick <= 10_000_000):
                continue

            first_event, first_end = _parse_note_event_core_v3(
                data,
                q,
                first_start_tick,
                note_class_id,
            )

            ref_marker = enc_var(list_class_id) + enc_var(object_id)
            boundary = data.find(ref_marker, p + 1)
            if boundary < 0:
                boundary = min(len(data), p + 250_000)

            events = _scan_note_events_v3(
                data,
                p,
                boundary,
                note_class_id,
                first_event=first_event,
                first_end=first_end,
            )
            if events:
                vectors.setdefault(object_id, events)
        except Exception:
            pass

    return note_class_id, list_class_id, vectors


def _compat_pattern_object_id_124(data: bytes, pattern):
    return _serialized_object_id_before_name(
        data,
        pattern["source_offset"],
        124,
        (1, 5),
    )


def _compat_extract_scenes_154(data: bytes):
    matches = []
    for match in re.finditer(b"Scene ", data):
        pos = match.start()
        name_match = re.match(rb"Scene [ -~]*", data[pos:pos + 96])
        if name_match:
            matches.append((
                pos,
                name_match.group().decode("utf-8", errors="replace"),
            ))

    scenes = {}
    scene_class = 154
    slot_class_expected = 159
    pattern_class_expected = 124

    for index, (pos, name) in enumerate(matches):
        scene_id = _serialized_object_id_before_name(
            data,
            pos,
            scene_class,
            (1, 2),
        )
        if scene_id is None:
            continue

        end = (
            matches[index + 1][0]
            if index + 1 < len(matches)
            else min(len(data), pos + 520)
        )
        chunk = data[pos + len(name.encode("utf-8")):end]
        slots = {}
        search_pos = 0
        group_marker = enc_var(22)

        while True:
            p = chunk.find(group_marker, search_pos)
            if p < 0:
                break

            try:
                group_id, q = read_var_at(
                    chunk,
                    p + len(group_marker),
                )

                if chunk[q:q + 3] == b"\x00\x01\x01":
                    q += 3

                slot_class, q = read_var_at(chunk, q)
                if slot_class != slot_class_expected:
                    search_pos = p + 1
                    continue

                # First occurrence defines the slot class and inserts [1, 2]
                # before the actual slot object id. Try the ordinary layout
                # first, then the class-definition layout.
                q_slot = q
                slot_object_id, q_after_slot = read_var_at(chunk, q_slot)
                pattern_class, q_after_class = read_var_at(chunk, q_after_slot)

                if pattern_class != pattern_class_expected:
                    meta_a, q_meta = read_var_at(chunk, q_slot)
                    meta_b, q_meta = read_var_at(chunk, q_meta)
                    if (meta_a, meta_b) != (1, 2):
                        search_pos = p + 1
                        continue
                    slot_object_id, q_after_slot = read_var_at(chunk, q_meta)
                    pattern_class, q_after_class = read_var_at(chunk, q_after_slot)

                if pattern_class != pattern_class_expected:
                    search_pos = p + 1
                    continue

                pattern_id, _q_end = read_var_at(chunk, q_after_class)
                slots[int(group_id)] = int(pattern_id)
            except Exception:
                pass

            search_pos = p + 1

        if slots:
            scenes[int(scene_id)] = {
                "scene_id": int(scene_id),
                "name": name,
                "slots": slots,
                "source_offset": pos,
            }

    return scenes


def _compat_extract_sections_165(data: bytes, scenes):
    if not scenes:
        return []

    last_scene_pos = max(scene["source_offset"] for scene in scenes.values())
    section_marker = enc_var(165)
    scene_marker = enc_var(154)
    sections = []
    search_pos = last_scene_pos

    while True:
        p = data.find(section_marker, search_pos)
        if p < 0:
            break
        q = p + len(section_marker)

        try:
            first, q1 = read_var_at(data, q)
            section_object_id = None
            if first == 1:
                second, qq = read_var_at(data, q1)
                if [first, second] == [1, 0]:
                    section_object_id, q = read_var_at(data, qq)

            if section_object_id is None:
                section_object_id = first
                q = q1

            scene_id = None
            if data[q:q + len(scene_marker)] == scene_marker:
                scene_id, q = read_var_at(data, q + len(scene_marker))
            elif data[q:q + 2] == b"\xff\x01":
                q += 2
            else:
                search_pos = p + 1
                continue

            for _ in range(4):
                _value, q = read_var_at(data, q)

            timing = []
            for _ in range(6):
                value, q = read_var_at(data, q)
                timing.append(value)

            if (
                timing[0] != 0
                or timing[1] != timing[2]
                or not (1 <= timing[1] <= 100_000_000)
            ):
                search_pos = p + 1
                continue

            if scene_id is None:
                break
            scene = scenes.get(int(scene_id))
            if scene is None:
                search_pos = p + 1
                continue

            sections.append({
                "section_object_id": int(section_object_id),
                "scene_id": int(scene_id),
                "name": scene["name"],
                "length": int(timing[1]),
                "previous_length": int(timing[3]),
                "source_offset": p,
            })
        except Exception:
            pass

        search_pos = p + 1

    return sections


def _compat_extract_scenes_155(data: bytes):
    """Scene class 155 / slot class 160 variant used by newer arrangement saves."""
    matches = []
    for match in re.finditer(b"Scene ", data):
        pos = match.start()
        name_match = re.match(rb"Scene [ -~]*", data[pos:pos + 96])
        if name_match:
            matches.append((pos, name_match.group().decode("utf-8", errors="replace")))
    scenes = {}
    scene_class = 155
    slot_class_expected = 160
    pattern_class_expected = 124

    for index, (pos, name) in enumerate(matches):
        scene_id = _serialized_object_id_before_name(data, pos, scene_class, (1, 2))
        if scene_id is None:
            continue
        end = matches[index + 1][0] if index + 1 < len(matches) else min(len(data), pos + 520)
        chunk = data[pos + len(name.encode("utf-8")):end]
        slots = {}
        search_pos = 0
        group_marker = enc_var(22)
        while True:
            p = chunk.find(group_marker, search_pos)
            if p < 0:
                break
            try:
                group_id, q = read_var_at(chunk, p + len(group_marker))
                if chunk[q:q + 3] == b"\x00\x01\x01":
                    q += 3
                slot_class, q = read_var_at(chunk, q)
                if slot_class != slot_class_expected:
                    search_pos = p + 1
                    continue
                q_slot = q
                slot_object_id, q_after_slot = read_var_at(chunk, q_slot)
                pattern_class, q_after_class = read_var_at(chunk, q_after_slot)
                if pattern_class != pattern_class_expected:
                    meta_a, q_meta = read_var_at(chunk, q_slot)
                    meta_b, q_meta = read_var_at(chunk, q_meta)
                    if (meta_a, meta_b) != (1, 2):
                        search_pos = p + 1
                        continue
                    slot_object_id, q_after_slot = read_var_at(chunk, q_meta)
                    pattern_class, q_after_class = read_var_at(chunk, q_after_slot)
                if pattern_class != pattern_class_expected:
                    search_pos = p + 1
                    continue
                pattern_id, _q_end = read_var_at(chunk, q_after_class)
                slots[int(group_id)] = int(pattern_id)
            except Exception:
                pass
            search_pos = p + 1
        if slots:
            scenes[int(scene_id)] = {
                "scene_id": int(scene_id),
                "name": name,
                "slots": slots,
                "source_offset": pos,
            }
    return scenes


def _compat_extract_sections_166(data: bytes, scenes, patterns_by_id=None):
    """Section class 166 variant; auto-length sections follow the longest Scene pattern."""
    if not scenes:
        return []
    last_scene_pos = max(scene["source_offset"] for scene in scenes.values())
    section_marker = enc_var(166)
    scene_marker = enc_var(155)
    sections = []
    search_pos = last_scene_pos
    while True:
        p = data.find(section_marker, search_pos)
        if p < 0:
            break
        q = p + len(section_marker)
        try:
            first, q1 = read_var_at(data, q)
            section_object_id = None
            if first == 1:
                second, qq = read_var_at(data, q1)
                if [first, second] == [1, 0]:
                    section_object_id, q = read_var_at(data, qq)
            if section_object_id is None:
                section_object_id = first
                q = q1

            if data[q:q + len(scene_marker)] != scene_marker:
                search_pos = p + 1
                continue
            scene_id, q = read_var_at(data, q + len(scene_marker))

            for _ in range(4):
                _value, q = read_var_at(data, q)
            timing = []
            for _ in range(6):
                value, q = read_var_at(data, q)
                timing.append(value)
            if timing[0] != 0 or timing[1] != timing[2] or not (1 <= timing[1] <= 100_000_000):
                search_pos = p + 1
                continue

            # Four object refs are followed by flags. In this family the
            # observed [0,1,1] prefix is Maschine's automatic Scene/Section
            # sizing mode. In that mode the musical section length is the
            # longest Pattern referenced by the Scene, not the 3840-tick UI
            # placeholder stored in the timing tuple.
            for _ in range(4):
                _value, q = read_var_at(data, q)
            flags = []
            for _ in range(3):
                value, q = read_var_at(data, q)
                flags.append(int(value))
            auto_length = flags == [0, 1, 1]

            scene = scenes.get(int(scene_id))
            if scene is None:
                search_pos = p + 1
                continue
            raw_length = int(timing[1])
            effective_length = raw_length
            if auto_length and patterns_by_id:
                pattern_lengths = [
                    int(patterns_by_id[pid]["length"])
                    for pid in scene.get("slots", {}).values()
                    if pid in patterns_by_id
                ]
                if pattern_lengths:
                    effective_length = max(raw_length, max(pattern_lengths))

            sections.append({
                "section_object_id": int(section_object_id),
                "scene_id": int(scene_id),
                "name": scene["name"],
                "length": int(effective_length),
                "serialized_length": raw_length,
                "auto_length": auto_length,
                "previous_length": int(timing[3]),
                "source_offset": p,
            })
        except Exception:
            pass
        search_pos = p + 1
    return sections


def _decode_all_global_float_modulation_events(data: bytes):
    """Decode every structurally valid global FloatModulationEvent vector in the MXPRJ."""
    class_id = _locate_serialized_class_id(data, FLOAT_MOD_EVENT_CLASS)
    if class_id is None:
        return []
    class_name_pos = data.find(FLOAT_MOD_EVENT_CLASS)
    if class_name_pos < 0:
        return []
    class_marker = enc_var(class_id)
    target_field = enc_var(70)
    events = []

    # The single class-defining event immediately after the class name has no
    # ordinary class-ref marker. Preserve it as tick zero when present.
    first_search = class_name_pos + len(FLOAT_MOD_EVENT_CLASS)
    first_target = data.find(target_field, first_search, first_search + 96)
    if first_target >= 0:
        try:
            target_id, q = read_var_at(data, first_target + len(target_field))
            floats = []
            fp = q
            while len(floats) < 2:
                z = data.find(b"\x01\x04", fp, min(len(data), q + 72))
                if z < 0:
                    break
                floats.append(struct.unpack_from("<f", data, z + 2)[0])
                fp = z + 6
            if len(floats) >= 2 and -16.0 <= float(floats[1]) <= 16.0:
                events.append({
                    "tick": 0,
                    "target_id": int(target_id),
                    "value": float(floats[1]),
                    "kind": "float",
                    "storage": "global_vst",
                    "source_offset": first_target,
                })
        except Exception:
            pass

    search_pos = first_search
    while True:
        ref = data.find(class_marker, search_pos)
        if ref < 0:
            break
        search_pos = ref + len(class_marker)

        tick = None
        for start in range(max(first_search, ref - 16), ref):
            try:
                _object_id, q1 = read_var_at(data, start)
                candidate_tick, q2 = read_var_at(data, q1)
                if q2 == ref and 0 <= candidate_tick <= 10_000_000:
                    tick = int(candidate_tick)
            except Exception:
                pass
        if tick is None:
            continue

        field_pos = data.find(target_field, ref, min(len(data), ref + 36))
        if field_pos < 0:
            continue
        try:
            target_id, q = read_var_at(data, field_pos + len(target_field))
            floats = []
            fp = q
            while len(floats) < 2:
                z = data.find(b"\x01\x04", fp, min(len(data), ref + 72))
                if z < 0:
                    break
                floats.append(struct.unpack_from("<f", data, z + 2)[0])
                fp = z + 6
            if len(floats) < 2:
                continue
            value = float(floats[1])
            if not (-16.0 <= value <= 16.0):
                continue
            events.append({
                "tick": tick,
                "target_id": int(target_id),
                "value": value,
                "kind": "float",
                "storage": "global_vst",
                "source_offset": ref,
            })
        except Exception:
            continue

    unique = {}
    for event in events:
        key = (event["tick"], event["kind"], event["target_id"], round(event["value"], 7), event.get("source_offset"))
        unique[key] = event
    return sorted(unique.values(), key=lambda e: (e["source_offset"], e["tick"], e["target_id"]))


def _assign_global_automation_sound_owners(target_metadata, vst3_instruments):
    """Assign wrapper parameter definitions to the enclosing VST3 instrument region."""
    instruments = sorted(
        [x for x in vst3_instruments if x.get("preset_pos") is not None and x.get("sound_id") is not None],
        key=lambda x: int(x["preset_pos"]),
    )
    for meta in target_metadata.values():
        if meta.get("owner") != "SoundPlugin" or meta.get("sound_id") is not None:
            continue
        pos = meta.get("definition_offset")
        if pos is None:
            continue
        chosen = None
        for i, instrument in enumerate(instruments):
            start = int(instrument["preset_pos"])
            end = int(instruments[i + 1]["preset_pos"]) if i + 1 < len(instruments) else len(data) if False else 1 << 62
            if start <= int(pos) < end:
                chosen = instrument
                break
        if chosen is not None:
            meta["sound_id"] = int(chosen["sound_id"])
            meta["ownership_mapping"] = "vst3_preset_region"

def _compat_extract_sections_generic(data: bytes, scenes, patterns_by_id=None):
    """Decode the stable Scene+11 Section family using dynamically inferred Scene ids."""
    if not scenes:
        return []
    scene_classes = {int(s.get("scene_class")) for s in scenes.values() if s.get("scene_class") is not None}
    if len(scene_classes) != 1:
        return []
    scene_class = next(iter(scene_classes))
    section_class = scene_class + 11
    last_scene_pos = max(scene["source_offset"] for scene in scenes.values())
    section_marker = enc_var(section_class)
    scene_marker = enc_var(scene_class)
    sections = []
    search_pos = last_scene_pos
    while True:
        p = data.find(section_marker, search_pos)
        if p < 0:
            break
        q = p + len(section_marker)
        try:
            first, q1 = read_var_at(data, q)
            section_object_id = None
            if first == 1:
                second, qq = read_var_at(data, q1)
                if [first, second] == [1, 0]:
                    section_object_id, q = read_var_at(data, qq)
            if section_object_id is None:
                section_object_id = first
                q = q1

            if data[q:q + len(scene_marker)] != scene_marker:
                search_pos = p + 1
                continue
            scene_id, q = read_var_at(data, q + len(scene_marker))

            for _ in range(4):
                _value, q = read_var_at(data, q)
            timing = []
            for _ in range(6):
                value, q = read_var_at(data, q)
                timing.append(value)
            if timing[0] != 0 or timing[1] != timing[2] or not (1 <= timing[1] <= 100_000_000):
                search_pos = p + 1
                continue

            for _ in range(4):
                _value, q = read_var_at(data, q)
            flags = []
            for _ in range(3):
                value, q = read_var_at(data, q)
                flags.append(int(value))

            scene = scenes.get(int(scene_id))
            if scene is None:
                search_pos = p + 1
                continue

            raw_length = int(timing[1])
            effective_length = raw_length
            auto_length = flags == [0, 1, 1]
            if auto_length and patterns_by_id:
                lengths = [
                    int(patterns_by_id[pid]["length"])
                    for pid in scene.get("slots", {}).values()
                    if pid in patterns_by_id
                ]
                if lengths:
                    effective_length = max(raw_length, max(lengths))

            sections.append({
                "section_object_id": int(section_object_id),
                "scene_id": int(scene_id),
                "name": scene["name"],
                "length": int(effective_length),
                "serialized_length": raw_length,
                "auto_length": auto_length,
                "previous_length": int(timing[3]),
                "source_offset": p,
                "section_class": int(section_class),
            })
        except Exception:
            pass
        search_pos = p + 1
    return sections


def _decode_patterns_scenes_sections(data: bytes, sound_ids):
    """
    Try the original legacy and shifted serialization IDs first, then the
    124/154/165 family used by newer multi-instrument Maschine saves.
    """
    patterns, note_vectors = extract_patterns_for_sound_ids_direct(
        data,
        sound_ids,
    )

    patterns_by_id = {}
    for pattern in patterns:
        pattern_id = _pattern_object_id(data, pattern)
        if pattern_id is None:
            continue
        pattern["pattern_id"] = pattern_id
        patterns_by_id[pattern_id] = pattern

    scenes = extract_scene_map(data)
    sections = extract_arrangement_sections(data, scenes)

    if sections:
        return (
            patterns_by_id,
            note_vectors,
            scenes,
            sections,
            "legacy",
        )

    # Newer shifted serialization-family fallback.
    patterns, note_vectors = _compat_extract_patterns_129(
        data,
        sound_ids,
    )

    patterns_by_id = {}
    for pattern in patterns:
        pattern_id = _compat_pattern_object_id_129(data, pattern)
        if pattern_id is None:
            continue
        pattern["pattern_id"] = pattern_id
        patterns_by_id[pattern_id] = pattern

    scenes = _compat_extract_scenes_188(data)
    sections = _compat_extract_sections_199(data, scenes)

    if sections:
        return (
            patterns_by_id,
            note_vectors,
            scenes,
            sections,
            "shifted",
        )

    # 124/154/165 family. Slot parsing is shared with the generic shifted
    # Pattern scanner; object/Scene/Section class IDs differ.
    patterns, note_vectors = _compat_extract_patterns_129(
        data,
        sound_ids,
    )

    patterns_by_id = {}
    for pattern in patterns:
        pattern_id = _compat_pattern_object_id_124(data, pattern)
        if pattern_id is None:
            continue
        pattern["pattern_id"] = pattern_id
        patterns_by_id[pattern_id] = pattern

    scenes = _compat_extract_scenes_154(data)
    sections = _compat_extract_sections_165(data, scenes)
    if sections:
        return (
            patterns_by_id,
            note_vectors,
            scenes,
            sections,
            "family-124-154-165",
        )

    # Arrangement-enabled variant observed in the 4-instrument stress test.
    scenes = _compat_extract_scenes_155(data)
    sections = _compat_extract_sections_166(data, scenes, patterns_by_id)
    if sections:
        return (
            patterns_by_id,
            note_vectors,
            scenes,
            sections,
            "family-124-155-166",
        )

    # v0.20.3: dynamic named-object fallback.  Newer saves may shift the
    # Pattern/Scene/Section classes while preserving the object relationships.
    # Resolve Pattern ids from their names, reuse the generic Scene parser,
    # and decode the stable Scene+11 Section family.
    dynamic_patterns_by_id = {}
    for pattern in patterns:
        class_id, pattern_id = _infer_named_object_class_and_id(
            data, pattern["source_offset"]
        )
        if pattern_id is None:
            continue
        pattern = dict(pattern)
        pattern["pattern_id"] = int(pattern_id)
        pattern["pattern_class"] = int(class_id) if class_id is not None else None
        dynamic_patterns_by_id[int(pattern_id)] = pattern

    dynamic_scenes = extract_automation_scene_map(data, [])
    dynamic_sections = _compat_extract_sections_generic(
        data, dynamic_scenes, dynamic_patterns_by_id
    )
    if dynamic_sections:
        pclasses = sorted({p.get("pattern_class") for p in dynamic_patterns_by_id.values() if p.get("pattern_class") is not None})
        sclasses = sorted({s.get("scene_class") for s in dynamic_scenes.values() if s.get("scene_class") is not None})
        section_classes = sorted({s.get("section_class") for s in dynamic_sections if s.get("section_class") is not None})
        family = "dynamic-" + "-".join(str(x[0]) if len(x) == 1 else "mixed" for x in (pclasses, sclasses, section_classes))
        return (
            dynamic_patterns_by_id,
            note_vectors,
            dynamic_scenes,
            dynamic_sections,
            family,
        )

    return (
        patterns_by_id,
        note_vectors,
        scenes,
        sections,
        "family-124-155-166",
    )

# ---------------------------------------------------------------------------
# v0.19 VST3 parameter identity probe support
# ---------------------------------------------------------------------------

def _normalize_probe_class_id(value):
    text = str(value or "").strip().upper()
    m = re.search(r"([0-9A-F]{32})", text)
    return m.group(1) if m else ""


def _read_vst3_parameter_identity_probe(path: Path):
    """Parse Plugin_Parameter_Identity_Probe v004-style output."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    class_id = ""
    fx_name = ""
    track_name = ""
    for line in text.splitlines()[:20]:
        if line.startswith("FX:"):
            fx_name = line.split(":", 1)[1].strip()
        elif line.startswith("Track:"):
            track_name = line.split(":", 1)[1].strip()
        if line.lower().startswith("fx_ident:"):
            class_id = _normalize_probe_class_id(line)

    by_id = {}
    rows = []
    for line in text.splitlines():
        # v004 output is tab-separated:
        # INDEX <tab> IDENT <tab> NAME <tab> VALUE <tab> MIN <tab> MAX ...
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].strip().isdigit():
            index = int(parts[0].strip())
            ident = parts[1].strip()
            name = parts[2].strip()
            parameter_value = None
            if len(parts) >= 4:
                try:
                    parameter_value = float(parts[3].strip())
                except Exception:
                    parameter_value = None
        else:
            m = re.match(r"\s*(\d+)\s+([^\s]+)\s+([^\t]+)", line)
            if not m:
                continue
            index = int(m.group(1))
            ident = m.group(2).strip()
            name = m.group(3).strip()
            parameter_value = None

        parameter_formatted = ""
        if len(parts) >= 7:
            parameter_formatted = "\t".join(parts[6:]).strip()

        rows.append({
            "reaper_parameter_index": index,
            "ident": ident,
            "parameter_name": name,
            "parameter_value": parameter_value,
            "parameter_formatted": parameter_formatted,
        })
        m_ident = re.match(r"([0-9]+):([0-9]+)$", ident)
        if not m_ident:
            continue
        ident_index = int(m_ident.group(1))
        parameter_id = int(m_ident.group(2))
        if ident_index != index:
            continue
        by_id[parameter_id] = {
            "reaper_parameter_index": index,
            "parameter_name": name,
            "parameter_value": parameter_value,
            "parameter_formatted": parameter_formatted,
        }

    if not rows:
        return None
    return {
        "path": str(path),
        "class_id": class_id,
        "fx_name": fx_name,
        "track_name": track_name,
        "by_id": by_id,
        "rows": rows,
    }


def _identity_probe_search_dirs(mxprj_path: Path, output_path: Path):
    """Common locations for probe output, deduplicated without deep scanning."""
    candidates = [
        mxprj_path.parent,
        output_path.parent,
        Path(__file__).resolve().parent,
        Path.home() / "Desktop" / "IndexProbes",
        Path.home() / "Documents" / "IndexProbes",
        Path.home() / "Downloads",
    ]
    result = []
    seen = set()
    for folder in candidates:
        try:
            key = str(folder.resolve()).lower()
        except Exception:
            key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        if folder.exists() and folder.is_dir():
            result.append(folder)
    return result


def _load_vst3_identity_probes(mxprj_path: Path, output_path: Path):
    probes = []
    seen = set()
    patterns = ("*Parameter_Identity*.txt", "*parameter_identity*.txt")
    for folder in _identity_probe_search_dirs(mxprj_path, output_path):
        for pattern in patterns:
            try:
                paths = list(folder.glob(pattern))
            except Exception:
                paths = []
            for path in paths:
                try:
                    key = str(path.resolve()).lower()
                except Exception:
                    key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                probe = _read_vst3_parameter_identity_probe(path)
                if probe:
                    probes.append(probe)
    return probes



# Some NI wrapper controls represent a plug-in-defined macro page rather than
# serializing the underlying VST3 parameter ID beside every automation target.
# Keep these bridges class-scoped and page-scoped: never map raw MXPRJ target
# IDs. The Massive Macros 1..8 alignment was validated independently in REAPER:
# macro N is native REAPER parameter N (parameter 0 is the hidden "Default").
VST3_WRAPPER_PAGE_IDENTITY_SCHEMAS = {
    "5653544E694D616D6173736976650000": {
        "macros": {
            "minimum_index": 1,
            "maximum_index": 8,
            "reaper_parameter_offset": 0,
        },
    },
}


def _recover_vst3_identity_from_wrapper_page(meta, instrument, probes):
    """
    Recover a native VST3 parameter identity when Maschine stores only a
    validated NI wrapper page/control ordinal (for example Massive Macros 1..8).

    The page schema selects a REAPER parameter row, then the identity probe's
    IDENT field supplies the stable native VST3 parameter ID. Downstream routing
    still uses the normal VST3-ID resolver and KK host translation.
    """
    page = str(meta.get("page") or "").strip().lower()
    page_index = meta.get("parameter_index")
    if not page or not isinstance(page_index, int):
        return None

    class_id = _normalize_probe_class_id(instrument.get("device_id"))
    class_schema = VST3_WRAPPER_PAGE_IDENTITY_SCHEMAS.get(class_id, {})
    schema = class_schema.get(page)
    if not schema:
        return None

    lo = int(schema.get("minimum_index", 1))
    hi = int(schema.get("maximum_index", 0))
    if not (lo <= int(page_index) <= hi):
        return None

    wanted_reaper_index = int(page_index) + int(schema.get("reaper_parameter_offset", 0))
    candidates = []
    for probe in probes:
        if probe.get("class_id") != class_id:
            continue
        for row in probe.get("rows", []):
            if int(row.get("reaper_parameter_index", -1)) != wanted_reaper_index:
                continue
            ident = str(row.get("ident") or "")
            m = re.fullmatch(r"(\d+):(\d+)", ident)
            if not m or int(m.group(1)) != wanted_reaper_index:
                continue
            candidates.append((probe, row, int(m.group(2))))

    if not candidates:
        return None

    # Multiple copies of the same class probe may be present in search paths;
    # they are equivalent for this exact row. Prefer the first stable result.
    probe, row, native_id = candidates[0]
    meta["vst3_parameter_id"] = int(native_id)
    meta["wrapper_page_identity_mapping"] = "class_scoped_page_index_via_identity_probe"
    meta["wrapper_page"] = str(meta.get("page") or "")
    meta["wrapper_page_parameter_index"] = int(page_index)
    meta["wrapper_page_reaper_parameter_index"] = int(wanted_reaper_index)
    meta["wrapper_page_probe_parameter_name"] = row.get("parameter_name", "")
    meta["wrapper_page_identity_probe"] = probe.get("path", "")
    return probe



KONTAKT7_CLASS_ID = "5653544E694B376B6F6E74616B742037"


def _kontakt_probe_row_native_id(row):
    """Return the native VST3 ID from a normal v004 INDEX:ID row."""
    index = int(row.get("reaper_parameter_index", -1))
    ident = str(row.get("ident") or "")
    m = re.fullmatch(r"(\d+):(\d+)", ident)
    if not m or int(m.group(1)) != index:
        return None
    return int(m.group(2))


def _probe_value_alignment_score(metas, rows, start):
    """
    Score one possible alignment between a sequential Maschine wrapper-control
    run and a contiguous set of REAPER probe rows.
    """
    score = 0.0
    close_matches = 0
    usable = 0
    for offset, meta in enumerate(metas):
        row = rows[start + offset]
        base = meta.get("base_value")
        value = row.get("parameter_value")
        if isinstance(base, (int, float)) and isinstance(value, (int, float)):
            usable += 1
            diff = abs(float(base) - float(value))
            if diff <= 1.0e-5:
                score += 4.0
                close_matches += 1
            elif diff <= 0.005:
                score += 3.0
                close_matches += 1
            elif diff <= 0.03:
                score += 1.0
            elif diff > 0.25:
                score -= 1.0

        name = str(row.get("parameter_name") or "")
        if name and not re.fullmatch(r"#\d+", name):
            score += 0.25

        if _kontakt_probe_row_native_id(row) is None:
            score -= 8.0

    return score, close_matches, usable


def _recover_kontakt_ordered_control_runs(
    target_metadata,
    vst3_instruments,
    probes,
):
    """
    AB04: Recover native identities for Kontakt wrapper/NKS control runs that
    omit the VST3 ID beside individual Maschine automation targets.

    Mapping is not based on preset names or raw target IDs. It uses:
      - one unresolved +5 target-ID run within a Kontakt Sound;
      - the exact Track:-matched Kontakt identity probe;
      - a unique contiguous probe-row alignment scored by restored static
        parameter values;
      - the stable INDEX:VST3_ID identity from each matched probe row.
    """
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }

    by_sound = {}
    for meta in (target_metadata or {}).values():
        if meta.get("owner") != "SoundPlugin":
            continue
        if isinstance(meta.get("vst3_parameter_id"), int):
            continue
        sound_id = meta.get("sound_id")
        if sound_id is None:
            continue
        instrument = instruments_by_sound.get(int(sound_id))
        if instrument is None:
            continue
        if _normalize_probe_class_id(instrument.get("device_id")) != KONTAKT7_CLASS_ID:
            continue
        if meta.get("ownership_mapping") != "vst3_preset_region":
            continue
        by_sound.setdefault(int(sound_id), []).append(meta)

    recovered = []
    for sound_id, metas in by_sound.items():
        instrument = instruments_by_sound.get(sound_id)
        track_name = str(instrument.get("track_name") or "").strip().lower()

        exact_probes = [
            probe for probe in probes
            if probe.get("class_id") == KONTAKT7_CLASS_ID
            and str(probe.get("track_name") or "").strip().lower() == track_name
        ]
        if len(exact_probes) != 1:
            continue
        probe = exact_probes[0]
        rows = sorted(
            probe.get("rows", []),
            key=lambda row: int(row.get("reaper_parameter_index", -1)),
        )

        metas = sorted(
            metas,
            key=lambda meta: (
                int(meta.get("target_id", -1)),
                int(meta.get("definition_offset", -1)),
            ),
        )

        runs = []
        current = []
        for meta in metas:
            if (
                current
                and int(meta.get("target_id", -1))
                    != int(current[-1].get("target_id", -1)) + 5
            ):
                if len(current) >= 4:
                    runs.append(current)
                current = []
            current.append(meta)
        if len(current) >= 4:
            runs.append(current)

        for run in runs:
            n = len(run)
            if n > len(rows):
                continue

            scored = []
            for start in range(0, len(rows) - n + 1):
                score, close_matches, usable = _probe_value_alignment_score(
                    run, rows, start
                )
                scored.append((score, close_matches, usable, start))
            scored.sort(reverse=True)
            if not scored:
                continue

            score, close_matches, usable, start = scored[0]
            runner_up_score = scored[1][0] if len(scored) > 1 else -999.0

            minimum_close = max(3, n // 2)
            if usable < minimum_close:
                continue
            if close_matches < minimum_close:
                continue
            if score < (2.0 * n):
                continue
            if score - runner_up_score < max(4.0, 0.5 * n):
                continue

            candidate_rows = rows[start:start + n]
            native_ids = [_kontakt_probe_row_native_id(row) for row in candidate_rows]
            if any(native_id is None for native_id in native_ids):
                continue

            for meta, row, native_id in zip(run, candidate_rows, native_ids):
                meta["vst3_parameter_id"] = int(native_id)
                meta["kontakt_ordered_control_mapping"] = (
                    "track_probe_value_fingerprint"
                )
                meta["kontakt_probe_run_start"] = int(start)
                meta["kontakt_probe_run_length"] = int(n)
                meta["kontakt_probe_alignment_score"] = float(score)
                meta["kontakt_probe_alignment_margin"] = float(
                    score - runner_up_score
                )
                meta["kontakt_probe_parameter_name"] = str(
                    row.get("parameter_name") or ""
                )
                meta["kontakt_probe_parameter_index"] = int(
                    row.get("reaper_parameter_index", -1)
                )
                meta["kontakt_identity_probe"] = probe.get("path", "")

                # Some Kontakt/NKS wrapper lanes are stored as signed centered
                # movement even though the wrapper's static base sits exactly
                # at the normalized lower boundary. Ordinary base+offset would
                # collapse every negative point to zero. Preserve the same
                # centered interpretation that the carrier fallback used, but
                # now on the real plug-in parameter.
                base = meta.get("base_value")
                lane_min = meta.get("lane_value_min")
                lane_max = meta.get("lane_value_max")
                if (
                    meta.get("value_mode") == "offset"
                    and isinstance(base, (int, float))
                    and isinstance(lane_min, (int, float))
                    and isinstance(lane_max, (int, float))
                    and float(base) <= 1.0e-6
                    and float(lane_min) < -1.0e-6
                    and float(lane_max) <= 1.0e-6
                ):
                    meta["value_mode"] = "carrier_centered"
                    meta["direct_value_mapping"] = (
                        "signed_centered_lower_boundary_safeguard"
                    )

                recovered.append(meta)

    return recovered



AUTOMATION_ROUTE_PROFILE_FILENAMES = (
    "Maschine_Automation_Route_Profiles.json",
    "*Automation_Route_Profiles*.json",
)


def _load_automation_route_profiles(mxprj_path: Path, output_path: Path):
    """
    Load reusable automation-destination profiles from the same practical
    locations as parameter-identity probes.

    These profiles contain only mappings that have been explicitly verified in
    the destination instrument. They let the converter reuse a confirmed
    Kontakt/NKS -> MIDI-CC route without baking track names or MXPRJ target IDs
    into the Python code.
    """
    profiles = []
    source_files = []
    seen_paths = set()
    seen_profile_ids = set()

    for folder in _identity_probe_search_dirs(mxprj_path, output_path):
        candidates = []
        for pattern in AUTOMATION_ROUTE_PROFILE_FILENAMES:
            try:
                candidates.extend(folder.glob(pattern))
            except Exception:
                pass

        for path in candidates:
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)

            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            file_profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
            if not isinstance(file_profiles, list):
                continue

            accepted = 0
            for profile in file_profiles:
                if not isinstance(profile, dict):
                    continue
                profile_id = str(profile.get("profile_id") or "").strip()
                class_id = _normalize_probe_class_id(profile.get("plugin_class_id"))
                signature = profile.get("probe_signature") or {}
                bindings = profile.get("bindings") or []
                names = signature.get("parameter_names") or []
                if (
                    not profile_id
                    or not class_id
                    or profile_id in seen_profile_ids
                    or not isinstance(names, list)
                    or not names
                    or not isinstance(bindings, list)
                    or not bindings
                ):
                    continue

                normalized = dict(profile)
                normalized["plugin_class_id"] = class_id
                normalized["profile_source"] = str(path)
                profiles.append(normalized)
                seen_profile_ids.add(profile_id)
                accepted += 1

            if accepted:
                source_files.append(str(path))

    return profiles, source_files


def _probe_signature_matches(profile, probe):
    signature = profile.get("probe_signature") or {}
    wanted_names = [
        _normalize_probe_parameter_name(name)
        for name in (signature.get("parameter_names") or [])
    ]
    if not wanted_names or not all(wanted_names):
        return False

    rows = sorted(
        probe.get("rows", []),
        key=lambda row: int(row.get("reaper_parameter_index", -1)),
    )
    start = int(signature.get("start_index", 0))
    if start < 0 or start + len(wanted_names) > len(rows):
        return False

    actual_names = [
        _normalize_probe_parameter_name(rows[start + i].get("parameter_name"))
        for i in range(len(wanted_names))    ]
    return actual_names == wanted_names


def _exact_track_identity_probe(instrument, probes):
    class_id = _normalize_probe_class_id(instrument.get("device_id"))
    track_name = str(instrument.get("track_name") or "").strip().lower()
    matches = [
        probe for probe in (probes or [])
        if probe.get("class_id") == class_id
        and str(probe.get("track_name") or "").strip().lower() == track_name
    ]
    return matches[0] if len(matches) == 1 else None


def _apply_automation_route_profiles(
    target_metadata,
    vst3_instruments,
    probes,
    profiles,
):
    """
    AB04 reusable route-profile layer.

    A profile first identifies an instrument by VST3 class ID plus an exact
    parameter-name signature from that instance's track-matched identity probe.
    Bindings then select the already-resolved native VST3 parameter identity and
    can redirect it to a verified MIDI CC destination.

    No track name, preset name or raw MXPRJ automation target ID is used as the
    persistent mapping key.
    """
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }

    applied = []
    for sound_id, instrument in instruments_by_sound.items():
        class_id = _normalize_probe_class_id(instrument.get("device_id"))
        probe = _exact_track_identity_probe(instrument, probes)
        if probe is None:
            continue

        matching_profiles = [
            profile for profile in (profiles or [])
            if profile.get("plugin_class_id") == class_id
            and _probe_signature_matches(profile, probe)
        ]
        if len(matching_profiles) != 1:
            continue
        profile = matching_profiles[0]

        sound_metas = [
            meta for meta in (target_metadata or {}).values()
            if meta.get("owner") == "SoundPlugin"
            and meta.get("sound_id") is not None
            and int(meta.get("sound_id")) == int(sound_id)
        ]

        for binding in profile.get("bindings", []):
            if str(binding.get("route") or "").strip().lower() != "midi_cc":
                continue
            try:
                cc_number = int(binding.get("cc_number"))
            except Exception:
                continue
            if not 0 <= cc_number <= 127:
                continue

            wanted_native_id = binding.get("native_vst3_parameter_id")
            try:
                wanted_native_id = int(wanted_native_id)
            except Exception:
                wanted_native_id = None
            wanted_name = _normalize_probe_parameter_name(binding.get("parameter_name"))

            candidates = []
            for meta in sound_metas:
                native_id = meta.get("vst3_parameter_id")
                meta_name = _normalize_probe_parameter_name(
                    meta.get("probe_parameter_name")
                    or meta.get("kontakt_probe_parameter_name")
                    or meta.get("name")
                )
                native_match = (
                    isinstance(wanted_native_id, int)
                    and isinstance(native_id, int)
                    and int(native_id) == int(wanted_native_id)
                )
                name_match = bool(wanted_name and meta_name == wanted_name)
                if native_match or (wanted_native_id is None and name_match):
                    candidates.append(meta)

            # Never guess if a profile binding is ambiguous in the current
            # project instance.
            if len(candidates) != 1:
                continue
            meta = candidates[0]

            meta["midi_cc_number"] = cc_number
            meta["midi_cc_mapping"] = "verified_route_profile"
            meta["midi_cc_profile_id"] = profile.get("profile_id", "")
            meta["midi_cc_profile_source"] = profile.get("profile_source", "")
            meta["midi_cc_track_name"] = instrument.get("track_name", "")
            meta["midi_cc_parameter_name"] = (
                meta.get("probe_parameter_name")
                or meta.get("kontakt_probe_parameter_name")
                or meta.get("name")
                or ""
            )
            if binding.get("value_mapping"):
                meta["midi_cc_value_mapping"] = str(binding.get("value_mapping"))

            applied.append({
                "sound_id": int(sound_id),
                "track_name": instrument.get("track_name", ""),
                "parameter_name": meta["midi_cc_parameter_name"],
                "target_id": meta.get("target_id"),
                "native_vst3_parameter_id": meta.get("vst3_parameter_id"),
                "cc_number": cc_number,
                "mapping": "verified_route_profile",
                "profile_id": profile.get("profile_id", ""),
                "profile_source": profile.get("profile_source", ""),
            })

    return applied


def _find_midi_cc_route_candidates(target_metadata, vst3_instruments):
    """
    Flag Kontakt/NKS lanes whose source value domain makes a direct host
    parameter route suspicious.

    This does NOT invent a CC number. It only produces diagnostics so a future
    mapping can be verified once in Kontakt and saved as a route profile.
    """
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }
    candidates = []
    for meta in (target_metadata or {}).values():
        sound_id = meta.get("sound_id")
        if sound_id is None or int(sound_id) not in instruments_by_sound:
            continue
        instrument = instruments_by_sound[int(sound_id)]
        if _normalize_probe_class_id(instrument.get("device_id")) != KONTAKT7_CLASS_ID:
            continue
        if isinstance(meta.get("midi_cc_number"), int):
            continue

        safeguard = str(meta.get("direct_value_mapping") or "")
        if safeguard != "signed_centered_lower_boundary_safeguard":
            continue

        candidates.append({
            "sound_id": int(sound_id),
            "track_name": instrument.get("track_name", ""),
            "target_id": meta.get("target_id"),
            "native_vst3_parameter_id": meta.get("vst3_parameter_id"),
            "reaper_parameter_index": meta.get("reaper_parameter_index"),
            "parameter_name": (
                meta.get("probe_parameter_name")
                or meta.get("kontakt_probe_parameter_name")
                or meta.get("name")
                or ""
            ),
            "reason": "signed_centered_lane_at_normalized_lower_boundary",
            "action": "verify Kontakt internal MIDI-CC assignment before rerouting",
        })
    return candidates




REAKTOR6_CLASS_ID = "5653544E6952367265616B746F722036"
MASSIVE_CLASS_ID = "5653544E694D616D6173736976650000"


def _probe_native_id(row):
    index = int(row.get("reaper_parameter_index", -1))
    ident = str(row.get("ident") or "")
    m = re.fullmatch(r"(\d+):(\d+)", ident)
    if not m or int(m.group(1)) != index:
        return None
    return int(m.group(2))


def _plus5_runs(metas, minimum=4):
    metas = sorted(
        metas,
        key=lambda meta: (
            int(meta.get("target_id", -1)),
            int(meta.get("definition_offset", -1)),
        ),
    )
    runs = []
    current = []
    for meta in metas:
        if current and int(meta.get("target_id", -1)) != int(current[-1].get("target_id", -1)) + 5:
            if len(current) >= minimum:
                runs.append(current)
            current = []
        current.append(meta)
    if len(current) >= minimum:
        runs.append(current)
    return runs


def _exact_track_probe(probes, track_name, class_id=None, require_kk=False):
    wanted_track = str(track_name or "").strip().lower()
    matches = []
    for probe in probes:
        if str(probe.get("track_name") or "").strip().lower() != wanted_track:
            continue
        if class_id and probe.get("class_id") != class_id:
            continue
        is_kk = "komplete kontrol" in str(probe.get("fx_name") or "").lower()
        if require_kk and not is_kk:
            continue
        matches.append(probe)
    return matches[0] if len(matches) == 1 else None


def _recover_reaktor_eventlist_value_runs(target_metadata, vst3_instruments, probes):
    """Recover state-specific Reaktor controls from exact track-probe values."""
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }
    by_sound = {}
    for meta in (target_metadata or {}).values():
        if meta.get("owner") != "SoundPlugin" or isinstance(meta.get("vst3_parameter_id"), int):
            continue
        sid = meta.get("sound_id")
        if sid is None or int(sid) not in instruments_by_sound:
            continue
        inst = instruments_by_sound[int(sid)]
        if _normalize_probe_class_id(inst.get("device_id")) != REAKTOR6_CLASS_ID:
            continue
        if meta.get("ownership_mapping") != "event_list_sound_slot":
            continue
        by_sound.setdefault(int(sid), []).append(meta)

    used = []
    for sid, metas in by_sound.items():
        inst = instruments_by_sound[sid]
        probe = _exact_track_probe(probes, inst.get("track_name"), REAKTOR6_CLASS_ID)
        if probe is None:
            continue
        rows = sorted(probe.get("rows", []), key=lambda r: int(r.get("reaper_parameter_index", -1)))
        mapped_any = False
        for run in _plus5_runs(metas, minimum=4):
            n = len(run)
            if n > len(rows):
                continue
            scored = []
            for start in range(0, len(rows) - n + 1):
                score, close, usable = _probe_value_alignment_score(run, rows, start)
                scored.append((score, close, usable, start))
            scored.sort(reverse=True)
            if not scored:
                continue
            score, close, usable, start = scored[0]
            runner = scored[1][0] if len(scored) > 1 else -999.0
            if usable < n or close < n:
                continue
            if score - runner < max(6.0, float(n)):
                continue
            candidate_rows = rows[start:start+n]
            native_ids = [_probe_native_id(row) for row in candidate_rows]
            if any(v is None for v in native_ids):
                continue
            for meta, row, native_id in zip(run, candidate_rows, native_ids):
                meta["vst3_parameter_id"] = int(native_id)
                meta["reaper_parameter_index"] = int(row["reaper_parameter_index"])
                meta["probe_parameter_name"] = str(row.get("parameter_name") or "")
                meta["name"] = meta["probe_parameter_name"] or meta.get("name", "")
                meta["parameter_mapping"] = "track_probe_exact_value_run"
                meta["identity_probe"] = probe.get("path", "")
                meta["track_probe_alignment_start"] = int(start)
                meta["track_probe_alignment_score"] = float(score)
                meta["track_probe_alignment_margin"] = float(score - runner)
            mapped_any = True
        if mapped_any:
            used.append(probe)
    return used


def _formatted_unit_candidates(text):
    text = str(text or "").strip()
    values = []
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*%", text):
        values.append(float(m.group(1)) / 100.0)
    if not values and re.search(r"[A-Za-z]", text) and "|" in text:
        for m in re.finditer(r"(?<![.\d])(\d+(?:\.\d+)?)(?![.\d])", text):
            v = float(m.group(1))
            if 0.0 <= v <= 100.0:
                values.append(v / 100.0)
    return values


def _recover_kontakt_sectioned_eventlist_run(target_metadata, vst3_instruments, probes):
    """Recover a verified Kontakt Layers/Macros/Master wrapper layout."""
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }
    by_sound = {}
    for meta in (target_metadata or {}).values():
        if meta.get("owner") != "SoundPlugin":
            continue
        sid = meta.get("sound_id")
        if sid is None or int(sid) not in instruments_by_sound:
            continue
        inst = instruments_by_sound[int(sid)]
        if _normalize_probe_class_id(inst.get("device_id")) != KONTAKT7_CLASS_ID:
            continue
        if meta.get("ownership_mapping") != "event_list_sound_slot":
            continue
        by_sound.setdefault(int(sid), []).append(meta)

    wanted_names = ["attack", "release", "reso", "width", "reverb", "40", "balance", "volume"]
    order = [6, 0, 1, 2, 3, 4, 5, 7]
    used = []

    for sid, metas in by_sound.items():
        inst = instruments_by_sound[sid]
        probe = _exact_track_probe(probes, inst.get("track_name"), KONTAKT7_CLASS_ID)
        if probe is None:
            continue
        rows = sorted(probe.get("rows", []), key=lambda r: int(r.get("reaper_parameter_index", -1)))
        signature_start = None
        for start in range(0, max(0, len(rows) - 7)):
            names = [_normalize_probe_parameter_name(r.get("parameter_name")) for r in rows[start:start+8]]
            if names == wanted_names:
                signature_start = start
                break
        if signature_start is None:
            continue
        sig_rows = rows[signature_start:signature_start+8]

        for run in _plus5_runs(metas, minimum=8):
            if len(run) != 8:
                continue
            labels = [str(m.get("control_label") or "").strip().lower() for m in run]
            if not (labels[0] == "layers" and labels[1] == "macros" and labels[7] == "master"):
                continue
            verified = 0
            for meta, row_pos in zip(run, order):
                row = sig_rows[row_pos]
                base = meta.get("base_value")
                if not isinstance(base, (int, float)):
                    continue
                candidates = []
                raw = row.get("parameter_value")
                if isinstance(raw, (int, float)):
                    candidates.append(float(raw))
                candidates.extend(_formatted_unit_candidates(row.get("parameter_formatted")))
                if candidates and min(abs(float(base) - v) for v in candidates) <= 0.01:
                    verified += 1
            if verified < 7:
                continue
            ok = True
            for meta, row_pos in zip(run, order):
                row = sig_rows[row_pos]
                native_id = _probe_native_id(row)
                if native_id is None:
                    ok = False
                    break
                meta["vst3_parameter_id"] = int(native_id)
                meta["reaper_parameter_index"] = int(row["reaper_parameter_index"])
                meta["probe_parameter_name"] = str(row.get("parameter_name") or "")
                meta["name"] = meta["probe_parameter_name"] or meta.get("name", "")
                meta["parameter_mapping"] = "kontakt_sectioned_wrapper_probe_signature"
                meta["identity_probe"] = probe.get("path", "")
                meta["kontakt_wrapper_section_order"] = "Layers / Macros / Master"
                meta["kontakt_probe_signature_start"] = int(signature_start)
                # v0.20.4: this verified Kontakt wrapper page stores Pattern
                # automation as modulation deltas around each control's saved
                # base value. Positive-only lanes (notably Release and Width)
                # were previously misclassified as absolute normalized values.
                meta["value_mode"] = "offset"
            if ok:
                used.append(probe)
                break
    return used


def _recover_massive_hosted_eventlist_macros(target_metadata, vst3_instruments, probes):
    """Recover an 8-control Massive macro page from its exact KK track probe."""
    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in (vst3_instruments or [])
        if item.get("sound_id") is not None
    }
    by_sound = {}
    for meta in (target_metadata or {}).values():
        if meta.get("owner") != "SoundPlugin":
            continue
        if isinstance(meta.get("vst3_parameter_id"), int) or isinstance(meta.get("reaper_parameter_index"), int):
            continue
        sid = meta.get("sound_id")
        if sid is None or int(sid) not in instruments_by_sound:
            continue
        inst = instruments_by_sound[int(sid)]
        if _normalize_probe_class_id(inst.get("device_id")) != MASSIVE_CLASS_ID:
            continue
        if meta.get("ownership_mapping") != "event_list_sound_slot":
            continue
        by_sound.setdefault(int(sid), []).append(meta)

    used = []
    for sid, metas in by_sound.items():
        inst = instruments_by_sound[sid]
        probe = _exact_track_probe(probes, inst.get("track_name"), require_kk=True)
        if probe is None:
            continue
        rows = sorted(probe.get("rows", []), key=lambda r: int(r.get("reaper_parameter_index", -1)))
        by_index = {int(r.get("reaper_parameter_index", -1)): r for r in rows}

        for run in _plus5_runs(metas, minimum=8):
            if len(run) != 8:
                continue
            labels = [str(m.get("control_label") or "").strip().lower() for m in run]
            if not (labels[0] == "macros" and labels[7] == "eq / master"):
                continue
            candidates = []
            for row in rows:
                idx = int(row.get("reaper_parameter_index", -1))
                name = _normalize_probe_parameter_name(row.get("parameter_name"))
                if name != "default":
                    continue
                macro_rows = [by_index.get(idx + k) for k in range(1, 9)]
                if any(r is None for r in macro_rows):
                    continue
                close = 0
                for meta, mr in zip(run, macro_rows):
                    base = meta.get("base_value")
                    value = mr.get("parameter_value")
                    if isinstance(base, (int, float)) and isinstance(value, (int, float)) and abs(float(base) - float(value)) <= 0.005:
                        close += 1
                candidates.append((close, idx, macro_rows))
            candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            if not candidates or candidates[0][0] < 6:
                continue
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                continue
            close, default_index, macro_rows = candidates[0]
            for ordinal, (meta, row) in enumerate(zip(run, macro_rows), start=1):
                meta["vst3_parameter_id"] = int(ordinal - 1)
                meta["reaper_parameter_index"] = int(ordinal)
                meta["komplete_kontrol_parameter_index"] = int(row["reaper_parameter_index"])
                pname = re.sub(r"^\d+\s+", "", str(row.get("parameter_name") or "")).strip()
                meta["probe_parameter_name"] = pname
                meta["kk_track_probe_parameter_name"] = pname
                meta["name"] = pname or meta.get("name", "")
                meta["parameter_mapping"] = "massive_macro_run_to_track_kk_probe"
                meta["identity_probe"] = probe.get("path", "")
                meta["komplete_kontrol_probe"] = probe.get("path", "")
                meta["massive_macro_ordinal"] = int(ordinal)
                meta["massive_kk_default_index"] = int(default_index)
            used.append(probe)
            break
    return used


def _apply_generic_vst3_automation_identity_map(
    target_metadata,
    vst3_instruments,
    mxprj_path: Path,
    output_path: Path,
):
    """
    Translate MXPRJ-native VST3 parameter IDs to REAPER parameter indices.

    Matching is primarily by VST3 class ID from the restored plug-in instance;
    plug-in-name matching is only a fallback for older probe output. Returns a
    diagnostic list of probe files that contributed at least one mapping.
    """
    probes = _load_vst3_identity_probes(mxprj_path, output_path)

    helper_used_probes = []
    helper_used_probes.extend(_recover_reaktor_eventlist_value_runs(
        target_metadata, vst3_instruments, probes
    ))
    helper_used_probes.extend(_recover_kontakt_sectioned_eventlist_run(
        target_metadata, vst3_instruments, probes
    ))
    helper_used_probes.extend(_recover_massive_hosted_eventlist_macros(
        target_metadata, vst3_instruments, probes
    ))

    _recover_kontakt_ordered_control_runs(
        target_metadata,
        vst3_instruments,
        probes,
    )

    instruments_by_sound = {
        int(item["sound_id"]): item
        for item in vst3_instruments
        if item.get("sound_id") is not None
    }
    used = {}
    for probe in helper_used_probes:
        if not probe or not probe.get("path"):
            continue
        used[probe["path"]] = {
            "path": probe.get("path", ""),
            "class_id": probe.get("class_id", ""),
            "fx_name": probe.get("fx_name", ""),
            "track_name": probe.get("track_name", ""),
        }

    for meta in (target_metadata or {}).values():
        if meta.get("owner") != "SoundPlugin":
            continue
        native_id = meta.get("vst3_parameter_id")
        sound_id = meta.get("sound_id")
        if sound_id is None:
            continue
        instrument = instruments_by_sound.get(int(sound_id))
        if instrument is None:
            continue

        if not isinstance(native_id, int):
            page_probe = _recover_vst3_identity_from_wrapper_page(
                meta, instrument, probes
            )
            native_id = meta.get("vst3_parameter_id")
            if page_probe is not None:
                used[page_probe["path"]] = {
                    "path": page_probe["path"],
                    "class_id": page_probe.get("class_id", ""),
                    "fx_name": page_probe.get("fx_name", ""),
                }

        if not isinstance(native_id, int):
            continue

        wanted_class = _normalize_probe_class_id(instrument.get("device_id"))
        plugin_name = str(instrument.get("plugin_name") or "").strip().lower()
        instrument_track = str(instrument.get("track_name") or "").strip().lower()
        matches = []
        for probe in probes:
            class_match = bool(wanted_class and probe.get("class_id") == wanted_class)
            name_match = bool(plugin_name and plugin_name in str(probe.get("fx_name", "")).lower())
            probe_track = str(probe.get("track_name") or "").strip().lower()
            track_match = bool(instrument_track and probe_track and probe_track == instrument_track)
            if class_match or (not probe.get("class_id") and name_match):
                entry = probe["by_id"].get(native_id)
                if entry is not None:
                    matches.append({
                        "probe": probe,
                        "entry": entry,
                        "class_match": class_match,
                        "track_match": track_match,
                    })

        if not matches:
            continue

        # AB01: Kontakt (and other dynamic hosts) can expose different parameter
        # layouts for two instances of the same VST3 class. Prefer a probe whose
        # Track: field exactly matches the restored destination track. If there
        # is no exact contextual match, only accept multiple class-matched probes
        # when they unanimously resolve this native VST3 ID to the same REAPER
        # parameter index. Otherwise preserve the lane on the carrier rather than
        # risk binding automation to the wrong control.
        exact = [m for m in matches if m["class_match"] and m["track_match"]]
        if exact:
            chosen = exact[0]
        else:
            class_matches = [m for m in matches if m["class_match"]]
            pool = class_matches if class_matches else matches
            resolved_indices = {int(m["entry"]["reaper_parameter_index"]) for m in pool}
            if len(pool) > 1 and len(resolved_indices) > 1:
                meta["identity_probe_ambiguity"] = [
                    {
                        "path": m["probe"].get("path", ""),
                        "track_name": m["probe"].get("track_name", ""),
                        "reaper_parameter_index": int(m["entry"]["reaper_parameter_index"]),
                        "parameter_name": m["entry"].get("parameter_name", ""),
                    }
                    for m in pool
                ]
                continue
            chosen = pool[0]

        probe = chosen["probe"]
        entry = chosen["entry"]
        meta["reaper_parameter_index"] = int(entry["reaper_parameter_index"])
        if meta.get("wrapper_page_identity_mapping"):
            meta["parameter_mapping"] = "wrapper_page_to_vst3_identity_probe"
        else:
            meta["parameter_mapping"] = "generic_vst3_identity_probe"
        meta["probe_parameter_name"] = entry.get("parameter_name", "")
        meta["identity_probe"] = probe["path"]
        if entry.get("parameter_name"):
            meta["name"] = str(entry["parameter_name"])
        used[probe["path"]] = {
            "path": probe["path"],
            "class_id": probe.get("class_id", ""),
            "fx_name": probe.get("fx_name", ""),
            "track_name": probe.get("track_name", ""),
        }

    return list(used.values())


def _normalize_probe_parameter_name(name):
    text = str(name or "").strip().lower()
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _discover_kk_hosted_parameter_maps(probes):
    """Discover native REAPER-index -> Komplete Kontrol hosted-index translations."""
    kk_probes = [p for p in probes if "komplete kontrol" in str(p.get("fx_name", "")).lower()]
    native_probes = [p for p in probes if p not in kk_probes and p.get("rows")]
    discovered = []
    for native in native_probes:
        native_rows = sorted(native.get("rows", []), key=lambda r: r["reaper_parameter_index"])
        native_names = [_normalize_probe_parameter_name(r.get("parameter_name")) for r in native_rows]
        best = None
        for kk in kk_probes:
            kk_rows = sorted(kk.get("rows", []), key=lambda r: r["reaper_parameter_index"])
            kk_names = [_normalize_probe_parameter_name(r.get("parameter_name")) for r in kk_rows]
            for n_start in range(min(8, len(native_names))):
                if not native_names[n_start]:
                    continue
                for k_start, kk_name in enumerate(kk_names):
                    if kk_name != native_names[n_start]:
                        continue
                    run = 0
                    while (n_start + run < len(native_names) and k_start + run < len(kk_names)
                           and native_names[n_start + run]
                           and native_names[n_start + run] == kk_names[k_start + run]):
                        run += 1
                    if run < 6:
                        continue
                    offset = int(kk_rows[k_start]["reaper_parameter_index"]) - int(native_rows[n_start]["reaper_parameter_index"])
                    candidate = (run, -n_start, -k_start, offset, kk)
                    if best is None or candidate[:3] > best[:3]:
                        best = candidate
        if best is None:
            continue
        run, _neg_ns, _neg_ks, offset, kk = best
        discovered.append({
            "native_probe": native.get("path", ""),
            "native_class_id": native.get("class_id", ""),
            "native_fx_name": native.get("fx_name", ""),
            "kk_probe": kk.get("path", ""),
            "kk_fx_name": kk.get("fx_name", ""),
            "parameter_index_offset": int(offset),
            "matched_parameter_run": int(run),
        })
    return discovered


def _apply_kk_host_parameter_maps(target_metadata, vst3_instruments, probes):
    maps = _discover_kk_hosted_parameter_maps(probes)
    by_class = {m.get("native_class_id"): m for m in maps if m.get("native_class_id")}
    instruments_by_sound = {int(item["sound_id"]): item for item in vst3_instruments if item.get("sound_id") is not None}
    used = {}
    for meta in (target_metadata or {}).values():
        native_reaper = meta.get("reaper_parameter_index")
        sound_id = meta.get("sound_id")
        if not isinstance(native_reaper, int) or sound_id is None:
            continue
        instrument = instruments_by_sound.get(int(sound_id))
        if not instrument:
            continue
        class_id = _normalize_probe_class_id(instrument.get("device_id"))
        mapping = by_class.get(class_id)
        if not mapping:
            continue
        meta["komplete_kontrol_parameter_index"] = int(native_reaper) + int(mapping["parameter_index_offset"])
        meta["komplete_kontrol_parameter_offset"] = int(mapping["parameter_index_offset"])
        meta["komplete_kontrol_mapping"] = "probe_sequence_alignment"
        meta["komplete_kontrol_probe"] = mapping.get("kk_probe", "")
        if meta.get("kk_track_probe_parameter_name"):
            meta["name"] = str(meta.get("kk_track_probe_parameter_name"))
            meta["probe_parameter_name"] = str(meta.get("kk_track_probe_parameter_name"))
        used[class_id] = mapping
    return list(used.values())


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def extract_and_convert(mxprj_path: Path, template_path: Path, output_path: Path):
    mx = mxprj_path.read_bytes()

    samplers = scan_maschine_samplers(mx)
    vst3_instruments = scan_embedded_vst3_instruments(mx)
    external_vst3_fx_not_attempted = scan_embedded_vst3_fx_devices(mx)

    if not samplers and not vst3_instruments:
        raise RuntimeError(
            "No supported Maschine Sampler Sounds or external VST3 instruments were detected."
        )

    for sampler in samplers:
        sampler["kind"] = "sampler"
        sampler["sound_id"] = _find_sampler_sound_id(
            mx,
            sampler["relative_path"],
        )
        sampler["midi_items"] = []
        sampler["midi_note_count"] = 0

    for instrument in vst3_instruments:
        instrument["midi_items"] = []
        instrument["midi_note_count"] = 0

    instruments = samplers + vst3_instruments
    sound_ids = [
        item.get("sound_id")
        for item in instruments
        if item.get("sound_id") is not None
    ]

    rpp_text = template_path.read_text(encoding="utf-8")
    template_tempo = _template_tempo(rpp_text)
    tempo = _mxprj_tempo(mx) or template_tempo
    rpp_lines = _set_project_tempo_line(rpp_text.splitlines(), tempo)

    track_start, track_end = _find_first_top_level_track(rpp_lines)
    template_track = rpp_lines[track_start:track_end + 1]

    # v0.20.1: the template track is a structural/state shell only. Never
    # inherit its song material into a converted MXPRJ. Strip every existing
    # MIDI/media ITEM and parameter envelope before cloning any destination
    # track. The converter inserts only MIDI/automation decoded from the
    # current Maschine project.
    template_track = _remove_track_blocks(
        template_track,
        ("<ITEM", "<PARMENV"),
    )

    # v0.20.1: a template may contain many existing tracks.  Only the first
    # top-level track is used as the cloning/state shell; ALL top-level tracks
    # from the template must be removed before writing converted tracks.
    # Previously the suffix began immediately after the first template track,
    # which accidentally copied every later template track into the output.
    top_level_track_spans = []
    scan = 0
    while scan < len(rpp_lines):
        if not rpp_lines[scan].startswith("  <TRACK "):
            scan += 1
            continue
        indent = rpp_lines[scan][:len(rpp_lines[scan]) - len(rpp_lines[scan].lstrip())]
        end_token = indent + ">"
        end = scan + 1
        while end < len(rpp_lines) and rpp_lines[end] != end_token:
            end += 1
        if end >= len(rpp_lines):
            raise RuntimeError("Template contains an unterminated top-level track.")
        top_level_track_spans.append((scan, end))
        scan = end + 1

    if not top_level_track_spans:
        raise RuntimeError("The template RPP does not contain a top-level track.")

    first_track_start = top_level_track_spans[0][0]
    last_track_end = top_level_track_spans[-1][1]
    prefix = rpp_lines[:first_track_start]
    suffix = rpp_lines[last_track_end + 1:]

    (
        _check_lines,
        _wrapper_indices,
        _state_indices,
        _metadata_indices,
        _wrapper,
        _state,
    ) = extract_rpp_kk_state("\n".join(template_track))

    # ------------------------------------------------------------
    # Pattern / Scene / Arranger decoding, including shifted-ID saves.
    # ------------------------------------------------------------
    try:
        (
            patterns_by_id,
            note_vectors,
            scenes,
            sections,
            serialization_family,
        ) = _decode_patterns_scenes_sections(
            mx,
            sound_ids,
        )
    except RuntimeError as exc:
        # Some projects serialize NoteEvent vectors in a family not yet handled
        # by the MIDI decoder. Automation extraction is independent, so keep
        # the conversion useful rather than discarding a valid automation test.
        patterns_by_id = {}
        note_vectors = {}
        scenes = {}
        sections = []
        serialization_family = f"automation-only fallback: {exc}"

    if not sections:
        # v0.12: keep conversion alive for Pattern-only/diagnostic projects.
        # MIDI arrangement still requires Sections, but automation can be
        # imported from Pattern-local timing using the fallback expander.
        sections = []

    instrument_by_sound = {
        item["sound_id"]: item
        for item in instruments
        if item.get("sound_id") is not None
    }

    timeline_tick = 0

    for section in sections:
        section["timeline_tick"] = timeline_tick
        scene = scenes.get(section["scene_id"])

        if scene is not None:
            for _group_id, pattern_id in scene["slots"].items():
                if not pattern_id:
                    continue

                pattern = patterns_by_id.get(pattern_id)
                if pattern is None:
                    continue

                for sound_id, vector_id in pattern["slots"].items():
                    if not vector_id:
                        continue

                    instrument = instrument_by_sound.get(sound_id)
                    if instrument is None:
                        continue

                    events = note_vectors.get(vector_id)
                    if not events:
                        continue

                    identity = (
                        f"{mxprj_path.resolve()}:"
                        f"section:{section['section_object_id']}:"
                        f"sound:{sound_id}:"
                        f"pattern:{pattern_id}"
                    )

                    item_lines = _build_arranged_midi_item_lines(
                        section,
                        pattern,
                        events,
                        timeline_tick,
                        tempo,
                        identity,
                        sound_id,
                    )

                    instrument["midi_items"].append(item_lines)
                    instrument["midi_note_count"] += len(events)

        timeline_tick += int(section["length"])

    # ------------------------------------------------------------
    # v0.19 automation extraction, native VST3 identity mapping, Scene ownership and Group routing.
    # ------------------------------------------------------------
    pattern_automation = extract_pattern_automation(mx)
    eventlist_pattern_automation = []
    if not pattern_automation and str(serialization_family).startswith("dynamic-"):
        eventlist_pattern_automation = _compat_extract_pattern_eventlist_automation_offset6(
            mx, patterns_by_id
        )
        if eventlist_pattern_automation:
            pattern_automation = eventlist_pattern_automation

    all_global_sound_automation = _decode_all_global_float_modulation_events(mx)
    use_global_absolute_automation = bool(all_global_sound_automation) and not pattern_automation
    if use_global_absolute_automation:
        # Keep every vector rather than dropping the project merely because
        # multiple named Patterns make ownership ambiguous. These vectors carry
        # absolute project ticks in the stress-test serialization family.
        pattern_automation = [{
            "source_offset": min(e.get("source_offset", 0) for e in all_global_sound_automation),
            "name": "Global VST automation",
            "length": max([timeline_tick] + [int(e["tick"]) + 1 for e in all_global_sound_automation]),
            "pattern_class": None,
            "pattern_id": None,
            "automation_events": all_global_sound_automation,
        }]
    automation_target_metadata = resolve_automation_targets(mx, pattern_automation)

    # v0.20.3 mixed EventLists are referenced directly from one Sound slot, so
    # they provide stronger automation ownership evidence than a backwards
    # binary search around the parameter definition.  Promote a target to that
    # Sound only when every occurrence agrees on the same Sound id.
    if eventlist_pattern_automation:
        target_sound_hints = {}
        for pattern in eventlist_pattern_automation:
            for event in pattern.get("automation_events", []):
                sound_id = event.get("sound_id_hint")
                if sound_id is None:
                    continue
                key = (event.get("kind", "float"), int(event["target_id"]))
                target_sound_hints.setdefault(key, set()).add(int(sound_id))
        for key, owners in target_sound_hints.items():
            if len(owners) != 1:
                continue
            meta = automation_target_metadata.get(key)
            if meta is None:
                continue
            meta["sound_id"] = next(iter(owners))
            meta["owner"] = "SoundPlugin"
            meta["ownership_mapping"] = "event_list_sound_slot"

    if use_global_absolute_automation:
        _assign_global_automation_sound_owners(automation_target_metadata, vst3_instruments)

    # Reaktor-hosted instruments can serialize their NKS/parameter-page graph
    # far away from the Sound object, beyond the local backwards ownership
    # search used for ordinary VST3s. When the project contains exactly one
    # external VST3 instrument, unresolved SoundPlugin automation can be
    # assigned to that Sound without ambiguity.
    if len(vst3_instruments) == 1:
        sole_vst_sound_id = vst3_instruments[0].get("sound_id")
        if sole_vst_sound_id is not None:
            for meta in automation_target_metadata.values():
                if meta.get("owner") == "SoundPlugin" and meta.get("sound_id") is None:
                    meta["sound_id"] = int(sole_vst_sound_id)
                    meta["ownership_mapping"] = "single_vst3_sound_fallback"

    identity_probes_used = _apply_generic_vst3_automation_identity_map(
        automation_target_metadata,
        vst3_instruments,
        mxprj_path,
        output_path,
    )
    all_identity_probes = _load_vst3_identity_probes(mxprj_path, output_path)
    automation_route_profiles, automation_route_profile_files = (
        _load_automation_route_profiles(mxprj_path, output_path)
    )
    midi_cc_bindings = _apply_automation_route_profiles(
        automation_target_metadata,
        vst3_instruments,
        all_identity_probes,
        automation_route_profiles,
    )
    midi_cc_route_candidates = _find_midi_cc_route_candidates(
        automation_target_metadata,
        vst3_instruments,
    )
    kk_host_maps_used = _apply_kk_host_parameter_maps(
        automation_target_metadata,
        vst3_instruments,
        all_identity_probes,
    )

    # For unresolved carrier-only lanes, preserve visible raw movement even
    # when an inferred offset mode plus an edge base value would clamp the
    # entire diagnostic lane to a constant. Direct plug-in routing is never
    # affected by this fallback; it is only a reversible carrier display.
    if use_global_absolute_automation:
        for key, meta in automation_target_metadata.items():
            if meta.get("owner") != "SoundPlugin":
                continue
            if isinstance(meta.get("reaper_parameter_index"), int):
                continue
            lane_events = [
                e for e in all_global_sound_automation
                if e["kind"] == key[0] and int(e["target_id"]) == key[1]
            ]
            raw_values = [round(float(e.get("value", 0.0)), 9) for e in lane_events]
            if len(set(raw_values)) < 2:
                continue
            converted = [
                round(_maschine_mod_value_to_unit(e["value"], e["kind"], meta), 9)
                for e in lane_events
            ]
            if len(set(converted)) < 2:
                meta["value_mode"] = "carrier_centered"
                meta["carrier_value_mapping"] = "centered_raw_fallback"

    automation_scenes = extract_automation_scene_map(mx, pattern_automation) if not use_global_absolute_automation else {}
    automation_scene_map = dict(scenes)
    automation_scene_map.update(automation_scenes)
    automation_pattern_groups = _automation_pattern_group_map(
        pattern_automation,
        automation_scene_map,
    ) if not use_global_absolute_automation else {}

    if use_global_absolute_automation:
        expanded_automation = _expand_global_vst_automation_to_pattern_occurrences(            all_global_sound_automation,
            automation_target_metadata,
            scenes,
            sections,
            patterns_by_id,
            tempo,
        )
        if not expanded_automation:
            expanded_automation = []
            for event in all_global_sound_automation:
                meta = automation_target_metadata.get((event["kind"], int(event["target_id"])), {})
                sound_id = meta.get("sound_id")
                group_id = _group_for_sound(sound_id, sorted(_group_ids_from_scenes(scenes))) if sound_id is not None else None
                expanded_automation.append({
                    **event,
                    "pattern_id": None,
                    "pattern_name": "Global VST automation",
                    "group_id": group_id,
                    "absolute_tick": int(event["tick"]),
                    "time": _ticks_to_seconds(int(event["tick"]), tempo),
                    "automation_expansion": "absolute_fallback",
                    **({"sound_id": int(sound_id)} if sound_id is not None else {}),
                })
    else:
        expanded_automation = _expand_pattern_automation_to_arrangement(
            pattern_automation,
            automation_scene_map,
            sections,
            tempo,
            automation_pattern_groups,
        )

    automation_events_by_group = {}
    automation_events_by_sound = {}
    unresolved_automation = []
    for event in expanded_automation:
        meta = automation_target_metadata.get((event["kind"], int(event["target_id"])), {})
        sound_id = meta.get("sound_id")
        if sound_id is not None:
            event["sound_id"] = int(sound_id)
            automation_events_by_sound.setdefault(int(sound_id), []).append(event)
            continue
        group_id = event.get("group_id")
        if group_id is None:
            unresolved_automation.append(event)
        else:
            automation_events_by_group.setdefault(int(group_id), []).append(event)

    # Target ids are globally unique in the observed MXPRJ serialization. Add
    # the resolved Group owner(s) to the metadata/report when possible.
    for key, meta in automation_target_metadata.items():
        owner_groups = sorted({
            int(event["group_id"])
            for event in expanded_automation
            if event.get("group_id") is not None
            and event["kind"] == key[0]
            and int(event["target_id"]) == key[1]
        })
        if owner_groups:
            meta["group_ids"] = owner_groups
            if len(owner_groups) == 1:
                meta["group_id"] = owner_groups[0]

    midi_cc_automation_routes = _apply_midi_cc_automation_to_instrument_items(
        instruments,
        automation_events_by_sound,
        automation_target_metadata,
    )

    # ------------------------------------------------------------
    # Associate supported instruments with the populated source Groups.
    # ------------------------------------------------------------
    group_ids = sorted(
        set(_group_ids_from_scenes(scenes))
        | set(_group_ids_from_scenes(automation_scene_map))
        | set(automation_events_by_group)
    )

    groups = {}
    group_order = []

    for index, group_id in enumerate(group_ids):
        groups[group_id] = {
            "group_id": group_id,
            "slot_index": index,
            "slot_label": _group_slot_label(index),
            "folder_name": _group_slot_label(index),
            "instruments": [],
            "automation_events": list(automation_events_by_group.get(group_id, [])),
        }
        group_order.append(group_id)

    ungrouped = []

    for instrument in instruments:
        group_id = _group_for_sound(
            instrument.get("sound_id"),
            group_ids,
        )
        instrument["group_id"] = group_id

        if group_id in groups:
            groups[group_id]["instruments"].append(instrument)
        else:
            ungrouped.append(instrument)

    populated_groups = []

    for group_id in group_order:
        group = groups[group_id]
        members = sorted(
            group["instruments"],
            key=lambda item: (
                item.get("sound_id") is None,
                item.get("sound_id") or 0,
            ),
        )

        if not members:
            continue

        group["instruments"] = members
        populated_groups.append(group)

    routing_profile = _maltodextrin2_routing_profile(
        mx,
        len(populated_groups),
    )

    # The confirmed Maltodextrin2 structure has five populated source Groups.
    # Use their real Maschine slot/name labels in order.
    if routing_profile is not None:
        for group, (slot, name) in zip(
            populated_groups,
            routing_profile["source_groups"],
        ):
            group["slot_label"] = slot
            group["folder_name"] = f"{slot} {name}"
            for instrument in group["instruments"]:
                instrument["group_slot"] = slot
                instrument["group_name"] = name
    else:
        # Safe fallback for projects whose routing has not yet been decoded.
        for group in populated_groups:
            slot = group["slot_label"].replace("Group ", "")
            group["slot_label"] = slot
            group["folder_name"] = f"{slot} Group"
            for instrument in group["instruments"]:
                instrument["group_slot"] = slot
                instrument["group_name"] = "Group"

    # ------------------------------------------------------------
    # Prepare all instrument tracks.
    # ------------------------------------------------------------
    source_key = str(mxprj_path.resolve())
    prepared_tracks = {}

    for index, sampler in enumerate(samplers):
        track_lines = _clone_template_track(
            template_track,
            sampler,
            index,
            source_key,
        )

        track_lines = _insert_midi_items(
            track_lines,
            sampler["midi_items"],
        )

        # User defaults: every instrument -3 dB and record-disarmed.
        track_lines = _set_track_volume(
            track_lines,
            INSTRUMENT_VOL_MINUS_3DB,
        )
        track_lines = _set_track_record_disarmed(track_lines)

        blank = not sampler["midi_items"]
        sampler["muted_by_default"] = blank
        sampler["fx_offline_by_default"] = False

        if blank:
            # Empty sampler Sounds must remain genuinely MIDI-empty.  Earlier
            # builds inserted a one-note VST wake item here, which is harmless
            # while muted but contaminates an otherwise faithful MIDI export.
            # Keep the restored sampler host online and mute the unused track
            # without inventing any MIDI data.
            track_lines = _set_track_muted(track_lines, True)
            track_lines = _set_first_fx_offline(track_lines, False)

        prepared_tracks[id(sampler)] = track_lines

    for index, instrument in enumerate(vst3_instruments):
        kk_host_shell = _find_kk_host_shell(
            instrument,
            mxprj_path,
            template_path,
            output_path,
        )

        if kk_host_shell is not None:
            track_lines = _clone_kk_host_track_for_vst3(
                kk_host_shell,
                instrument,
                source_key,
                index,
            )
        else:
            track_lines = _clone_template_track_for_vst3(
                template_track,
                instrument,
                source_key,
                index,
            )
            instrument["host_mode"] = "native_vst3"
            instrument["kk_host_shell"] = None

        track_lines = _insert_midi_items(
            track_lines,
            instrument["midi_items"],
        )

        track_lines = _set_track_volume(
            track_lines,
            INSTRUMENT_VOL_MINUS_3DB,
        )
        track_lines = _set_track_record_disarmed(track_lines)

        blank = not instrument["midi_items"]
        instrument["muted_by_default"] = blank
        instrument["fx_offline_by_default"] = False

        if blank:
            # Keep empty VST3 instruments online. The track remains muted to
            # avoid an audible test note, while the MIDI item forces the plug-in
            # to instantiate instead of opening offline/lazy.
            wake_item = _build_vst_wake_midi_item(
                tempo,
                f"{source_key}:vst3:{instrument.get('sound_id', index)}",
            )
            track_lines = _insert_midi_items(track_lines, [wake_item])
            track_lines = _set_track_muted(track_lines, True)
            track_lines = _set_first_fx_offline(track_lines, False)

        prepared_tracks[id(instrument)] = track_lines

    # v0.19: route resolved Sound/VST automation directly onto the owning instrument
    # track. The carrier is appended after the restored instrument plug-in so
    # its envelopes can later be copied/remapped to any desired destination FX.
    sound_automation_lane_maps = {}
    for instrument in instruments:
        sound_id = instrument.get("sound_id")
        events = automation_events_by_sound.get(sound_id, []) if sound_id is not None else []
        if not events:
            continue
        track_lines = prepared_tracks[id(instrument)]
        consumed = set()
        track_lines, consumed = _insert_plugin_direct_automation(
            track_lines,
            events,
            source_key,
            f"sound:{sound_id}",
            automation_target_metadata,
            instrument.get("plugin_name", ""),
        )

        fallback_events = [
            e for e in events
            if (e["kind"], int(e["target_id"])) not in consumed
            and _events_have_meaningful_movement(
                [x for x in events if x["kind"] == e["kind"] and int(x["target_id"]) == int(e["target_id"])],
                automation_target_metadata.get((e["kind"], int(e["target_id"])), {}),
            )
        ]
        lane_map = {}
        if fallback_events:
            track_lines, lane_map = _insert_automation_carrier_into_track(
                track_lines,
                fallback_events,
                source_key,
                f"sound:{sound_id}",
                automation_target_metadata,
            )
        prepared_tracks[id(instrument)] = track_lines
        sound_automation_lane_maps[int(sound_id)] = lane_map

    # ------------------------------------------------------------
    # Build source Group folders first.
    # ------------------------------------------------------------
    logical_tracks = []
    # Each entry: {"kind":..., "key":..., "lines":...}
    automation_destinations = []
    represented_automation_groups = set()

    for instrument in instruments:
        sound_id = instrument.get("sound_id")
        lane_map = sound_automation_lane_maps.get(sound_id) if sound_id is not None else None
        if lane_map:
            automation_destinations.append({
                "group_id": instrument.get("group_id"),
                "group_slot": instrument.get("group_slot"),
                "sound_id": sound_id,
                "track_key": f"instrument:{id(instrument)}",
                "track_name": instrument.get("track_name", instrument.get("plugin_name", "Instrument")),
                "lane_map": lane_map,
            })

    def record_automation_destination(group, track_key, track_name, lane_map):
        if not lane_map:
            return
        automation_destinations.append({
            "group_id": group.get("group_id") if group else None,
            "group_slot": (
                group.get("slot_label", "").replace("Group ", "")
                if group else None
            ),
            "track_key": track_key,
            "track_name": track_name,
            "lane_map": lane_map,
        })

    for group in populated_groups:
        folder_lines = _build_group_folder_track(
            group["folder_name"],
            source_key,
            group["group_id"],
        )
        folder_lines = _set_track_volume(folder_lines, 1.0)
        folder_lines = _set_track_record_disarmed(folder_lines)

        # When the routing profile is known, the source Group should not also
        # go to REAPER master; it feeds its dedicated A2-E2 bus instead.
        if routing_profile is not None:
            folder_lines = _set_track_main_send(
                folder_lines,
                False,
            )

        if group.get("automation_events"):
            folder_lines, lane_map = _insert_automation_carrier_into_track(
                folder_lines,
                group["automation_events"],
                source_key,
                f"group:{group['group_id']}",
                automation_target_metadata,
            )
            represented_automation_groups.add(group["group_id"])
            record_automation_destination(
                group,
                group["slot_label"],
                group["folder_name"],
                lane_map,
            )

        logical_tracks.append({
            "kind": "source_group",
            "key": group["slot_label"],
            "group": group,
            "lines": folder_lines,
        })

        for member_index, instrument in enumerate(group["instruments"]):
            track_lines = prepared_tracks[id(instrument)]
            track_lines = _set_track_folder_end(
                track_lines,
                member_index == len(group["instruments"]) - 1,
            )

            logical_tracks.append({
                "kind": "instrument",
                "key": f"instrument:{id(instrument)}",
                "instrument": instrument,
                "lines": track_lines,
            })

    # Defensive fallback for supported instruments with no resolved Group.
    for instrument in sorted(
        ungrouped,
        key=lambda item: item.get("sound_id") or 0,
    ):
        logical_tracks.append({
            "kind": "instrument",
            "key": f"instrument:{id(instrument)}",
            "instrument": instrument,
            "lines": prepared_tracks[id(instrument)],
        })

    # ------------------------------------------------------------
    # Build the A2-E2 FX buses and H2 SUBMix. Automation belonging to those
    # Maschine Groups is placed directly on the matching REAPER bus track.
    # ------------------------------------------------------------
    group_by_slot = {
        group["slot_label"].replace("Group ", ""): group
        for group in groups.values()
    }

    if routing_profile is not None:
        for slot, name in routing_profile["fx_buses"]:
            bus_lines = _build_bus_track(
                f"{slot} {name}",
                source_key,
                slot,
                False,
            )
            bus_lines = _set_track_volume(bus_lines, 1.0)
            bus_lines = _set_track_record_disarmed(bus_lines)

            owner_group = group_by_slot.get(slot)
            if owner_group and owner_group.get("automation_events"):
                bus_lines, lane_map = _insert_automation_carrier_into_track(
                    bus_lines,
                    owner_group["automation_events"],
                    source_key,
                    f"group:{owner_group['group_id']}",
                    automation_target_metadata,
                )
                represented_automation_groups.add(owner_group["group_id"])
                record_automation_destination(
                    owner_group,
                    slot,
                    f"{slot} {name}",
                    lane_map,
                )

            logical_tracks.append({
                "kind": "fx_bus",
                "key": slot,
                "lines": bus_lines,
            })

        submix_slot, submix_name = routing_profile["submix"]
        submix_lines = _build_bus_track(
            f"{submix_slot} {submix_name}",
            source_key,
            submix_slot,
            True,
        )
        submix_lines = _set_track_volume(submix_lines, 1.0)
        submix_lines = _set_track_record_disarmed(submix_lines)

        owner_group = group_by_slot.get(submix_slot)
        if owner_group and owner_group.get("automation_events"):
            submix_lines, lane_map = _insert_automation_carrier_into_track(
                submix_lines,
                owner_group["automation_events"],
                source_key,
                f"group:{owner_group['group_id']}",
                automation_target_metadata,
            )
            represented_automation_groups.add(owner_group["group_id"])
            record_automation_destination(
                owner_group,
                submix_slot,
                f"{submix_slot} {submix_name}",
                lane_map,
            )

        logical_tracks.append({
            "kind": "submix",
            "key": submix_slot,
            "lines": submix_lines,
        })

        # Track indices are zero-based and include folder parents and children.
        track_index_by_key = {
            entry["key"]: index
            for index, entry in enumerate(logical_tracks)
        }

        # A1->A2, B1->B2, ... E1->E2
        for (source_slot, _source_name), (bus_slot, _bus_name) in zip(
            routing_profile["source_groups"],
            routing_profile["fx_buses"],
        ):
            source_index = track_index_by_key[source_slot]

            for entry in logical_tracks:
                if entry["key"] == bus_slot:
                    entry["lines"] = _insert_auxrecv(
                        entry["lines"],
                        source_index,
                    )
                    break

        # A2-E2 -> H2
        submix_entry = next(
            entry
            for entry in logical_tracks
            if entry["key"] == submix_slot
        )

        for bus_slot, _bus_name in routing_profile["fx_buses"]:
            source_index = track_index_by_key[bus_slot]
            submix_entry["lines"] = _insert_auxrecv(
                submix_entry["lines"],
                source_index,
            )

    # Automation can belong to a Group that currently has no supported
    # instrument/audio track and is not one of the known routing buses. Keep it
    # useful by creating a correctly named Group-level carrier track.
    for group_id in group_order:
        group = groups[group_id]
        if not group.get("automation_events"):
            continue
        if group_id in represented_automation_groups:
            continue

        slot = group["slot_label"].replace("Group ", "")
        track_name = f"{slot} Group — MASCHINE AUTOMATION"
        carrier_group_lines = _build_bus_track(
            track_name,
            source_key,
            f"automation-{slot}",
            True,
        )
        carrier_group_lines = _set_track_record_disarmed(carrier_group_lines)
        carrier_group_lines, lane_map = _insert_automation_carrier_into_track(
            carrier_group_lines,
            group["automation_events"],
            source_key,
            f"group:{group_id}",
            automation_target_metadata,
        )
        represented_automation_groups.add(group_id)
        record_automation_destination(
            group,
            f"automation-group:{slot}",
            track_name,
            lane_map,
        )
        logical_tracks.append({
            "kind": "automation_group",
            "key": f"automation-group:{slot}",
            "group": group,
            "lines": carrier_group_lines,
        })

    # Only genuinely unresolved events retain a separate fallback carrier.
    unresolved_carrier_lines, unresolved_lane_map = _build_automation_carrier_track(
        unresolved_automation,
        source_key,
        automation_target_metadata,
    )
    if unresolved_carrier_lines:
        logical_tracks.append({
            "kind": "automation_carrier",
            "key": "automation_carrier",
            "lines": unresolved_carrier_lines,
        })
        automation_destinations.append({
            "group_id": None,
            "group_slot": None,
            "track_key": "automation_carrier",
            "track_name": "MASCHINE AUTOMATION (unresolved)",
            "lane_map": unresolved_lane_map,
        })

    output_tracks = []
    for entry in logical_tracks:
        output_tracks.extend(entry["lines"])

    output_lines = prefix + output_tracks + suffix
    output_path.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )

    automation_jsfx_path = output_path.with_name(AUTOMATION_CARRIER_NAME)
    if expanded_automation:
        _write_automation_carrier_jsfx(automation_jsfx_path)

    flattened_automation_lanes = []
    for destination in automation_destinations:
        for key, lane in sorted(
            destination["lane_map"].items(),
            key=lambda pair: pair[1],
        ):
            flattened_automation_lanes.append({
                "lane": lane,
                "kind": key[0],
                "target_id": key[1],
                "target_hex": f"0x{key[1]:04X}",
                "group_id": destination.get("group_id"),
                "group_slot": destination.get("group_slot"),
                "sound_id": destination.get("sound_id"),
                "track_key": destination["track_key"],
                "track_name": destination["track_name"],
                "resolved": automation_target_metadata.get(key, {}),
            })

    not_attempted = _build_not_attempted_report(
        mx,
        external_vst3_fx_not_attempted,
        routing_profile,
    )

    automation_report_path = output_path.with_suffix(".automation.json")
    report = {
        "source": str(mxprj_path),
        "tempo": tempo,
        "mapping": "v0.20.3 stable: validated AB04 data-driven automation route profiles. Verified Kontakt/NKS MIDI-CC routes are matched by VST3 class + track-specific parameter signature + native VST3 parameter identity. Unknown suspicious lanes are reported but never guessed.",
        "automation_route_profile_files": automation_route_profile_files,
        "midi_cc_bindings": midi_cc_bindings,
        "midi_cc_route_candidates": midi_cc_route_candidates,
        "midi_cc_automation_routes": midi_cc_automation_routes,
        "identity_probes_used": identity_probes_used,
        "komplete_kontrol_host_maps": kk_host_maps_used,
        "not_attempted": not_attempted,
        "vst3_hosting": [
            {
                "track_name": item.get("track_name", ""),
                "plugin_name": item.get("plugin_name", ""),
                "device_id": item.get("device_id", ""),
                "sound_id": item.get("sound_id"),
                "host_mode": item.get("host_mode", "native_vst3"),
                "kk_host_shell": item.get("kk_host_shell"),
            }
            for item in vst3_instruments
        ],
        "patterns": [
            {
                "name": item["name"],
                "pattern_id": item.get("pattern_id"),
                "pattern_class": item.get("pattern_class"),
                "length_ticks": item["length"],
                "events": item["automation_events"],
            }
            for item in pattern_automation
        ],
        "automation_scenes": list(automation_scene_map.values()),
        "pattern_group_map": automation_pattern_groups,
        "lanes": flattened_automation_lanes,
        "destinations": [
            {
                "group_id": destination.get("group_id"),
                "group_slot": destination.get("group_slot"),
                "sound_id": destination.get("sound_id"),
                "track_key": destination["track_key"],
                "track_name": destination["track_name"],
                "lane_count": len(destination["lane_map"]),
            }
            for destination in automation_destinations
        ],
        "expanded_events": [
            {
                **event,
                "reaper_value": _maschine_mod_value_to_unit(
                    event["value"],
                    event["kind"],
                    automation_target_metadata.get(
                        (event["kind"], int(event["target_id"]))
                    ),
                ),
            }
            for event in expanded_automation
        ],
    }
    automation_report_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    tracks_with_midi = [
        item for item in instruments
        if item["midi_items"]
    ]
    blank_instruments = [
        item for item in instruments
        if not item["midi_items"]
    ]

    total_items = sum(
        len(item["midi_items"])
        for item in instruments
    )
    total_notes = sum(
        item["midi_note_count"]
        for item in instruments
    )

    return {
        "samplers": samplers,
        "vst3": vst3_instruments,
        "kk_hosted_vst3": sum(1 for item in vst3_instruments if item.get("host_mode") == "komplete_kontrol"),
        "native_vst3": sum(1 for item in vst3_instruments if item.get("host_mode") != "komplete_kontrol"),
        "patterns": len(patterns_by_id),
        "note_vectors": len(note_vectors),
        "scenes": len(scenes),
        "sections": len(sections),
        "serialization_family": serialization_family,
        "arrangement_ticks": timeline_tick,
        "arrangement_bars_4_4": timeline_tick / 3840.0,
        "tempo": tempo,
        "midi_items": total_items,
        "midi_notes": total_notes,
        "tracks_with_midi": len(tracks_with_midi),
        "blank_instruments": len(blank_instruments),
        "groups_created": populated_groups,
        "routing_profile": routing_profile,
        "ungrouped_instruments": len(ungrouped),
        "automation_patterns": len(pattern_automation),
        "automation_events": len(expanded_automation),
        "automation_lanes": len(flattened_automation_lanes),
        "automation_destinations": len(automation_destinations),
        "identity_probes_used": identity_probes_used,
        "komplete_kontrol_host_maps": kk_host_maps_used,
        "automation_jsfx": str(automation_jsfx_path) if expanded_automation else None,
        "automation_report": str(automation_report_path),
        "external_vst3_fx_not_attempted": external_vst3_fx_not_attempted,
        "not_attempted": not_attempted,
    }


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x620")
        self.minsize(720, 520)

        self.mxprj_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a Maschine project to begin.")
        self.detected_samplers = []

        self._build_ui()
        self._load_settings()
        saved = self.mxprj_var.get().strip()
        if saved and Path(saved).is_file():
            self._preview_source(saved)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="Maschine → REAPER Converter",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            header,
            text="v0.20.5 — v0.20.4 stable conversion engine + explicit not-attempted report",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(self, padding=(16, 8, 16, 16))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(5, weight=1)

        # Source MXPRJ
        ttk.Label(body, text="1. Maschine project (.mxprj):").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        self.mx_entry = ttk.Entry(body, textvariable=self.mxprj_var)
        self.mx_entry.grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(body, text="Browse…", command=self.choose_mxprj).grid(
            row=0, column=2, padx=(8, 0), pady=6
        )

        # Template RPP
        ttk.Label(body, text="2. KK template (.rpp):").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(body, textvariable=self.template_var).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(body, text="Browse…", command=self.choose_template).grid(
            row=1, column=2, padx=(8, 0), pady=6
        )

        # Output RPP
        ttk.Label(body, text="3. Output project (.rpp):").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(body, textvariable=self.output_var).grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Button(body, text="Save As…", command=self.choose_output).grid(
            row=2, column=2, padx=(8, 0), pady=6
        )

        # Prominent selected-source display
        source_box = ttk.LabelFrame(body, text="TEST SOURCE", padding=10)
        source_box.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        source_box.columnconfigure(0, weight=1)
        self.source_display = ttk.Label(
            source_box,
            text="No MXPRJ selected",
            font=("Segoe UI", 11, "bold"),
        )
        self.source_display.grid(row=0, column=0, sticky="w")

        # Buttons
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        ttk.Button(
            buttons,
            text="Convert MXPRJ → RPP",
            command=self.convert_clicked,
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Clear Log",
            command=lambda: self.log.delete("1.0", tk.END),
        ).pack(side="left", padx=(8, 0))

        # Log
        log_frame = ttk.LabelFrame(body, text="Conversion log", padding=6)
        log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = tk.Text(
            log_frame,
            wrap="word",
            height=16,
            font=("Consolas", 10),
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        ttk.Label(
            body,
            textvariable=self.status_var,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.mxprj_var.trace_add("write", self._on_source_changed)

    def _settings(self):
        return {
            "mxprj": self.mxprj_var.get(),
            "template": self.template_var.get(),
            "output": self.output_var.get(),
        }

    def _load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                self.mxprj_var.set(data.get("mxprj", ""))
                self.template_var.set(data.get("template", ""))
                self.output_var.set(data.get("output", ""))
        except Exception:
            pass

    def _save_settings(self):
        try:
            SETTINGS_FILE.write_text(
                json.dumps(self._settings(), indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _initial_dir(self, value):
        try:
            p = Path(value)
            if p.exists():
                return str(p.parent if p.is_file() else p)
        except Exception:
            pass
        return str(Path.home())

    def choose_mxprj(self):
        path = filedialog.askopenfilename(
            title="Choose Maschine project",
            initialdir=self._initial_dir(self.mxprj_var.get()),
            filetypes=[
                ("Maschine projects", "*.mxprj"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self.mxprj_var.set(path)

        # Short, explicit output filename.
        src = Path(path)
        stem = re.sub(r"(?i)^maschine[ _-]+", "", src.stem)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")
        if not stem:
            stem = "Project"
        suggested = src.with_name(f"{stem}_v0205.rpp")
        self.output_var.set(str(suggested))
        self._preview_source(path)
        self._save_settings()

    def choose_template(self):
        path = filedialog.askopenfilename(
            title="Choose REAPER Komplete Kontrol template",
            initialdir=self._initial_dir(self.template_var.get()),
            filetypes=[
                ("REAPER projects", "*.rpp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.template_var.set(path)
            self._save_settings()

    def choose_output(self):
        initial = self.output_var.get()
        p = Path(initial) if initial else Path("Project_v0194.rpp")

        path = filedialog.asksaveasfilename(
            title="Save converted REAPER project as",
            initialdir=str(p.parent) if str(p.parent) else str(Path.home()),
            initialfile=p.name,
            defaultextension=".rpp",
            filetypes=[
                ("REAPER projects", "*.rpp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.output_var.set(path)
            self._save_settings()

    def _on_source_changed(self, *_):
        value = self.mxprj_var.get().strip()
        if value:
            self.source_display.config(text=Path(value).name)
        else:
            self.detected_samplers = []
            self.source_display.config(text="No MXPRJ selected")

    def _preview_source(self, path):
        try:
            p = Path(path)
            if not p.is_file():
                return
            raw = p.read_bytes()
            self.detected_samplers = scan_maschine_samplers(raw)
            detected_vst3 = scan_embedded_vst3_instruments(raw)

            sampler_count = len(self.detected_samplers)
            vst_count = len(detected_vst3)

            self.source_display.config(
                text=(
                    f"{p.name}  —  "
                    f"{sampler_count} Sampler + "
                    f"{vst_count} VST3 instrument(s)"
                )
            )
        except Exception:
            self.detected_samplers = []
            self.source_display.config(text=Path(path).name)

    def _write_log(self, text=""):
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.update_idletasks()

    def _show_completion_dialog(self, mx, output, result):
        dialog = tk.Toplevel(self)
        dialog.title(f"{APP_TITLE} — Conversion complete")
        dialog.geometry("820x650")
        dialog.minsize(680, 500)
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(
            frame,
            text="Conversion complete",
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")

        summary = (
            f"Source: {mx.name}\n"
            f"Sampler tracks: {len(result['samplers'])}   |   "
            f"VST3 instruments: {len(result['vst3'])}   |   "
            f"MIDI items: {result['midi_items']}   |   "
            f"Automation lanes: {result['automation_lanes']}\n"
            f"Created: {output}"
        )
        ttk.Label(frame, text=summary, justify="left").grid(
            row=1, column=0, sticky="ew", pady=(8, 10)
        )
        box = ttk.LabelFrame(
            frame,
            text="NOT ATTEMPTED BY THIS BUILD",
            padding=8,
        )
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        text = tk.Text(
            box,
            wrap="word",
            height=20,
            font=("Segoe UI", 10),
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(box, command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        for item in result.get("not_attempted", []):
            detected = item.get("detected")
            prefix = "[DETECTED] " if detected is True else ""
            text.insert(tk.END, f"• {prefix}{item.get('category', 'Unspecified')}\n")
            text.insert(tk.END, f"  {item.get('detail', '')}\n\n")

        text.insert(
            tk.END,
            "These are scope exclusions, not conversion errors. "
            "The conversion log and .automation.json report contain the same list.\n",
        )
        text.configure(state="disabled")

        ttk.Button(dialog, text="OK", command=dialog.destroy).grid(
            row=3, column=0, sticky="e", pady=(12, 0)
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()

    def convert_clicked(self):
        mx = Path(self.mxprj_var.get().strip())
        template = Path(self.template_var.get().strip())
        output = Path(self.output_var.get().strip())

        if not mx.is_file():
            messagebox.showerror(APP_TITLE, "Choose a valid .mxprj source file.")
            return

        if mx.suffix.lower() != ".mxprj":
            messagebox.showerror(APP_TITLE, "The source must be an .mxprj file.")
            return

        if not template.is_file():
            messagebox.showerror(APP_TITLE, "Choose a valid Komplete Kontrol .rpp template.")
            return

        if not output.name:
            messagebox.showerror(APP_TITLE, "Choose an output .rpp filename.")
            return

        if output.suffix.lower() != ".rpp":
            output = output.with_suffix(".rpp")
            self.output_var.set(str(output))

        if output.exists():
            ok = messagebox.askyesno(
                APP_TITLE,
                f"This output already exists:\n\n{output}\n\nOverwrite it?",
            )
            if not ok:
                return

        self._save_settings()
        self.status_var.set("Converting…")
        self._write_log("")
        self._write_log("=" * 68)
        self._write_log("MASCHINE → REAPER TEST")
        self._write_log(f"MXPRJ    : {mx}")
        self._write_log(f"Template : {template}")
        self._write_log(f"Output   : {output}")
        self._write_log("-" * 68)

        try:
            result = extract_and_convert(mx, template, output)

            samplers = result["samplers"]
            vst3 = result["vst3"]

            engine_names = {
                0: "Standard",
                1: "MP60",
                2: "S1200",
                3: "S1200 L",
                4: "S1200 M",
                5: "S1200 HM",
            }

            self._write_log(
                f"Sampler tracks    : {len(samplers)}"
            )
            self._write_log(
                f"VST3 instruments  : {len(vst3)}"
            )
            self._write_log(
                f"  KK-hosted       : {result.get('kk_hosted_vst3', 0)}"
            )
            self._write_log(
                f"  Native VST3     : {result.get('native_vst3', len(vst3))}"
            )
            self._write_log(
                f"Patterns decoded  : {result['patterns']}"
            )
            self._write_log(
                f"NoteEvent vectors : {result['note_vectors']}"
            )
            self._write_log(
                f"Scenes decoded    : {result['scenes']}"
            )
            self._write_log(
                f"Arranger Sections : {result['sections']}"
            )
            self._write_log(
                f"Arrangement       : {result['arrangement_bars_4_4']:g} bars (4/4)"
            )
            self._write_log(
                f"MIDI items        : {result['midi_items']}"
            )
            self._write_log(
                f"MIDI source notes : {result['midi_notes']}"
            )
            self._write_log(
                f"Tracks with MIDI  : {result['tracks_with_midi']}"
            )
            self._write_log(
                f"Group folders     : {len(result['groups_created'])}"
            )
            self._write_log(
                f"Automation lanes  : {result['automation_lanes']}"
            )
            self._write_log(
                f"Automation events : {result['automation_events']}"
            )
            self._write_log(
                f"Automation tracks : {result['automation_destinations']}"
            )
            self._write_log(
                f"Identity probes   : {len(result.get('identity_probes_used', []))}"
            )
            for probe in result.get("identity_probes_used", []):
                self._write_log(
                    f"    VST3 map      : {probe.get('fx_name', '')} <- {probe.get('path', '')}"
                )
            self._write_log(
                f"KK host maps      : {len(result.get('komplete_kontrol_host_maps', []))}"
            )
            for host_map in result.get("komplete_kontrol_host_maps", []):
                self._write_log(
                    f"    KK host map   : {host_map.get('native_fx_name', '')} "
                    f"offset {host_map.get('parameter_index_offset', 0):+d} "
                    f"({host_map.get('matched_parameter_run', 0)} names matched)"
                )
            if result.get("automation_jsfx"):
                self._write_log(
                    f"Carrier JSFX      : {result['automation_jsfx']}"
                )
            self._write_log(
                f"Automation report : {result['automation_report']}"
            )
            self._write_log(
                f"Blank instruments : {result['blank_instruments']} muted + FX Offline"
            )
            self._write_log(
                f"Serialization     : {result['serialization_family']}"
            )
            if result.get("routing_profile"):
                self._write_log(
                    "Routing           : A1-E1 -> A2-E2 -> H2 SUBMix -> Master"
                )
            self._write_log(
                f"RPP tempo         : {result['tempo']:g} BPM"
            )
            self._write_log("")

            if samplers:
                self._write_log("SAMPLER TRACKS")
                for index, item in enumerate(samplers, 1):
                    self._write_log(
                        f"S{index:02d}. {item['track_name']}"
                    )
                    self._write_log(
                        f"    Sample     : {item['full_path']}"
                    )
                    self._write_log(
                        f"    Poly/Mode  : {item['polyphony']} / "
                        f"{engine_names.get(item['engine_mode'], item['engine_mode'])}"
                    )
                    self._write_log(
                        f"    MIDI       : {len(item.get('midi_items', []))} item(s), "
                        f"{item.get('midi_note_count', 0)} source note(s)"
                    )
                    self._write_log(
                        f"    Group      : {item.get('group_slot', 'Ungrouped')}"
                    )
                    if item.get('muted_by_default'):
                        self._write_log(
                            "    Default    : TRACK MUTED / VST WAKE NOTE / FX ONLINE"
                        )
                self._write_log("")

            if vst3:
                self._write_log("VST3 INSTRUMENT TRACKS")
                for index, item in enumerate(vst3, 1):
                    state_bytes = sum(
                        len(chunk)
                        for _chunk_id, chunk in item["chunks"]
                    )
                    self._write_log(
                        f"V{index:02d}. {item['track_name']}"
                    )
                    self._write_log(
                        f"    Sound ID   : {item['sound_id']}"
                    )
                    self._write_log(
                        f"    VST3 UID   : {item['device_id']}"
                    )
                    self._write_log(
                        f"    Host       : {item.get('host_mode', 'native_vst3')}"
                    )
                    if item.get("kk_host_shell"):
                        self._write_log(
                            f"    KK shell   : {item.get('kk_host_shell')}"
                        )
                    self._write_log(
                        f"    State      : {state_bytes} bytes"
                    )
                    self._write_log(
                        f"    MIDI       : {len(item.get('midi_items', []))} item(s), "
                        f"{item.get('midi_note_count', 0)} source note(s)"
                    )
                    self._write_log(
                        f"    Group      : {item.get('group_slot', 'Ungrouped')}"
                    )
                    if item.get('muted_by_default'):
                        self._write_log(
                            "    Default    : TRACK MUTED"
                        )

            self._write_log("")
            self._write_log("NOT ATTEMPTED BY THIS BUILD")
            for item in result.get("not_attempted", []):
                detected = item.get("detected")
                prefix = "[DETECTED] " if detected is True else ""
                self._write_log(
                    f"- {prefix}{item.get('category', 'Unspecified')}: "
                    f"{item.get('detail', '')}"
                )

            self._write_log("-" * 68)
            self._write_log("CONVERSION COMPLETE")
            self._write_log(f"Created: {output}")

            total_tracks = len(samplers) + len(vst3)

            self.status_var.set(
                f"Complete — {total_tracks} instrument track(s) — {output.name}"
            )

            self._show_completion_dialog(mx, output, result)

        except Exception as exc:
            self.status_var.set("Conversion failed.")
            self._write_log("ERROR:")
            self._write_log(str(exc))
            self._write_log("")
            self._write_log(traceback.format_exc())

            messagebox.showerror(
                APP_TITLE,
                f"Conversion failed:\n\n{exc}\n\n"
                "The full traceback is in the conversion log.",
            )


def main():
    app = ConverterApp()
    app.mainloop()


if __name__ == "__main__":
    main()