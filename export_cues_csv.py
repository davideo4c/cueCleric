#!/usr/bin/env python3
"""
Export from Disguise cue tables + all_content_table.txt.

Default outputs (Airtable-friendly):
  exports/media.csv — one row per unique media file (verbose filename + optional v###); column Name.
  exports/channels.csv — one row per unique channel code (primary column Name), e.g. C01, C21.
  exports/filesets.csv — one row per fileset; columns: Name, Channels (→ Channels), Media (→ Media).
  exports/cues.csv — CUE_NUMBER, Track, CUE_NAME, Media (→ Media, per-file), Filesets (→ Filesets).
    Cues with no tag / empty CUE_NUMBER are omitted. Duplicate non-empty CUE_NUMBER → fatal error.

Media filenames are parsed as NNN-NNN-<channel>-<description>.<ext> (channel = C + alphanumerics),
e.g. 000-021-c11-grids_and_guides.mov → fileset 000-021-grids_and_guides, channel C11.

Optional legacy file (newlines in VIDEOS column):
  python3 export_cues_csv.py --combined-out exports/cues_with_videos.csv

- CUE_NUMBER: TAG parsed from CUE XX.YYY.ZZ (pad to 2-3-2 digits) as
    int(XX)*100 + int(YYY) + int(ZZ)/100
- CUE_NAME: Note column

Usage:
  python3 export_cues_csv.py
  python3 export_cues_csv.py --filesets-out a.csv --cues-out b.csv
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
DEFAULT_MEDIA_CSV = DEFAULT_EXPORT_DIR / "media.csv"
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
    """Ordered unique final filenames (for legacy VIDEOS column)."""
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
        if final not in seen:
            seen.add(final)
            names.append(final)
    return names


def section_filesets_and_media_for_videos(
    videos: dict[str, str],
    fileset_channels: defaultdict[str, set[str]],
    fileset_media: defaultdict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """
    For one section's videos: ordered unique fileset keys, ordered unique media filenames.
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
        fkey, ch = spec
        fileset_channels[fkey].add(ch)
        fileset_media[fkey].add(final)
        if fkey not in seen_fs:
            seen_fs.add(fkey)
            keys.append(fkey)
        if final not in seen_m:
            seen_m.add(final)
            media.append(final)
    return keys, media


def format_videos_field(videos: dict[str, str]) -> str:
    """Legacy: one line per unique file (newline-separated)."""
    return "\n".join(normalized_media_files_for_section_videos(videos))


def build_cue_and_fileset_rows(
    all_content_path: Path, assets_dir: Path
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
      filesets_media: fileset key -> sorted media Names for that fileset
      media_names: sorted unique media Names (for media.csv)
    """
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
            if best is not None:
                fileset_list, media_list = section_filesets_and_media_for_videos(
                    best.get("videos") or {}, fileset_channels, fileset_media
                )

            cue_rows.append(
                {
                    "track": track,
                    "cue_number": num,
                    "cue_name": note,
                    "media": media_list,
                    "filesets": fileset_list,
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
        description="Export media, channels, filesets, and cues CSVs for Airtable."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_ALL_CONTENT,
        help="Path to all_content_table.txt",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=DEFAULT_EXPORT_DIR,
        help="Directory for default media.csv, channels.csv, filesets.csv, cues.csv",
    )
    parser.add_argument(
        "--media-out",
        type=Path,
        default=None,
        help="Override path for media.csv",
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
    args = parser.parse_args()

    all_content_path: Path = args.input
    if not all_content_path.is_file():
        raise SystemExit(f"Missing input file: {all_content_path}")

    export_dir = args.export_dir
    media_path = args.media_out or (export_dir / "media.csv")
    channels_path = args.channels_out or (export_dir / "channels.csv")
    filesets_path = args.filesets_out or (export_dir / "filesets.csv")
    cues_path = args.cues_out or (export_dir / "cues.csv")

    cue_rows, filesets_map, filesets_media_map, media_names = (
        build_cue_and_fileset_rows(all_content_path, ASSETS)
    )
    assert_unique_cue_numbers(cue_rows)

    export_dir.mkdir(parents=True, exist_ok=True)

    with media_path.open("w", newline="", encoding="utf-8") as f:
        mw = csv.DictWriter(f, fieldnames=["Name"], quoting=csv.QUOTE_MINIMAL)
        mw.writeheader()
        for name in media_names:
            mw.writerow({"Name": name})

    all_channel_codes: set[str] = set()
    for chans in filesets_map.values():
        all_channel_codes.update(chans)

    with channels_path.open("w", newline="", encoding="utf-8") as f:
        cw = csv.DictWriter(f, fieldnames=["Name"], quoting=csv.QUOTE_MINIMAL)
        cw.writeheader()
        for code in sorted(all_channel_codes):
            cw.writerow({"Name": code})

    # Filesets: Channels → Channels table; Media → Media table (comma-separated Names)
    with filesets_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Name", "Channels", "Media"],
            quoting=csv.QUOTE_MINIMAL,
        )
        w.writeheader()
        for name in sorted(filesets_map.keys()):
            med = filesets_media_map.get(name, [])
            w.writerow(
                {
                    "Name": name,
                    "Channels": ",".join(filesets_map[name]),
                    "Media": ",".join(med),
                }
            )

    # Cues: primary field CUE_NUMBER (globally unique). Omit rows with no tag / empty number.
    cue_fieldnames = ["CUE_NUMBER", "Track", "CUE_NAME", "Media", "Filesets"]
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
            med_list = row["media"]
            fs_list = row["filesets"]
            assert isinstance(med_list, list)
            assert isinstance(fs_list, list)
            w.writerow(
                {
                    "CUE_NUMBER": num,
                    "Track": track,
                    "CUE_NAME": name,
                    # No spaces after commas — Airtable matches linked record names exactly
                    "Media": ",".join(med_list),
                    "Filesets": ",".join(fs_list),
                }
            )

    print(f"Wrote {len(media_names)} media rows -> {media_path}")
    print(f"Wrote {len(all_channel_codes)} channel rows -> {channels_path}")
    print(f"Wrote {len(filesets_map)} fileset rows -> {filesets_path}")
    print(f"Wrote {len(airtable_cue_rows)} cue rows -> {cues_path} (omitted {omitted} with empty CUE_NUMBER)")

    if args.combined_out is not None:
        combined = args.combined_out
        combined.parent.mkdir(parents=True, exist_ok=True)
        tracks = parse_all_content(all_content_path)
        legacy_rows: list[dict[str, str]] = []
        for cue_file, track in discover_cue_files_and_tracks(ASSETS):
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
