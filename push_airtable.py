#!/usr/bin/env python3
"""
Push exports/media.csv and exports/cues.csv to Airtable via the REST API.

Prerequisites:
  1. Run: python3 export_cues_csv.py
  2. Create an Airtable base with tables/fields matching docs/AIRTABLE.md
  3. Create a Personal Access Token (PAT) with data read/write on the base.

Environment variables:
  AIRTABLE_TOKEN       Required. PAT (starts with pat...)
  AIRTABLE_BASE_ID     Required. Base ID (starts with app...)

Optional:
  AIRTABLE_MEDIA_TABLE          Default: Media
  AIRTABLE_CUES_TABLE           Default: Cues
  AIRTABLE_MEDIA_NAME_FIELD     Primary field on Media table. Default: Name
  AIRTABLE_CUE_PRIMARY_FIELD    Primary field on Cues (CUE_NUMBER). Default: CUE_NUMBER
  AIRTABLE_CUE_WRITABLE_FIELDS  Comma-separated fields PATCH may update.
                                Default: Act,CUE_NAME,Media (never Description, Call, Page number, Notes)

Usage:
  export AIRTABLE_TOKEN=pat...
  export AIRTABLE_BASE_ID=app...
  python3 push_airtable.py --dry-run
  python3 push_airtable.py

  python3 push_airtable.py --media-csv exports/media.csv --cues-csv exports/cues.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MEDIA_CSV = ROOT / "exports" / "media.csv"
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
) -> None:
    for i in range(0, len(records_fields), BATCH):
        chunk = records_fields[i : i + BATCH]
        body = {"records": [{"fields": f} for f in chunk]}
        url = f"https://api.airtable.com/v0/{urllib.parse.quote(base_id)}/{urllib.parse.quote(table)}"
        time.sleep(REQUEST_DELAY_S)
        api_request("POST", url, token, body)


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


def read_media_csv(path: Path) -> list[str]:
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


def read_cues_csv(path: Path) -> tuple[list[dict[str, str]], int]:
    """Return (rows with non-empty CUE_NUMBER, count of skipped empty rows)."""
    rows: list[dict[str, str]] = []
    skipped_empty = 0
    required = {"CUE_NUMBER", "Act", "CUE_NAME", "Media"}
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or not required.issubset(set(r.fieldnames)):
            raise SystemExit(f"{path}: need columns {sorted(required)}")
        for row in r:
            rec = {k: (row.get(k) or "").strip() for k in required}
            if not rec["CUE_NUMBER"]:
                skipped_empty += 1
                continue
            rows.append(rec)
    return rows, skipped_empty


def parse_media_links(cell: str) -> list[str]:
    """Split Media cell from CSV (comma-separated Names, no spaces)."""
    if not cell:
        return []
    return [p.strip() for p in cell.split(",") if p.strip()]


def parse_writable_fields(env_val: str) -> list[str]:
    """Fields allowed on PATCH (must not include manual-only fields)."""
    raw = (env_val or "Act,CUE_NAME,Media").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_patch_fields(
    full_row_fields: dict,
    writable: list[str],
) -> dict:
    """Subset of fields for update only."""
    return {k: v for k, v in full_row_fields.items() if k in writable}


def main() -> None:
    load_dotenv_if_present()

    p = argparse.ArgumentParser(description="Sync media.csv + cues.csv to Airtable.")
    p.add_argument("--media-csv", type=Path, default=DEFAULT_MEDIA_CSV)
    p.add_argument("--cues-csv", type=Path, default=DEFAULT_CUES_CSV)
    p.add_argument("--dry-run", action="store_true", help="Print plan only; no API calls")
    args = p.parse_args()

    token = os.environ.get("AIRTABLE_TOKEN", "").strip()
    base_id = os.environ.get("AIRTABLE_BASE_ID", "").strip()
    media_table = os.environ.get("AIRTABLE_MEDIA_TABLE", "Media").strip()
    cues_table = os.environ.get("AIRTABLE_CUES_TABLE", "Cues").strip()
    name_field = os.environ.get("AIRTABLE_MEDIA_NAME_FIELD", "Name").strip()
    cue_primary_field = os.environ.get(
        "AIRTABLE_CUE_PRIMARY_FIELD", "CUE_NUMBER"
    ).strip()
    writable_fields = parse_writable_fields(
        os.environ.get("AIRTABLE_CUE_WRITABLE_FIELDS", "")
    )
    # Never PATCH the primary field by mistake
    writable_fields = [f for f in writable_fields if f != cue_primary_field]

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

    if not args.media_csv.is_file() or not args.cues_csv.is_file():
        print("Run export_cues_csv.py first, or pass --media-csv / --cues-csv.", file=sys.stderr)
        sys.exit(1)

    media_names = read_media_csv(args.media_csv)
    cue_rows, skipped_cues = read_cues_csv(args.cues_csv)

    if args.dry_run:
        print(f"Would sync {len(media_names)} Media names, {len(cue_rows)} Cues")
        if skipped_cues:
            print(f"  (Skipped {skipped_cues} cue rows with empty CUE_NUMBER in CSV)")
        print(f"  Base: {base_id or '(not set)'}")
        print(f"  Tables: {media_table!r}, {cues_table!r}")
        print(f"  Media primary field: {name_field!r}")
        print(f"  Cues primary field: {cue_primary_field!r}")
        print(f"  PATCH fields only: {writable_fields}")
        return

    # --- Media: ensure every Name exists ---
    print("Fetching existing Media records...")
    media_recs = list_all_records(base_id, media_table, token, fields=[name_field])
    name_to_id: dict[str, str] = {}
    for rec in media_recs:
        rid = rec["id"]
        fields = rec.get("fields") or {}
        val = fields.get(name_field)
        if isinstance(val, str) and val.strip():
            name_to_id[val.strip()] = rid

    missing = [n for n in media_names if n not in name_to_id]
    print(f"Media: {len(name_to_id)} existing, {len(missing)} to create")
    if missing:
        create_records(
            base_id,
            media_table,
            token,
            [{name_field: n} for n in missing],
        )
        print("Re-fetching Media after create...")
        media_recs = list_all_records(base_id, media_table, token, fields=[name_field])
        name_to_id.clear()
        for rec in media_recs:
            val = (rec.get("fields") or {}).get(name_field)
            if isinstance(val, str) and val.strip():
                name_to_id[val.strip()] = rec["id"]

    # --- Cues: upsert by CUE_NUMBER (primary) ---
    print("Fetching existing Cues...")
    cue_recs = list_all_records(
        base_id, cues_table, token, fields=[cue_primary_field]
    )
    number_to_id: dict[str, str] = {}
    for rec in cue_recs:
        fields = rec.get("fields") or {}
        key = fields.get(cue_primary_field)
        if isinstance(key, str) and key.strip():
            number_to_id[key.strip()] = rec["id"]
        elif isinstance(key, (int, float)):
            # Airtable may return number field types
            number_to_id[str(key)] = rec["id"]

    to_create: list[dict] = []
    to_update: list[tuple[str, dict]] = []
    unresolved: list[tuple[str, str]] = []

    for row in cue_rows:
        cnum = row["CUE_NUMBER"]
        media_ids: list[str] = []
        for mname in parse_media_links(row["Media"]):
            mid = name_to_id.get(mname)
            if mid:
                media_ids.append(mid)
            else:
                unresolved.append((cnum, mname))

        full_fields = {
            "Act": row["Act"],
            "CUE_NAME": row["CUE_NAME"],
            "Media": media_ids,
        }
        if cnum in number_to_id:
            patch = build_patch_fields(full_fields, writable_fields)
            to_update.append((number_to_id[cnum], patch))
        else:
            new_f = {cue_primary_field: cnum, **full_fields}
            to_create.append(new_f)

    if skipped_cues:
        print(f"Skipped {skipped_cues} cue row(s) with empty CUE_NUMBER in CSV.")

    if unresolved:
        print("WARNING: missing Media rows for some links (skipped those links):", file=sys.stderr)
        for cnum, mn in unresolved[:15]:
            print(f"  CUE_NUMBER={cnum!r} missing Media Name={mn!r}", file=sys.stderr)
        if len(unresolved) > 15:
            print(f"  ... and {len(unresolved) - 15} more", file=sys.stderr)

    print(f"Cues: {len(to_create)} to create, {len(to_update)} to update")
    if to_create:
        create_records(base_id, cues_table, token, to_create)
    if to_update:
        update_records(base_id, cues_table, token, to_update)

    print("Done.")


if __name__ == "__main__":
    main()
