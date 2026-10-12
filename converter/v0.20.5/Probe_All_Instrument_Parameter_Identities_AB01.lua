-- Maschine Converter - Automation Binding Branch AB01
-- Batch VST3 Parameter Identity Probe
--
-- Run this once with the v0.20.1 stress-test REAPER project open.
-- It scans every project track, finds the track instrument, and writes one
-- v004-compatible parameter-identity report per instrument to:
--
--   Desktop\IndexProbes\
--
-- The Track: line is deliberately preserved because AB01 uses it to distinguish
-- multiple instances of the same VST3 class (for example Kontakt 7 / Amazing
-- Organ versus Kontakt 7 / Aeon).

local function sanitize_filename(s)
  s = tostring(s or "Plugin")
  s = s:gsub('[<>:"/\\|%?%*]', "_")
  s = s:gsub("%s+", " ")
  s = s:gsub("^%s+", ""):gsub("%s+$", "")
  if s == "" then s = "Plugin" end
  return s
end

local userprofile = os.getenv("USERPROFILE")
if not userprofile or userprofile == "" then
  reaper.MB("Could not determine USERPROFILE.", "AB01 Parameter Probe", 0)
  return
end

local sep = package.config:sub(1,1)
local folder = userprofile .. sep .. "Desktop" .. sep .. "IndexProbes"
reaper.RecursiveCreateDirectory(folder, 0)

local stamp = os.date("%Y%m%d_%H%M%S")
local written = {}
local skipped = {}

local function write_probe(track, track_index, fx)
  local _, fx_name = reaper.TrackFX_GetFXName(track, fx, "")
  local _, track_name = reaper.GetTrackName(track, "")
  local num_params = reaper.TrackFX_GetNumParams(track, fx)
  local _, fx_ident = reaper.TrackFX_GetNamedConfigParm(track, fx, "fx_ident")

  local filename = sanitize_filename(track_name) .. "__" ..
                   sanitize_filename(fx_name) ..
                   "_Parameter_Identity_v004_" .. stamp .. ".txt"
  local path = folder .. sep .. filename

  local f, err = io.open(path, "w")
  if not f then
    skipped[#skipped+1] = string.format("Track %d %s: %s", track_index + 1, track_name, tostring(err))
    return
  end

  f:write("FX: " .. tostring(fx_name) .. "\n")
  f:write("Track: " .. tostring(track_name) .. "\n")
  f:write("REAPER TrackFX_GetNumParams: " .. tostring(num_params) .. "\n")
  f:write("FX index: " .. tostring(fx) .. "\n")
  f:write("fx_ident: " .. tostring(fx_ident or "") .. "\n")
  f:write("Output folder: " .. folder .. "\n\n")
  f:write("INDEX\tIDENT\tNAME\tVALUE\tMIN\tMAX\tFORMATTED\n")

  for p = 0, num_params - 1 do
    local _, pname = reaper.TrackFX_GetParamName(track, fx, p, "")
    local value, minval, maxval = reaper.TrackFX_GetParam(track, fx, p)
    local _, formatted = reaper.TrackFX_GetFormattedParamValue(track, fx, p, "")

    local ident = ""
    if reaper.TrackFX_GetParamIdent then
      local ok, ident_out = reaper.TrackFX_GetParamIdent(track, fx, p, "")
      if ok then ident = ident_out or "" end
    end

    f:write(string.format(
      "%d\t%s\t%s\t%.17g\t%.17g\t%.17g\t%s\n",
      p,
      tostring(ident),
      tostring(pname or ""),
      value or 0,
      minval or 0,
      maxval or 0,
      tostring(formatted or "")
    ))
  end

  f:close()
  written[#written+1] = path
end

local track_count = reaper.CountTracks(0)
for i = 0, track_count - 1 do
  local track = reaper.GetTrack(0, i)
  local fx = reaper.TrackFX_GetInstrument(track)
  if fx and fx >= 0 then
    write_probe(track, i, fx)
  end
end

reaper.ClearConsole()
reaper.ShowConsoleMsg("Automation Binding AB01 batch identity probe complete.\n")
reaper.ShowConsoleMsg("Output folder: " .. folder .. "\n")
for _, path in ipairs(written) do
  reaper.ShowConsoleMsg("  " .. path .. "\n")
end
if #skipped > 0 then
  reaper.ShowConsoleMsg("\nSkipped/errors:\n")
  for _, msg in ipairs(skipped) do
    reaper.ShowConsoleMsg("  " .. msg .. "\n")
  end
end

local message = "Parameter identity capture complete.\n\n" ..
                "Reports written: " .. tostring(#written) .. "\n\n" ..
                "Folder:\n" .. folder .. "\n\n" ..
                "Please upload the FM8 and both Kontakt 7 reports to ChatGPT."
if #skipped > 0 then
  message = message .. "\n\nSome tracks could not be written; see the REAPER console."
end
reaper.MB(message, "AB01 Parameter Probe", 0)
