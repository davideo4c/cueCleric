#!/usr/bin/env python3
"""
Export Disguise `all_content_table.txt` + `*cue_table*.txt` → exports/channels.csv, filesets.csv, cues.csv.

Optional disk scan: --video-file-dir or --pick-video-file-dir (otherwise no VideoFile step).
See docs/AIRTABLE.md for CSV columns and Airtable workflow.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

# Default paths (repo root = parent of this file)
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
DEFAULT_ALL_CONTENT = ASSETS / "all_content_table.txt"
DEFAULT_EXPORT_DIR = ROOT / "exports"
DEFAULT_CHANNELS_CSV = DEFAULT_EXPORT_DIR / "channels.csv"
DEFAULT_FILESETS_CSV = DEFAULT_EXPORT_DIR / "filesets.csv"
DEFAULT_CUES_CSV = DEFAULT_EXPORT_DIR / "cues.csv"

def discover_cue_files_and_tracks(assets_dir: Path) -> list[tuple[Path, str]]:
    """
    Discover any .txt file containing 'cue_table' in assets and infer track name
    from filename prefix, e.g.:
      2_act1_cue_table.txt -> 2_act1
      0_checkout_cue_table.txt -> 0_checkout
    """
    pairs: list[tuple[Path, str]] = []
    for cue_file in sorted(assets_dir.glob("*cue_table*.txt")):
        name = cue_file.name
        track = re.sub(r"_cue_table.*\.txt$", "", name)
        if track:
            pairs.append((cue_file, track))
    return pairs


def pick_input_dir_gui() -> Path:
    """Open a folder picker and return the chosen directory."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise SystemExit(
            "Folder picker unavailable (tkinter not installed). "
            "Use --input-dir /path/to/folder."
        ) from exc

    root = tk.Tk()
    root.withdraw()
    root.update()
    chosen = filedialog.askdirectory(
        title=(
            "Navigate to your Disguise export folder — open the folder that contains "
            "all_content_table.txt and *cue_table*.txt"
        )
    )
    root.destroy()
    if not chosen:
        raise SystemExit("No folder selected.")
    return Path(chosen)


