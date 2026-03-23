#!/usr/bin/env python3
"""
Push channels.csv, filesets.csv, and cues.csv to Airtable via the REST API.

By default the **Media** table is **not** synced (set AIRTABLE_SYNC_MEDIA=1 to opt in and
supply exports/media.csv).

Prerequisites:
  1. Run: python3 export_cues_csv.py
  2. Create an Airtable base with tables/fields matching docs/AIRTABLE.md
  3. Create a Personal Access Token (PAT) with data read/write on the base.

Environment variables:
  AIRTABLE_TOKEN       Required. PAT (starts with pat...)
  AIRTABLE_BASE_ID     Required. Base ID (starts with app...)

Optional:
  AIRTABLE_SYNC_MEDIA               If 1/true/yes/on, sync Media table (requires media.csv)
  AIRTABLE_MEDIA_TABLE              Default: Media
  ... (other AIRTABLE_MEDIA_* when sync enabled)
  AIRTABLE_CHANNELS_TABLE           Default: Channels
  AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD  Single line text on Filesets. Default: Channel versions
  AIRTABLE_FILESETS_WRITABLE_FIELDS Default: Channels,Used in show,Channel versions (Media omitted)
  AIRTABLE_CUE_WRITABLE_FIELDS      Default: Track,CUE_NAME,Filesets (add Media to sync/clear links)

Usage:
  export AIRTABLE_TOKEN=pat...
  export AIRTABLE_BASE_ID=app...
  python3 push_airtable.py --dry-run
  python3 push_airtable.py

  python3 push_airtable.py --channels-csv exports/channels.csv \\
      --filesets-csv exports/filesets.csv --cues-csv exports/cues.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Callable
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MEDIA_CSV = ROOT / "exports" / "media.csv"
DEFAULT_CHANNELS_CSV = ROOT / "exports" / "channels.csv"
DEFAULT_FILESETS_CSV = ROOT / "exports" / "filesets.csv"
DEFAULT_CUES_CSV = ROOT / "exports" / "cues.csv"

# Airtable allows up to 10 records per create/patch request
BATCH = 10
# Stay under ~5 req/s
REQUEST_DELAY_S = 0.22


def load_dotenv_if_present() -> None:
    """Load KEY=VALUE lines from .env in project root (no dependency on python-dotenv).

    Values in .env override existing environment variables so edits to .env take
    effect even if the shell still has old exports.
    """
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            os.environ[k] = v


def api_request(
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
) -> dict | list:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {method} {url}\n{err_body}") from e


def list_all_records(
    base_id: str, table: str, token: str, fields: list[str] | None = None
) -> list[dict]:
    """Return all records with id + fields."""
    out: list[dict] = []
    offset = None
    while True:
        qs = "pageSize=100"
        if fields:
            for fld in fields:
                qs += f"&fields[]={urllib.parse.quote(fld)}"
        if offset:
            qs += f"&offset={urllib.parse.quote(offset)}"
        url = (
            f"https://api.airtable.com/v0/{urllib.parse.quote(base_id)}/"
            f"{urllib.parse.quote(table)}?{qs}"
        )
        time.sleep(REQUEST_DELAY_S)
        payload = api_request("GET", url, token)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected list response from {table}")
        out.extend(payload.get("records") or [])
        offset = payload.get("offset")
        if not offset:
            break
    return out


def create_records(
    base_id: str, table: str, token: str, records_fields: list[dict]
) -> list[dict]:
    """POST records in batches; return Airtable record dicts (id + fields) from all responses."""
    created: list[dict] = []
    for i in range(0, len(records_fields), BATCH):
        chunk = records_fields[i : i + BATCH]
        body = {"records": [{"fields": f} for f in chunk]}
        url = f"https://api.airtable.com/v0/{urllib.parse.quote(base_id)}/{urllib.parse.quote(table)}"
        time.sleep(REQUEST_DELAY_S)
        payload = api_request("POST", url, token, body)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected POST response from {table}")
        created.extend(payload.get("records") or [])
    return created


def merge_created_by_primary_field(
    created: list[dict],
    primary_field: str,
    id_by_key: dict[str, str],
    *,
    primary_reader: Callable[[dict, str], str | None] | None = None,
) -> None:
    """Insert id into id_by_key using primary field value from each created record."""
    for rec in created:
        rid = rec.get("id")
        if not rid:
            continue
        fields = rec.get("fields") or {}
        if primary_reader is not None:
            key = primary_reader(fields, primary_field)
        else:
            val = fields.get(primary_field)
            key = val.strip() if isinstance(val, str) and val.strip() else None
            if key is None and isinstance(val, (int, float)):
                key = str(val)
        if key:
            id_by_key[key] = rid


def normalized_link_ids(val) -> tuple[str, ...]:
    """Order-independent comparison for Airtable linked-record fields."""
    if not val:
        return ()
    if isinstance(val, list):
        return tuple(sorted(str(x) for x in val))
    return ()


def normalized_scalar(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def airtable_field_matches_desired(existing_val, desired_val) -> bool:
    """True if Airtable GET value already matches a PATCH value we would send."""
    if isinstance(desired_val, list):
        return normalized_link_ids(existing_val) == tuple(
            sorted(str(x) for x in desired_val)
        )
    if isinstance(desired_val, bool):
        if existing_val is None:
            ex_b = False
        else:
            ex_b = bool(existing_val)
        return ex_b == desired_val
    if isinstance(desired_val, int) and not isinstance(desired_val, bool):
        if existing_val is None:
            return False
        try:
            ex_n = int(existing_val)
        except (TypeError, ValueError):
            return False
        return ex_n == desired_val
    return normalized_scalar(existing_val) == normalized_scalar(desired_val)


def patch_redundant_with_existing(existing_fields: dict, patch: dict) -> bool:
    """True if every key in patch already equals existing_fields (skip PATCH)."""
    return all(
        airtable_field_matches_desired(existing_fields.get(k), v)
        for k, v in patch.items()
    )


def update_records(
    base_id: str, table: str, token: str, updates: list[tuple[str, dict]]
) -> None:
    for i in range(0, len(updates), BATCH):
        chunk = updates[i : i + BATCH]
        body = {"records": [{"id": rid, "fields": flds} for rid, flds in chunk]}
        url = (
            f"https://api.airtable.com/v0/{urllib.parse.quote(base_id)}/"
            f"{urllib.parse.quote(table)}"
        )
        time.sleep(REQUEST_DELAY_S)
        api_request("PATCH", url, token, body)


def delete_record(base_id: str, table: str, token: str, record_id: str) -> None:
    url = (
        f"https://api.airtable.com/v0/{urllib.parse.quote(base_id)}/"
        f"{urllib.parse.quote(table)}/{urllib.parse.quote(record_id)}"
    )
    time.sleep(REQUEST_DELAY_S)
    api_request("DELETE", url, token, None)


def parse_csv_bool(cell: str | None) -> bool | None:
    """CSV checkbox columns: TRUE/FALSE (and a few aliases). Unknown → None."""
    s = (cell or "").strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f", ""):
        return False
    return None


def read_media_csv(path: Path) -> tuple[list[dict[str, str]], frozenset[str]]:
    """
    Rows from media.csv (Name required). Returns (rows, column names present).
    Optional columns: Version, Used in show, On disk.
    """
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "Name" not in r.fieldnames:
            raise SystemExit(f"{path}: need a 'Name' column")
        cols = frozenset(x.strip() for x in r.fieldnames if x and x.strip())
        for row in r:
            n = (row.get("Name") or "").strip()
            if n:
                rows.append(dict(row))
    return rows, cols


def media_extra_fields_from_row(
    row: dict[str, str],
    csv_cols: frozenset[str],
    writable_airtable_fields: list[str],
    version_field: str,
    used_field: str,
    on_disk_field: str,
) -> dict:
    """Subset of Airtable Media fields derived from one CSV row (writable only)."""
    patch: dict = {}
    if version_field in writable_airtable_fields and "Version" in csv_cols:
        v = (row.get("Version") or "").strip()
        if v.isdigit():
            patch[version_field] = int(v)
        elif v:
            patch[version_field] = v
    if used_field in writable_airtable_fields and "Used in show" in csv_cols:
        b = parse_csv_bool(row.get("Used in show"))
        if b is not None:
            patch[used_field] = b
    if on_disk_field in writable_airtable_fields and "On disk" in csv_cols:
        b2 = parse_csv_bool(row.get("On disk"))
        if b2 is not None:
            patch[on_disk_field] = b2
    return patch


def read_channels_csv(path: Path) -> list[str]:
    """Channel codes from channels.csv (primary Name)."""
    names: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "Name" not in r.fieldnames:
            raise SystemExit(f"{path}: need a 'Name' column")
        for row in r:
            n = (row.get("Name") or "").strip()
            if n:
                names.append(n)
    return names


def read_filesets_csv(path: Path) -> list[dict[str, str | bool | None]]:
    """Rows from filesets.csv: Name, Channels; optional Channel versions, Used in show."""
    out: list[dict[str, str | bool | None]] = []
    required = {"Name", "Channels"}
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        fn = set(r.fieldnames or [])
        if not required.issubset(fn):
            raise SystemExit(f"{path}: need columns {sorted(required)}")
        has_used_col = "Used in show" in fn
        has_cv_col = "Channel versions" in fn
        for row in r:
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            used_val: bool | None
            if has_used_col:
                used_val = parse_csv_bool(row.get("Used in show"))
            else:
                used_val = None
            cv_cell = (row.get("Channel versions") or "").strip() if has_cv_col else ""
            out.append(
                {
                    "Name": name,
                    "Channels": (row.get("Channels") or "").strip(),
                    "Channel versions": cv_cell,
                    "_has_channel_versions_csv": has_cv_col,
                    "_has_used_in_show_csv": has_used_col,
                    "_used_in_show": used_val,
                }
            )
    return out


def read_cues_csv(path: Path) -> tuple[list[dict[str, str]], int]:
    """Return (rows with non-empty CUE_NUMBER, count of skipped empty rows)."""
    rows: list[dict[str, str]] = []
    skipped_empty = 0
    required = {"CUE_NUMBER", "Track", "CUE_NAME", "Filesets"}
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        fn = set(r.fieldnames or [])
        if not r.fieldnames or not required.issubset(fn):
            raise SystemExit(f"{path}: need columns {sorted(required)}")
        for row in r:
            rec = {k: (row.get(k) or "").strip() for k in required}
            rec["Media"] = (row.get("Media") or "").strip() if "Media" in fn else ""
            if not rec["CUE_NUMBER"]:
                skipped_empty += 1
                continue
            rows.append(rec)
    return rows, skipped_empty


def parse_comma_separated_cell(cell: str) -> list[str]:
    """Split CSV cell (comma-separated tokens, no spaces after commas)."""
    if not cell:
        return []
    return [p.strip() for p in cell.split(",") if p.strip()]


def parse_writable_fields(env_val: str) -> list[str]:
    """Fields allowed on PATCH (must not include manual-only fields)."""
    raw = (env_val or "Track,CUE_NAME,Filesets").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def env_flag_true(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on", "y")


def build_patch_fields(
    full_row_fields: dict,
    writable: list[str],
) -> dict:
    """Subset of fields for update only."""
    return {k: v for k, v in full_row_fields.items() if k in writable}


def cue_primary_from_fields(
    fields: dict, cue_primary_field: str
) -> str | None:
    key = fields.get(cue_primary_field)
    if isinstance(key, str) and key.strip():
        return key.strip()
    if isinstance(key, (int, float)):
        return str(key)
    return None


def airtable_cue_name(fields: dict, cue_name_field: str) -> str:
    v = fields.get(cue_name_field)
    if isinstance(v, str):
        return v.strip()
    if v is None:
        return ""
    return str(v).strip()


def notes_field_nonempty(fields: dict, notes_field: str) -> bool:
    """True if Notes (or linked notes) has content — record should be kept when orphaned."""
    val = fields.get(notes_field)
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, str):
        return val.strip() != ""
    return bool(val)


def main() -> None:
    load_dotenv_if_present()

    p = argparse.ArgumentParser(
        description="Sync media + channels + filesets + cues CSVs to Airtable."
    )
    p.add_argument("--media-csv", type=Path, default=DEFAULT_MEDIA_CSV)
    p.add_argument("--channels-csv", type=Path, default=DEFAULT_CHANNELS_CSV)
    p.add_argument("--filesets-csv", type=Path, default=DEFAULT_FILESETS_CSV)
    p.add_argument("--cues-csv", type=Path, default=DEFAULT_CUES_CSV)
    p.add_argument("--dry-run", action="store_true", help="Print plan only; no API calls")
    args = p.parse_args()

    token = os.environ.get("AIRTABLE_TOKEN", "").strip()
    base_id = os.environ.get("AIRTABLE_BASE_ID", "").strip()
    sync_media = env_flag_true(os.environ.get("AIRTABLE_SYNC_MEDIA"))
    media_table = os.environ.get("AIRTABLE_MEDIA_TABLE", "Media").strip()
    media_name_field = os.environ.get("AIRTABLE_MEDIA_NAME_FIELD", "Name").strip()
    media_version_field = os.environ.get(
        "AIRTABLE_MEDIA_VERSION_FIELD", "Version"
    ).strip()
    media_used_field = os.environ.get(
        "AIRTABLE_MEDIA_USED_FIELD", "Used in show"
    ).strip()
    media_on_disk_field = os.environ.get(
        "AIRTABLE_MEDIA_ON_DISK_FIELD", "On disk"
    ).strip()
    _mw = os.environ.get("AIRTABLE_MEDIA_WRITABLE_FIELDS", "").strip()
    if not _mw:
        media_writable_fields = ["Version", "Used in show", "On disk"]
    else:
        media_writable_fields = [x.strip() for x in _mw.split(",") if x.strip()]
    media_writable_fields = [f for f in media_writable_fields if f != media_name_field]
    channels_table = os.environ.get("AIRTABLE_CHANNELS_TABLE", "Channels").strip()
    channels_name_field = os.environ.get(
        "AIRTABLE_CHANNELS_NAME_FIELD", "Name"
    ).strip()
    filesets_table = os.environ.get("AIRTABLE_FILESETS_TABLE", "Filesets").strip()
    filesets_name_field = os.environ.get(
        "AIRTABLE_FILESETS_NAME_FIELD", "Name"
    ).strip()
    filesets_channels_field = os.environ.get(
        "AIRTABLE_FILESETS_CHANNELS_FIELD", "Channels"
    ).strip()
    filesets_media_field = os.environ.get(
        "AIRTABLE_FILESETS_MEDIA_FIELD", "Media"
    ).strip()
    filesets_used_field = os.environ.get(
        "AIRTABLE_FILESETS_USED_FIELD", "Used in show"
    ).strip()
    filesets_channel_versions_field = os.environ.get(
        "AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD", "Channel versions"
    ).strip()
    _fw = os.environ.get("AIRTABLE_FILESETS_WRITABLE_FIELDS", "").strip()
    if not _fw:
        # Do not PATCH/POST Filesets.Media by default — empty [] often 422s or is unwanted.
        filesets_writable_fields = [
            "Channels",
            "Used in show",
            filesets_channel_versions_field,
        ]
    else:
        filesets_writable_fields = [x.strip() for x in _fw.split(",") if x.strip()]
    cues_table = os.environ.get("AIRTABLE_CUES_TABLE", "Cues").strip()
    cue_media_field = os.environ.get("AIRTABLE_CUE_MEDIA_FIELD", "Media").strip()
    cue_filesets_field = os.environ.get(
        "AIRTABLE_CUE_FILESETS_FIELD", "Filesets"
    ).strip()
    cue_primary_field = os.environ.get(
        "AIRTABLE_CUE_PRIMARY_FIELD", "CUE_NUMBER"
    ).strip()
    writable_fields = parse_writable_fields(
        os.environ.get("AIRTABLE_CUE_WRITABLE_FIELDS", "")
    )
    # Never PATCH the primary field by mistake
    writable_fields = [f for f in writable_fields if f != cue_primary_field]
    notes_airtable_field = os.environ.get(
        "AIRTABLE_CUE_NOTES_FIELD", "Notes"
    ).strip()

    if not args.dry_run and (not token or not base_id):
        print(
            "Set AIRTABLE_TOKEN and AIRTABLE_BASE_ID (or use --dry-run).",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.dry_run and token.startswith("pat") and len(token) < 40:
        print(
            "Warning: AIRTABLE_TOKEN looks too short. Airtable PATs are long; copy the "
            "full token from https://airtable.com/create/tokens (401 = bad/missing token).",
            file=sys.stderr,
        )

    missing_inputs: list[str] = []
    if sync_media and not args.media_csv.is_file():
        missing_inputs.append(str(args.media_csv))
    if not args.channels_csv.is_file():
        missing_inputs.append(str(args.channels_csv))
    if not args.filesets_csv.is_file():
        missing_inputs.append(str(args.filesets_csv))
    if not args.cues_csv.is_file():
        missing_inputs.append(str(args.cues_csv))
    if missing_inputs:
        print(
            "Missing CSV(s): " + "; ".join(missing_inputs),
            file=sys.stderr,
        )
        print(
            "Run export_cues_csv.py first, or pass --channels-csv / --filesets-csv / "
            "--cues-csv. Set AIRTABLE_SYNC_MEDIA=1 only if you also supply media.csv.",
            file=sys.stderr,
        )
        sys.exit(1)

    media_by_name: dict[str, dict[str, str]] = {}
    media_csv_cols: frozenset[str] = frozenset()
    if sync_media:
        media_rows, media_csv_cols = read_media_csv(args.media_csv)
        for r in media_rows:
            n = (r.get("Name") or "").strip()
            if n:
                media_by_name[n] = r
    channel_names = read_channels_csv(args.channels_csv)
    fileset_rows = read_filesets_csv(args.filesets_csv)
    cue_rows, skipped_cues = read_cues_csv(args.cues_csv)

    needed_channel_codes: set[str] = set(channel_names)
    for fr in fileset_rows:
        needed_channel_codes.update(
            parse_comma_separated_cell(str(fr["Channels"]))
        )

    needed_media_names: set[str] = set(media_by_name.keys())
    if sync_media:
        for cr in cue_rows:
            needed_media_names.update(parse_comma_separated_cell(cr["Media"]))

    if args.dry_run:
        print(
            f"Would sync {len(needed_channel_codes)} Channels, "
            f"{len(fileset_rows)} Filesets, {len(cue_rows)} Cues"
        )
        if sync_media:
            print(f"  (Also Media: {len(needed_media_names)} names — AIRTABLE_SYNC_MEDIA is on)")
        else:
            print("  (Media table skipped — set AIRTABLE_SYNC_MEDIA=1 + media.csv to enable)")
        if skipped_cues:
            print(f"  (Skipped {skipped_cues} cue rows with empty CUE_NUMBER in CSV)")
        print(f"  Base: {base_id or '(not set)'}")
        print(
            f"  Tables: {channels_table!r}, {filesets_table!r}, {cues_table!r}"
            + (f", {media_table!r}" if sync_media else "")
        )
        print(f"  Channels primary field: {channels_name_field!r}")
        print(
            f"  Filesets.{filesets_channels_field!r} → {channels_table!r}; "
            f"text field {filesets_channel_versions_field!r}"
        )
        print(f"  Filesets primary field: {filesets_name_field!r}")
        print(
            f"  Cues.{cue_filesets_field!r} → {filesets_table!r}"
            + (
                f"; Cues.{cue_media_field!r} (linked records)"
                if cue_media_field in writable_fields
                else ""
            )
        )
        print(f"  Cues primary field: {cue_primary_field!r}")
        print(f"  Cues PATCH fields only: {writable_fields}")
        if sync_media:
            print(f"  Media PATCH fields: {media_writable_fields}")
        print(f"  Filesets PATCH fields: {filesets_writable_fields}")
        print(
            f"  Orphan cleanup: name-matched primary migration; "
            f"keep if {notes_airtable_field!r} non-empty else delete"
        )
        return

    media_name_to_id: dict[str, str] = {}
    media_id_to_fields: dict[str, dict] = {}

    if sync_media:
        # --- Media: ensure every Name exists (cue link targets when CSV lists Media) ---
        print("Fetching existing Media records...")
        media_fetch_fields = [media_name_field, *media_writable_fields]
        media_fetch_fields = list(dict.fromkeys(f for f in media_fetch_fields if f))
        media_recs = list_all_records(
            base_id, media_table, token, fields=media_fetch_fields
        )
        for rec in media_recs:
            rid = rec["id"]
            fields = rec.get("fields") or {}
            media_id_to_fields[rid] = fields
            val = fields.get(media_name_field)
            if isinstance(val, str) and val.strip():
                media_name_to_id[val.strip()] = rid

        missing_media = sorted(needed_media_names - set(media_name_to_id.keys()))
        print(f"Media: {len(media_name_to_id)} existing, {len(missing_media)} to create")
        if missing_media:
            create_payloads: list[dict] = []
            for n in missing_media:
                row = media_by_name.get(n, {})
                flds: dict = {media_name_field: n}
                flds.update(
                    media_extra_fields_from_row(
                        row,
                        media_csv_cols,
                        media_writable_fields,
                        media_version_field,
                        media_used_field,
                        media_on_disk_field,
                    )
                )
                create_payloads.append(flds)
            created_m = create_records(base_id, media_table, token, create_payloads)
            merge_created_by_primary_field(
                created_m, media_name_field, media_name_to_id
            )
            for rec in created_m:
                rid = rec.get("id")
                if rid:
                    media_id_to_fields[rid] = rec.get("fields") or {}
            print(f"  Merged {len(created_m)} Media record(s) from POST response.")

        to_update_media: list[tuple[str, dict]] = []
        media_patch_skipped = 0
        for n in sorted(needed_media_names):
            rid = media_name_to_id.get(n)
            if not rid:
                continue
            row = media_by_name.get(n, {})
            patch = media_extra_fields_from_row(
                row,
                media_csv_cols,
                media_writable_fields,
                media_version_field,
                media_used_field,
                media_on_disk_field,
            )
            if not patch:
                continue
            ex = media_id_to_fields.get(rid, {})
            if patch_redundant_with_existing(ex, patch):
                media_patch_skipped += 1
            else:
                to_update_media.append((rid, patch))
        if to_update_media:
            print(
                f"Media: {len(to_update_media)} to update "
                f"({media_patch_skipped} unchanged skipped PATCH)"
            )
            update_records(base_id, media_table, token, to_update_media)
        elif media_patch_skipped:
            print(f"Media: {media_patch_skipped} unchanged (skipped PATCH)")
    else:
        print("Skipping Media table (AIRTABLE_SYNC_MEDIA not set).")

    # --- Channels: ensure every Name exists (Filesets link targets) ---
    print("Fetching existing Channels records...")
    channel_recs = list_all_records(
        base_id, channels_table, token, fields=[channels_name_field]
    )
    channel_name_to_id: dict[str, str] = {}
    for rec in channel_recs:
        rid = rec["id"]
        fields = rec.get("fields") or {}
        val = fields.get(channels_name_field)
        if isinstance(val, str) and val.strip():
            channel_name_to_id[val.strip()] = rid

    missing_ch = sorted(needed_channel_codes - set(channel_name_to_id.keys()))
    print(f"Channels: {len(channel_name_to_id)} existing, {len(missing_ch)} to create")
    if missing_ch:
        created_ch = create_records(
            base_id,
            channels_table,
            token,
            [{channels_name_field: code} for code in missing_ch],
        )
        merge_created_by_primary_field(
            created_ch, channels_name_field, channel_name_to_id
        )
        print(f"  Merged {len(created_ch)} Channels record(s) from POST response.")

    # --- Filesets: upsert Channels, Used in show, Channel versions (Media only if writable) ---
    print("Fetching existing Filesets records...")
    fs_fetch_fields = [filesets_name_field, filesets_channels_field]
    if filesets_media_field in filesets_writable_fields:
        fs_fetch_fields.append(filesets_media_field)
    if filesets_used_field in filesets_writable_fields:
        fs_fetch_fields.append(filesets_used_field)
    if filesets_channel_versions_field in filesets_writable_fields:
        fs_fetch_fields.append(filesets_channel_versions_field)
    fs_fetch_fields = list(dict.fromkeys(fs_fetch_fields))
    fileset_recs = list_all_records(
        base_id, filesets_table, token, fields=fs_fetch_fields
    )
    fileset_name_to_id: dict[str, str] = {}
    fileset_id_to_fields: dict[str, dict] = {}
    for rec in fileset_recs:
        rid = rec["id"]
        fields = rec.get("fields") or {}
        fileset_id_to_fields[rid] = fields
        val = fields.get(filesets_name_field)
        if isinstance(val, str) and val.strip():
            fileset_name_to_id[val.strip()] = rid

    to_create_fs: list[dict] = []
    to_update_fs: list[tuple[str, dict]] = []
    unresolved_ch: list[tuple[str, str]] = []
    for fr in fileset_rows:
        n = fr["Name"]
        ch_link_ids: list[str] = []
        for code in parse_comma_separated_cell(fr["Channels"]):
            cid = channel_name_to_id.get(code)
            if cid:
                ch_link_ids.append(cid)
            else:
                unresolved_ch.append((n, code))
        body: dict = {}
        if filesets_channels_field in filesets_writable_fields:
            body[filesets_channels_field] = ch_link_ids
        if filesets_media_field in filesets_writable_fields:
            body[filesets_media_field] = []
        if (
            filesets_used_field in filesets_writable_fields
            and fr.get("_has_used_in_show_csv")
        ):
            u = fr.get("_used_in_show")
            if u is not None:
                body[filesets_used_field] = u
        if (
            filesets_channel_versions_field in filesets_writable_fields
            and fr.get("_has_channel_versions_csv")
        ):
            cv = fr.get("Channel versions")
            body[filesets_channel_versions_field] = (
                cv if isinstance(cv, str) else (cv or "")
            )
        if n in fileset_name_to_id:
            rid = fileset_name_to_id[n]
            ex = fileset_id_to_fields.get(rid, {})
            if not patch_redundant_with_existing(ex, body):
                to_update_fs.append((rid, body))
        else:
            to_create_fs.append({filesets_name_field: n, **body})

    if unresolved_ch:
        print(
            "WARNING: missing Channels rows for some filesets (skipped those links):",
            file=sys.stderr,
        )
        for fsname, code in unresolved_ch[:15]:
            print(
                f"  Fileset={fsname!r} missing Channel Name={code!r}",
                file=sys.stderr,
            )
        if len(unresolved_ch) > 15:
            print(f"  ... and {len(unresolved_ch) - 15} more", file=sys.stderr)

    n_fs_skipped = len(fileset_rows) - len(to_create_fs) - len(to_update_fs)
    print(
        f"Filesets: {len(fileset_name_to_id)} existing, "
        f"{len(to_create_fs)} to create, {len(to_update_fs)} to update "
        f"({filesets_channels_field!r}, {filesets_used_field!r}, "
        f"{filesets_channel_versions_field!r}"
        + (
            f"; {filesets_media_field!r} when in AIRTABLE_FILESETS_WRITABLE_FIELDS"
            if filesets_media_field in filesets_writable_fields
            else ""
        )
        + ")"
        + (
            f", {n_fs_skipped} unchanged (skipped PATCH)"
            if n_fs_skipped
            else ""
        )
    )
    if to_create_fs:
        created_fs = create_records(
            base_id, filesets_table, token, to_create_fs
        )
        merge_created_by_primary_field(
            created_fs, filesets_name_field, fileset_name_to_id
        )
        for rec in created_fs:
            rid = rec.get("id")
            if rid:
                fileset_id_to_fields[rid] = rec.get("fields") or {}
        print(f"  Merged {len(created_fs)} Filesets record(s) from POST response.")

    if to_update_fs:
        update_records(base_id, filesets_table, token, to_update_fs)

    # --- Cues: upsert by CUE_NUMBER (primary), orphan migration + cleanup ---
    csv_numbers = {row["CUE_NUMBER"] for row in cue_rows}
    cue_fetch_fields = list(
        {
            cue_primary_field,
            "CUE_NAME",
            notes_airtable_field,
            *writable_fields,
        }
    )
    print("Fetching existing Cues...")
    cue_recs = list_all_records(
        base_id, cues_table, token, fields=cue_fetch_fields
    )
    number_to_id: dict[str, str] = {}
    cue_id_to_fields: dict[str, dict] = {}
    for rec in cue_recs:
        fields = rec.get("fields") or {}
        cue_id_to_fields[rec["id"]] = fields
        pval = cue_primary_from_fields(fields, cue_primary_field)
        if pval:
            number_to_id[pval] = rec["id"]

    orphans: list[dict] = []
    for rec in cue_recs:
        fields = rec.get("fields") or {}
        pval = cue_primary_from_fields(fields, cue_primary_field)
        if pval and pval not in csv_numbers:
            orphans.append({"id": rec["id"], "num": pval, "fields": fields})

    enriched: list[dict] = []
    unresolved_fs: list[tuple[str, str]] = []
    unresolved_med: list[tuple[str, str]] = []
    for row in cue_rows:
        cnum = row["CUE_NUMBER"]
        fs_link_ids: list[str] = []
        for fsname in parse_comma_separated_cell(row["Filesets"]):
            fid = fileset_name_to_id.get(fsname)
            if fid:
                fs_link_ids.append(fid)
            else:
                unresolved_fs.append((cnum, fsname))
        full_fields: dict = {
            "Track": row["Track"],
            "CUE_NAME": row["CUE_NAME"],
            cue_filesets_field: fs_link_ids,
        }
        if cue_media_field in writable_fields:
            med_link_ids: list[str] = []
            if sync_media:
                for mname in parse_comma_separated_cell(row["Media"]):
                    mid = media_name_to_id.get(mname)
                    if mid:
                        med_link_ids.append(mid)
                    else:
                        unresolved_med.append((cnum, mname))
            else:
                med_link_ids = []
            full_fields[cue_media_field] = med_link_ids
        enriched.append({"cnum": cnum, "full_fields": full_fields})

    used_orphan_ids: set[str] = set()
    to_migrate: list[tuple[str, dict]] = []
    migrated_record_ids: set[str] = set()

    for block in enriched:
        cnum = block["cnum"]
        if cnum in number_to_id:
            continue
        csv_name = (block["full_fields"].get("CUE_NAME") or "").strip()
        if not csv_name:
            continue
        for orb in orphans:
            if orb["id"] in used_orphan_ids:
                continue
            if airtable_cue_name(orb["fields"], "CUE_NAME") != csv_name:
                continue
            old_num = orb["num"]
            rid = orb["id"]
            used_orphan_ids.add(rid)
            migrated_record_ids.add(rid)
            if number_to_id.get(old_num) == rid:
                del number_to_id[old_num]
            number_to_id[cnum] = rid
            migrate_payload = {
                cue_primary_field: cnum,
                **build_patch_fields(block["full_fields"], writable_fields),
            }
            to_migrate.append((rid, migrate_payload))
            print(
                f"Migrated cue {old_num!r} -> {cnum!r} "
                f"(matching CUE_NAME={csv_name!r})"
            )
            break

    for orb in orphans:
        if orb["id"] in used_orphan_ids:
            continue
        num = orb["num"]
        cname = airtable_cue_name(orb["fields"], "CUE_NAME")
        if notes_field_nonempty(orb["fields"], notes_airtable_field):
            disp = cname if cname else "(empty)"
            print(
                f"Keeping removed cue number {num!r} (CUE_NAME={disp!r}) — "
                f"{notes_airtable_field!r} is not empty; not deleting."
            )

    orphans_to_delete = [
        orb
        for orb in orphans
        if orb["id"] not in used_orphan_ids
        and not notes_field_nonempty(orb["fields"], notes_airtable_field)
    ]

    if to_migrate:
        print(f"Cues: applying {len(to_migrate)} migration(s) (primary + fields)")
        update_records(base_id, cues_table, token, to_migrate)

    for orb in orphans_to_delete:
        num = orb["num"]
        cname = airtable_cue_name(orb["fields"], "CUE_NAME")
        disp = cname if cname else "(empty)"
        print(f"Deleting orphaned cue {num!r} (CUE_NAME={disp!r})")
        delete_record(base_id, cues_table, token, orb["id"])

    to_create: list[dict] = []
    to_update: list[tuple[str, dict]] = []
    cue_patch_skipped = 0

    for block in enriched:
        cnum = block["cnum"]
        full_fields = block["full_fields"]
        if cnum in number_to_id:
            rid = number_to_id[cnum]
            if rid in migrated_record_ids:
                continue
            patch = build_patch_fields(full_fields, writable_fields)
            ex = cue_id_to_fields.get(rid, {})
            if patch_redundant_with_existing(ex, patch):
                cue_patch_skipped += 1
            else:
                to_update.append((rid, patch))
        else:
            new_f = {cue_primary_field: cnum, **full_fields}
            to_create.append(new_f)

    if skipped_cues:
        print(f"Skipped {skipped_cues} cue row(s) with empty CUE_NUMBER in CSV.")

    if unresolved_fs:
        print(
            "WARNING: missing Filesets rows for some cue links (skipped those links):",
            file=sys.stderr,
        )
        for cnum, fn in unresolved_fs[:15]:
            print(
                f"  CUE_NUMBER={cnum!r} missing Fileset Name={fn!r}",
                file=sys.stderr,
            )
        if len(unresolved_fs) > 15:
            print(f"  ... and {len(unresolved_fs) - 15} more", file=sys.stderr)

    if unresolved_med:
        print(
            "WARNING: missing Media rows for some cue links (skipped those links):",
            file=sys.stderr,
        )
        for cnum, mn in unresolved_med[:15]:
            print(
                f"  CUE_NUMBER={cnum!r} missing Media Name={mn!r}",
                file=sys.stderr,
            )
        if len(unresolved_med) > 15:
            print(f"  ... and {len(unresolved_med) - 15} more", file=sys.stderr)

    print(
        f"Cues: {len(to_create)} to create, {len(to_update)} to update"
        + (
            f", {cue_patch_skipped} unchanged (skipped PATCH)"
            if cue_patch_skipped
            else ""
        )
    )
    if to_create:
        created_cues = create_records(base_id, cues_table, token, to_create)
        merge_created_by_primary_field(
            created_cues,
            cue_primary_field,
            number_to_id,
            primary_reader=cue_primary_from_fields,
        )
        print(f"  Merged {len(created_cues)} Cues record(s) from POST response.")
    if to_update:
        update_records(base_id, cues_table, token, to_update)

    print("Done.")


if __name__ == "__main__":
    main()
