#!/usr/bin/env python3
"""
Export from Disguise cue tables + all_content_table.txt.

Default outputs (Airtable-friendly):
  exports/media.csv — one row per unique media file; primary column "Name"
  exports/cues.csv — one row per cue with a non-empty CUE_NUMBER (Airtable primary);
    columns: CUE_NUMBER, Act, CUE_NAME, Media (comma-separated Names for link import).
    Cues with no tag / empty CUE_NUMBER are omitted. Duplicate non-empty CUE_NUMBER → fatal error.

Optional legacy file (newlines in VIDEOS column):
  python3 export_cues_csv.py --combined-out exports/cues_with_videos.csv

- CUE_NUMBER: TAG parsed from CUE XX.YYY.ZZ (pad to 2-3-2 digits) as
    int(XX)*100 + int(YYY) + int(ZZ)/100
- CUE_NAME: Note column
- Media filenames: final token only (+ optional v###); ###-###-C## + media extension.

Usage:
  python3 export_cues_csv.py
  python3 export_cues_csv.py --media-out a.csv --cues-out b.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

# Default paths (repo root = parent of this file)
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
DEFAULT_ALL_CONTENT = ASSETS / "all_content_table.txt"
DEFAULT_EXPORT_DIR = ROOT / "exports"
DEFAULT_MEDIA_CSV = DEFAULT_EXPORT_DIR / "media.csv"
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


def asset_matches_naming_convention(asset_name: str) -> bool:
    """
    Keep only assets containing ###-###-C## (case-insensitive C),
    e.g. 101-300-c03-scrim_gradient.mov v001
    """
    return bool(re.search(r"\b\d{3}-\d{3}-[cC]\d{2}\b", asset_name or ""))


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


def media_list_for_section_videos(videos: dict[str, str]) -> list[str]:
    """Ordered unique media Names for a section (sorted by filename for stability)."""
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


def format_videos_field(videos: dict[str, str]) -> str:
    """Legacy: one line per unique file (newline-separated)."""
    return "\n".join(media_list_for_section_videos(videos))


def build_cue_and_media_rows(
    all_content_path: Path, assets_dir: Path
) -> tuple[list[dict[str, str | list[str]]], list[str]]:
    """
    Returns:
      cue_rows: dicts with act, cue_number, cue_name, media (list of Name strings)
      media_names: sorted unique Name values for Media table
    """
    tracks = parse_all_content(all_content_path)
    cue_file_track_pairs = discover_cue_files_and_tracks(assets_dir)
    cue_rows: list[dict[str, str | list[str]]] = []
    all_media: set[str] = set()

    for cue_file, track in cue_file_track_pairs:
        cues = read_cue_table(cue_file)
        sections = tracks.get(track, [])

        for c in cues:
            tag = c.get("Tag", "")
            note = c.get("Note", "")
            tc = c.get("TC_Time", "")
            num = tag_to_cue_number(tag)

            best = find_best_section(sections, note, tc)
            media_list: list[str] = []
            if best is not None:
                media_list = media_list_for_section_videos(best.get("videos") or {})
                for m in media_list:
                    all_media.add(m)

            cue_rows.append(
                {
                    "act": track,
                    "cue_number": num,
                    "cue_name": note,
                    "media": media_list,
                }
            )

    return cue_rows, sorted(all_media)


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
                f"First row: {seen[num]}; duplicate context: act={row['act']!r} name={row['cue_name']!r}"
            )
        seen[num] = f"act={row['act']!r} name={row['cue_name']!r}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export cues + media CSVs for Airtable.")
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
        help="Directory for default media.csv and cues.csv",
    )
    parser.add_argument(
        "--media-out",
        type=Path,
        default=None,
        help="Override path for media.csv",
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
    cues_path = args.cues_out or (export_dir / "cues.csv")

    cue_rows, media_names = build_cue_and_media_rows(all_content_path, ASSETS)
    assert_unique_cue_numbers(cue_rows)

    export_dir.mkdir(parents=True, exist_ok=True)

    # Media table: primary field must match linked names in Cues
    with media_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Name"], quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for name in media_names:
            w.writerow({"Name": name})

    # Cues: primary field CUE_NUMBER (globally unique). Omit rows with no tag / empty number.
    cue_fieldnames = ["CUE_NUMBER", "Act", "CUE_NAME", "Media"]
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
            act = str(row["act"])
            num = str(row["cue_number"]).strip()
            name = str(row["cue_name"])
            media_list = row["media"]
            assert isinstance(media_list, list)
            w.writerow(
                {
                    "CUE_NUMBER": num,
                    "Act": act,
                    "CUE_NAME": name,
                    # No spaces after commas — Airtable matches linked record names exactly
                    "Media": ",".join(media_list),
                }
            )

    print(f"Wrote {len(media_names)} media rows -> {media_path}")
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
                        "Act": track,
                        "CUE_NUMBER": tag_to_cue_number(tag),
                        "CUE_NAME": note,
                        "VIDEOS": vids,
                    }
                )
        with combined.open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(
                f,
                fieldnames=["Act", "CUE_NUMBER", "CUE_NAME", "VIDEOS"],
                quoting=csv.QUOTE_MINIMAL,
            )
            wr.writeheader()
            wr.writerows(legacy_rows)
        print(f"Wrote {len(legacy_rows)} legacy cue rows -> {combined}")


if __name__ == "__main__":
    main()