def pick_video_file_dir_gui(initial_dir: Path | None = None) -> Path | None:
    """
    Second folder picker: root folder for rendered media (e.g. VideoFile/).
    Cancel → None (no disk scan; Version / On disk come only from CSV defaults).
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise SystemExit(
            "VideoFile folder picker unavailable (tkinter not installed). "
            "Pass --video-file-dir /path/to/VideoFile to set the path on the command line."
        ) from exc

    root = tk.Tk()
    root.withdraw()
    root.update()
    kwargs: dict = {
        "title": (
            "Navigate to VideoFile — open the root folder that holds rendered media "
            "(often named VideoFile, with subfolders such as 000-Grids)"
        ),
    }
    if initial_dir is not None and initial_dir.is_dir():
        kwargs["initialdir"] = str(initial_dir)
    chosen = filedialog.askdirectory(**kwargs)
    root.destroy()
    if not chosen:
        return None
    return Path(chosen)


def resolve_input_dir(cli_value: Path | None) -> Path:
    """
    Option A:
      - if --input-dir is provided, use it
      - otherwise open a GUI folder picker
    """
    if cli_value is not None:
        return cli_value
    return pick_input_dir_gui()


def parse_tc(tc: str) -> float | None:
    if not tc or not tc.strip():
        return None
    h, m, sf = tc.split(":", 2)
    s, ff = sf.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ff) / 100.0


def tag_to_cue_number(tag: str) -> str:
    """
    Parse TAG like 'CUE 00.11.00' into a decimal string.
    Pattern is 2-3-2 digit places: XX.YYY.ZZ -> XX*100 + YYY + ZZ/100.
    """
    raw = (tag or "").strip()
    if not raw:
        return ""

    if raw.upper().startswith("CUE"):
        raw = raw[3:].strip()

    parts = raw.split(".")
    if len(parts) != 3:
        return (tag or "").strip()

    seg0, seg1, seg2 = parts[0], parts[1], parts[2]
    # digits only for each segment (ignore stray chars)
    if not all(p.isdigit() for p in (seg0, seg1, seg2)):
        return (tag or "").strip()

    a = seg0.zfill(2)[-2:]
    b = seg1.zfill(3)[-3:]
    c = seg2.zfill(2)[-2:]

    value = int(a) * 100 + int(b) + int(c) / 100.0
    if value == int(value):
        return str(int(value))
    # trim trailing zeros in fractional part
    s = f"{value:.10f}".rstrip("0").rstrip(".")
    return s


def asset_name_only(disguise_value: str) -> str:
    """Full asset label without folder path (keep filename + version suffix)."""
    v = (disguise_value or "").strip()
    if not v:
        return ""
    # Disguise exports often use "folder/file.mov v001"
    if "/" in v:
        v = v.rsplit("/", 1)[-1]
    return v.strip()


# Stem: three digit groups, channel token (c/C + alphanumerics), then description (rest).
_FILESET_STEM_RE = re.compile(
    r"^(\d{3})-(\d{3})-([cC][a-zA-Z0-9]+)-(.+)$"
)


def first_media_token(normalized_final: str) -> str:
    """Filename token before optional Disguise version (e.g. 'clip.mov v001' -> 'clip.mov')."""
    s = (normalized_final or "").strip()
    if not s:
        return ""
    return re.split(r"\s+", s, maxsplit=1)[0]


def stem_without_known_extension(filename: str) -> str:
    first = (filename or "").strip()
    if not first:
        return ""
    lower = first.lower()
    for ext in sorted(_MEDIA_EXT_SUFFIXES, key=len, reverse=True):
        if lower.endswith(ext):
            return first[: len(first) - len(ext)]
    return first


def fileset_key_and_channel_from_final(normalized_final: str) -> tuple[str, str] | None:
    """
    Parse NNN-NNN-<channel>-<description> from normalized media line.
    Returns (fileset_key, channel) e.g. ('000-021-grids_and_guides', 'C11') or None.
    """
    token = first_media_token(normalized_final)
    if not token:
        return None
    stem = stem_without_known_extension(token)
    m = _FILESET_STEM_RE.match(stem)
    if not m:
        return None
    d1, d2, ch_raw, desc = m.groups()
    desc = desc.strip()
    if not desc:
        return None
    channel = "C" + ch_raw[1:]
    fileset_key = f"{d1}-{d2}-{desc}"
    return (fileset_key, channel)


def asset_matches_naming_convention(asset_name: str) -> bool:
    """True if the asset parses as a fileset + channel (NNN-NNN-c…-description)."""
    return fileset_key_and_channel_from_final(asset_name or "") is not None


# Filename token before optional Disguise version (e.g. "clip.mov v001" -> "clip.mov")
_MEDIA_EXT_SUFFIXES = frozenset(
    {
        ".mov",
        ".mp4",
        ".m4v",
        ".mxf",
        ".avi",
        ".mkv",
        ".webm",
        ".wmv",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".tga",
        ".exr",
        ".dpx",
        ".gif",
        ".bmp",
        ".heic",
        ".svg",
    }
)


def asset_has_media_file_extension(asset_name: str) -> bool:
    """
    True if the asset's filename (first token, before version suffix) ends with
    a known media extension, e.g. .mov, .png
    """
    s = (asset_name or "").strip()
    if not s:
        return False
    first = re.split(r"\s+", s, maxsplit=1)[0]
    lower = first.lower()
    return any(lower.endswith(ext) for ext in _MEDIA_EXT_SUFFIXES)


def _token_ends_media_ext(token: str) -> bool:
    lower = (token or "").lower()
    return any(lower.endswith(ext) for ext in _MEDIA_EXT_SUFFIXES)


def extract_final_media_filename(s: str) -> str:
    """
    Keep only the actual media file token: the rightmost whitespace-separated
    token that ends with a known extension, plus a following Disguise version
    token (e.g. v001) if present.

    Strips layer/output prefixes like:
      'c02-wall - 000-021-c02-grids_and_guides    000-011-c02-guides.mov'
    -> '000-011-c02-guides.mov'
    """
    s = (s or "").strip()
    if not s:
        return ""
    tokens = s.split()
    if not tokens:
        return ""
    for i in range(len(tokens) - 1, -1, -1):
        if _token_ends_media_ext(tokens[i]):
            parts = [tokens[i]]
            if i + 1 < len(tokens) and re.match(r"^v\d", tokens[i + 1], re.I):
                parts.append(tokens[i + 1])
            return " ".join(parts)
    return ""


def normalized_video_filename_from_disguise_value(disguise_value: str) -> str:
    """Path-stripped value, reduced to final filename (+ optional v###)."""
    return extract_final_media_filename(asset_name_only(disguise_value))


# Disk filenames: strip trailing `_v001`, `-v02`, `.v3` from stem (before extension).
_DISK_VERSION_SUFFIX_RE = re.compile(r"(?i)([._-]v)(\d+)$")


def disk_canonical_name_and_version(filename: str) -> tuple[str, int] | None:
    """
    From a disk basename (e.g. 000-011-c02-guides_v003.mov), return
    (canonical Name, version int). If no _vNNN suffix, version defaults to 1.
    """
    name = (filename or "").strip()
    if not name:
        return None
    lower = name.lower()
    ext = ""
    for e in sorted(_MEDIA_EXT_SUFFIXES, key=len, reverse=True):
        if lower.endswith(e):
            ext = name[len(name) - len(e) :]
            stem = name[: len(name) - len(e)]
            break
    else:
        return None
    m = _DISK_VERSION_SUFFIX_RE.search(stem)
    if m:
        base_stem = stem[: m.start(1)]
        ver = int(m.group(2))
    else:
        base_stem, ver = stem, 1
    canonical = f"{base_stem}{ext}"
    return (canonical, ver)


def canonical_media_name_from_disguise_final(final: str) -> str | None:
    """
    Disk-canonical media Name: first filename token (no separate Disguise ` v###` token),
    then strip disk-style `_vNNN` / `-vN` / `.vN` from the stem so it matches VideoFile scan keys.
    """
    s = (final or "").strip()
    if not s:
        return None
    token = first_media_token(s)
    if not token or not asset_has_media_file_extension(token):
        return None
    parsed = disk_canonical_name_and_version(token)
    if parsed is None:
        return token.strip()
    return parsed[0]


def scan_video_file_directory(root: Path) -> dict[str, int]:
    """
    Walk root recursively; group by canonical Name; return max version per Name.
    """
    best: dict[str, int] = {}
    if not root.is_dir():
        return best
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parsed = disk_canonical_name_and_version(path.name)
        if parsed is None:
            continue
        name, ver = parsed
        prev = best.get(name)
        if prev is None or ver > prev:
            best[name] = ver
    return best


def format_channel_versions_summary(
    media_names: list[str],
    version_by_canonical: dict[str, int],
) -> str:
    """
    Single-line text for Filesets: per channel, max version among media in this fileset.
    Example: C01-v003,C11-v001 (comma-separated, no spaces; version zero-padded to 3 digits).
    """
    ch_to_ver: dict[str, int] = {}
    for name in media_names:
        spec = fileset_key_and_channel_from_final(name)
        if spec is None:
            continue
        _fkey, ch = spec
        ver = int(version_by_canonical.get(name, 1))
        prev = ch_to_ver.get(ch)
        if prev is None or ver > prev:
            ch_to_ver[ch] = ver
    if not ch_to_ver:
        return ""
    parts = [f"{ch}-v{ch_to_ver[ch]:03d}" for ch in sorted(ch_to_ver.keys())]
    return ",".join(parts)


def merge_fileset_aggregates_from_tracks(
    tracks: dict[str, list[dict]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Every fileset key and its channels / canonical media names appearing anywhere
    in all_content_table (all tracks / sections). Used for filesets.csv rows.
    """
    fc: defaultdict[str, set[str]] = defaultdict(set)
    fm: defaultdict[str, set[str]] = defaultdict(set)
    for sections in tracks.values():
        for sec in sections:
            section_filesets_and_media_for_videos(
                sec.get("videos") or {}, fc, fm
            )
    fs_ch = {k: sorted(v) for k, v in fc.items()}
    fs_med = {k: sorted(v) for k, v in fm.items()}
    return fs_ch, fs_med


def collect_used_fileset_keys_from_tracks(tracks: dict[str, list[dict]]) -> set[str]:
    """Every fileset key derived from any video row in the content table."""
    out: set[str] = set()
    for sections in tracks.values():
        for sec in sections:
            for val in (sec.get("videos") or {}).values():
                fin = normalized_video_filename_from_disguise_value(val)
                if not fin or not asset_has_media_file_extension(fin):
                    continue
                spec = fileset_key_and_channel_from_final(fin)
                if spec:
                    out.add(spec[0])
    return out


def read_cue_table(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Beat\t") and "TC_Time" in line:
            header_idx = i
            break
    if header_idx is None:
        return []

    header = lines[header_idx].split("\t")
    col = {name: j for j, name in enumerate(header)}

    def get(parts: list[str], name: str) -> str:
        j = col.get(name)
        if j is None or j >= len(parts):
            return ""
        return parts[j]

    cues: list[dict[str, str]] = []
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            continue
        parts = line.split("\t")
        cues.append(
            {
                "Beat": get(parts, "Beat"),
                "Tag": get(parts, "Tag"),
                "Note": get(parts, "Note"),
                "Track_Time": get(parts, "Track_Time"),
                "TC_Time": get(parts, "TC_Time"),
            }
        )
    return cues


def parse_all_content(all_content_path: Path) -> dict[str, list[dict]]:
    """
    track_name -> list of sections, each:
      { 'name': str, 'time': str, 'videos': { output_name: disguise_value } }
    """
    tracks: dict[str, list[dict]] = {}
    cur_track: str | None = None
    cur_section: dict | None = None

    for raw in all_content_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        key = parts[0]

        if key == "Track":
            cur_track = parts[1] if len(parts) > 1 else ""
            tracks.setdefault(cur_track, [])
            cur_section = None
            continue

        if key == "Section":
            name = ""
            t = ""
            if len(parts) >= 3:
                name = parts[1]
                t = parts[2]
            elif len(parts) == 2:
                t = parts[1]
            cur_section = {"name": name, "time": t, "videos": {}}
            tracks.setdefault(cur_track or "__unknown__", []).append(cur_section)
            continue

        if key == "video" and cur_section is not None and len(parts) >= 4:
            out = parts[1]
            val = parts[3]
            cur_section["videos"][out] = val
            continue

    return tracks


def find_best_section(
    sections: list[dict], cue_note: str, cue_tc: str
) -> dict | None:
    note = (cue_note or "").strip()
    name_matches = [s for s in sections if (s.get("name") or "").strip() == note]
    candidates = name_matches if name_matches else list(sections)
    if not candidates:
        return None

    cue_t = parse_tc(cue_tc)

    def score(s: dict) -> float:
        st = parse_tc(s.get("time") or "")
        if st is None:
            return float("inf")
        if cue_t is None:
            return st
        return abs(st - cue_t)

    return min(candidates, key=score)


def normalized_media_files_for_section_videos(videos: dict[str, str]) -> list[str]:
    """Ordered unique disk-canonical Names (for legacy VIDEOS column)."""
    if not videos:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for _layer, disguise_val in sorted(videos.items()):
        final = normalized_video_filename_from_disguise_value(disguise_val)
        if not final:
            continue
        if not asset_matches_naming_convention(final):
            continue
        if not asset_has_media_file_extension(final):
            continue
        canon = canonical_media_name_from_disguise_final(final)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        names.append(canon)
    return names


def section_filesets_and_media_for_videos(
    videos: dict[str, str],
    fileset_channels: defaultdict[str, set[str]],
    fileset_media: defaultdict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """
    For one section's videos: ordered unique fileset keys, ordered unique disk-canonical media Names.
    Updates fileset_channels and fileset_media with every parsed file.
    """
    if not videos:
        return [], []
    keys: list[str] = []
    seen_fs: set[str] = set()
    media: list[str] = []
    seen_m: set[str] = set()
    for _layer, disguise_val in sorted(videos.items()):
        final = normalized_video_filename_from_disguise_value(disguise_val)
        if not final:
            continue
        if not asset_has_media_file_extension(final):
            continue
        spec = fileset_key_and_channel_from_final(final)
        if spec is None:
            continue
        canon = canonical_media_name_from_disguise_final(final)
        if not canon:
            continue
        fkey, ch = spec
        fileset_channels[fkey].add(ch)
        fileset_media[fkey].add(canon)
        if fkey not in seen_fs:
            seen_fs.add(fkey)
            keys.append(fkey)
        if canon not in seen_m:
            seen_m.add(canon)
            media.append(canon)
    return keys, media


def format_videos_field(videos: dict[str, str]) -> str:
    """Legacy: one line per unique file (newline-separated)."""
    return "\n".join(normalized_media_files_for_section_videos(videos))


def format_video_layers_used(videos: dict[str, str]) -> str:
    """
    Newline-separated list of distinct resolved media filenames for this cue's section,
    in layer name order (no output/layer labels).
    """
    if not videos:
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    for _layer in sorted(videos.keys()):
        disguise_value = videos.get(_layer) or ""
        resolved = normalized_video_filename_from_disguise_value(disguise_value)
        if not resolved:
            resolved = asset_name_only(disguise_value).strip()
        if not resolved:
            resolved = disguise_value.strip()
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        lines.append(resolved)
    return "\n".join(lines)


def build_cue_and_fileset_rows(
    all_content_path: Path,
    assets_dir: Path,
    tracks: dict[str, list[dict]] | None = None,
) -> tuple[
    list[dict[str, str | list[str]]],
    dict[str, list[str]],
    dict[str, list[str]],
    list[str],
]:
    """
    Returns:
      cue_rows: track, cue_number, cue_name, media (filenames), filesets (fileset keys)
      filesets_channels: fileset key -> sorted channel codes
      filesets_media: fileset key -> sorted disk-canonical media Names for that fileset
      media_names: sorted unique Names appearing in filesets (for disguise catalog / media.csv merge)
    """
    if tracks is None:
        tracks = parse_all_content(all_content_path)
    cue_file_track_pairs = discover_cue_files_and_tracks(assets_dir)
    cue_rows: list[dict[str, str | list[str]]] = []
    fileset_channels: defaultdict[str, set[str]] = defaultdict(set)
    fileset_media: defaultdict[str, set[str]] = defaultdict(set)

    for cue_file, track in cue_file_track_pairs:
        cues = read_cue_table(cue_file)
        sections = tracks.get(track, [])

        for c in cues:
            tag = c.get("Tag", "")
            note = c.get("Note", "")
            tc = c.get("TC_Time", "")
            num = tag_to_cue_number(tag)

            best = find_best_section(sections, note, tc)
            fileset_list: list[str] = []
            media_list: list[str] = []
            video_layers_used = ""
            if best is not None:
                fileset_list, media_list = section_filesets_and_media_for_videos(
                    best.get("videos") or {}, fileset_channels, fileset_media
                )
                video_layers_used = format_video_layers_used(best.get("videos") or {})

            cue_rows.append(
                {
                    "track": track,
                    "cue_number": num,
                    "cue_name": note,
                    "media": media_list,
                    "filesets": fileset_list,
                    "video_layers_used": video_layers_used,
                }
            )

    fs_ch = {k: sorted(v) for k, v in fileset_channels.items()}
    fs_med = {k: sorted(v) for k, v in fileset_media.items()}
    all_media = sorted({m for names in fs_med.values() for m in names})
    return cue_rows, fs_ch, fs_med, all_media


def assert_unique_cue_numbers(cue_rows: list[dict[str, str | list[str]]]) -> None:
    """Fatal error if the same non-empty CUE_NUMBER appears more than once (Airtable primary)."""
    seen: dict[str, str] = {}  # number -> first occurrence context
    for row in cue_rows:
        num = str(row["cue_number"]).strip()
        if not num:
            continue
        if num in seen:
            raise SystemExit(
                f"Duplicate CUE_NUMBER {num!r} (Airtable primary must be unique). "
                f"First row: {seen[num]}; duplicate context: track={row['track']!r} name={row['cue_name']!r}"
            )
        seen[num] = f"track={row['track']!r} name={row['cue_name']!r}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Disguise data to CSVs for Airtable."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=None,
        help="Optional explicit path to all_content_table.txt (overrides --input-dir/picker)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Folder containing all_content_table.txt and *cue_table*.txt; if omitted, opens a folder picker",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=DEFAULT_EXPORT_DIR,
        help="Directory for default channels.csv, filesets.csv, cues.csv",
    )
    parser.add_argument(
        "--channels-out",
        type=Path,
        default=None,
        help="Override path for channels.csv",
    )
    parser.add_argument(
        "--filesets-out",
        type=Path,
        default=None,
        help="Override path for filesets.csv",
    )
    parser.add_argument(
        "--cues-out",
        type=Path,
        default=None,
        help="Override path for cues.csv",
    )
    parser.add_argument(
        "--combined-out",
        type=Path,
        default=None,
        help="Optional legacy CSV with VIDEOS column (newlines)",
    )
    parser.add_argument(
        "--video-file-dir",
        type=Path,
        default=None,
        help="Root folder to scan for media files (e.g. …/VideoFile). If omitted, no disk scan "
        "unless --pick-video-file-dir is used.",
    )
    parser.add_argument(
        "--pick-video-file-dir",
        action="store_true",
        help="Open a folder picker for the VideoFile root (after input folder is chosen). "
        "Ignored if --video-file-dir is set. Cancel → no scan.",
    )
    args = parser.parse_args()

    input_dir = resolve_input_dir(args.input_dir)
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    all_content_path: Path = args.input or (input_dir / "all_content_table.txt")
    if not all_content_path.is_file():
        raise SystemExit(f"Missing input file: {all_content_path}")

    video_root: Path | None
    if args.video_file_dir is not None:
        video_root = args.video_file_dir
        if not video_root.is_dir():
            raise SystemExit(f"VideoFile directory does not exist: {video_root}")
    elif args.pick_video_file_dir:
        candidate = input_dir / "VideoFile"
        video_root = pick_video_file_dir_gui(
            candidate if candidate.is_dir() else input_dir
        )
    else:
        video_root = None

    export_dir = args.export_dir
    channels_path = args.channels_out or (export_dir / "channels.csv")
    filesets_path = args.filesets_out or (export_dir / "filesets.csv")
    cues_path = args.cues_out or (export_dir / "cues.csv")

    tracks = parse_all_content(all_content_path)
    cue_rows, _fs_map, _fs_med_map, _all_media = build_cue_and_fileset_rows(
        all_content_path, input_dir, tracks
    )
    assert_unique_cue_numbers(cue_rows)

    used_filesets = collect_used_fileset_keys_from_tracks(tracks)
    global_fs_ch, global_fs_med = merge_fileset_aggregates_from_tracks(tracks)

    disk_max: dict[str, int] = {}
    if video_root is not None and video_root.is_dir():
        disk_max = scan_video_file_directory(video_root)
        print(f"VideoFile scan: {video_root}")
    else:
        print(
            "VideoFile scan skipped (no --video-file-dir). "
            "Channel versions use defaults unless you pass --video-file-dir or --pick-video-file-dir."
        )

    export_dir.mkdir(parents=True, exist_ok=True)

    all_channel_codes: set[str] = set()
    for fs_name in used_filesets:
        all_channel_codes.update(global_fs_ch.get(fs_name, []))

    with channels_path.open("w", newline="", encoding="utf-8") as f:
        cw = csv.DictWriter(f, fieldnames=["Name"], quoting=csv.QUOTE_MINIMAL)
        cw.writeheader()
        for code in sorted(all_channel_codes):
            cw.writerow({"Name": code})

    # Filesets: only used-in-show rows; Channel versions = text summary (not Media links).
    fileset_rows_written = 0
    with filesets_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Name", "Channels", "Channel versions", "Used in show"],
            quoting=csv.QUOTE_MINIMAL,
        )
        w.writeheader()
        for name in sorted(used_filesets):
            chans = global_fs_ch.get(name, [])
            med = global_fs_med.get(name, [])
            cv_line = format_channel_versions_summary(med, disk_max)
            w.writerow(
                {
                    "Name": name,
                    "Channels": ",".join(chans),
                    "Channel versions": cv_line,
                    "Used in show": "TRUE",
                }
            )
            fileset_rows_written += 1

    # Cues: primary field CUE_NUMBER (globally unique). Omit rows with no tag / empty number.
    cue_fieldnames = [
        "CUE_NUMBER",
        "Track",
        "CUE_NAME",
        "Filesets",
        "Video layers used",
    ]
    airtable_cue_rows = [
        r
        for r in cue_rows
        if str(r["cue_number"]).strip()
    ]
    omitted = len(cue_rows) - len(airtable_cue_rows)
    with cues_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cue_fieldnames, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for row in airtable_cue_rows:
            track = str(row["track"])
            num = str(row["cue_number"]).strip()
            name = str(row["cue_name"])
            fs_list = row["filesets"]
            assert isinstance(fs_list, list)
            w.writerow(
                {
                    "CUE_NUMBER": num,
                    "Track": track,
                    "CUE_NAME": name,
                    # No spaces after commas — Airtable matches linked record names exactly
                    "Filesets": ",".join(fs_list),
                    "Video layers used": str(row.get("video_layers_used") or ""),
                }
            )

    print(f"Wrote {len(all_channel_codes)} channel rows -> {channels_path}")
    print(
        f"Wrote {fileset_rows_written} fileset rows (used in show only) -> {filesets_path}"
    )
    print(f"Wrote {len(airtable_cue_rows)} cue rows -> {cues_path} (omitted {omitted} with empty CUE_NUMBER)")

    if args.combined_out is not None:
        combined = args.combined_out
        combined.parent.mkdir(parents=True, exist_ok=True)
        legacy_rows: list[dict[str, str]] = []
        for cue_file, track in discover_cue_files_and_tracks(input_dir):
            cues = read_cue_table(cue_file)
            sections = tracks.get(track, [])
            for c in cues:
                tag = c.get("Tag", "")
                note = c.get("Note", "")
                tc = c.get("TC_Time", "")
                best = find_best_section(sections, note, tc)
                vids = (
                    format_videos_field(best.get("videos") or {})
                    if best
                    else ""
                )
                legacy_rows.append(
                    {
                        "Track": track,
                        "CUE_NUMBER": tag_to_cue_number(tag),
                        "CUE_NAME": note,
                        "VIDEOS": vids,
                    }
                )
        with combined.open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(
                f,
                fieldnames=["Track", "CUE_NUMBER", "CUE_NAME", "VIDEOS"],
                quoting=csv.QUOTE_MINIMAL,
            )
            wr.writeheader()
            wr.writerows(legacy_rows)
        print(f"Wrote {len(legacy_rows)} legacy cue rows -> {combined}")


if __name__ == "__main__":
    main()
