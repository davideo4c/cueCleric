# Airtable integration

## 1. Export CSVs from Disguise data

From the project root:

```bash
python3 export_cues_csv.py
```

This writes:

| File | Purpose |
|------|---------|
| `exports/media.csv` | One row per unique media file. Column **`Name`** = primary field in Airtable (must match link text exactly). |
| `exports/cues.csv` | One row per cue that has a **non-empty `CUE_NUMBER`** (from the cue tag). Columns: **`CUE_NUMBER`**, **`Act`**, **`CUE_NAME`**, **`Media`** (comma-separated `Name` values, no spaces after commas). |

**Omitted from `cues.csv`:** cues with no tag / empty computed `CUE_NUMBER` (count is printed when you export).

**Fatal error:** if the same non-empty `CUE_NUMBER` appears twice (Airtable primary must be globally unique).

Optional legacy file (newline-separated list in one column):

```bash
python3 export_cues_csv.py --combined-out exports/cues_with_videos.csv
```

---

## 2. Create the Airtable base (once)

### Table **Media**

| Field | Type | Notes |
|-------|------|--------|
| **Name** | Primary field (Single line text) | Must match the `Name` column in `media.csv`. |

### Table **Cues**

| Field | Type | Notes |
|-------|------|--------|
| **CUE_NUMBER** | **Primary field** (Single line text) | Computed decimal from tag, e.g. `26.5`. Globally unique. |
| **Act** | Single line text (or Single select) | Updated by `push_airtable.py`. |
| **CUE_NAME** | Single line text | Updated by script. |
| **Media** | **Link to another record** → Media | Allow **multiple** links. Updated by script. |
| **Description** | Single line text | Manual only — **not** written by the script. |
| **Call** | Single line text | Manual only — **not** written by the script. |
| **Page number** | Single line text | Manual only — **not** written by the script. |
| **Notes** | Link to **Notes** table (or your notes table) | Manual only — **not** written by the script. |

Field names for script-managed columns must match **`Act`**, **`CUE_NAME`**, **`Media`** exactly (or set env overrides for Media; see below).

---

## 3. First-time import (optional, no code)

If you prefer the UI over the API:

1. Import **`media.csv`** into the **Media** table (map `Name` → primary).
2. Import **`cues.csv`** into **Cues**; map **`Media`** to the linked field. Airtable resolves links by **primary field** (`Name`).

---

## 4. Push updates via API (repeatable workflow)

### Get credentials

1. [Airtable → Create token](https://airtable.com/create/tokens) with **data.records:read/write** for your base.
   - Copy the **entire** token string (Personal Access Tokens are long). A short or truncated value causes **401 Authentication required**.
2. Base ID: **Help → API documentation** in the base (starts with `app…`).

If you still get 401 after fixing `.env`, run `unset AIRTABLE_TOKEN` in the terminal (or open a new shell) so an old export doesn’t override `.env`. The script prefers values from `.env` when the file is loaded.

### Configure environment

Copy `.env.example` to `.env` and fill in values, **or** export in your shell:

```bash
export AIRTABLE_TOKEN="patxxxxxxxx"
export AIRTABLE_BASE_ID="appxxxxxxxx"
```

Optional overrides:

| Variable | Default |
|----------|---------|
| `AIRTABLE_MEDIA_TABLE` | `Media` |
| `AIRTABLE_CUES_TABLE` | `Cues` |
| `AIRTABLE_MEDIA_NAME_FIELD` | `Name` |
| `AIRTABLE_CUE_PRIMARY_FIELD` | `CUE_NUMBER` |
| `AIRTABLE_CUE_WRITABLE_FIELDS` | `Act,CUE_NAME,Media` (fields included in **PATCH** only; primary is never PATCHed) |

### Sync

```bash
python3 export_cues_csv.py
python3 push_airtable.py --dry-run   # optional: show counts only
python3 push_airtable.py
```

What **`push_airtable.py`** does:

1. Ensures every **Media** `Name` from `media.csv` exists (creates missing rows in batches of 10).
2. For each **Cue** in `cues.csv` (non-empty `CUE_NUMBER` only):
   - If **`CUE_NUMBER`** already exists → **PATCH** with **only** `Act`, `CUE_NAME`, `Media` (same set as `AIRTABLE_CUE_WRITABLE_FIELDS`). **Description**, **Call**, **Page number**, **Notes** are never sent.
   - Else → **POST** with **`CUE_NUMBER`** (primary) + `Act` + `CUE_NAME` + `Media`.

Media links are sent as **record IDs** resolved from the `Name` → `record id` map.

### Migrating from `Cue_Key`

If you used an older schema with **`Cue_Key`**, remove that field from Airtable (or ignore it), set **CUE_NUMBER** as primary, and re-import or let the script **create** new rows. Existing rows keyed only by `Cue_Key` will not match until **`CUE_NUMBER`** values align.

### Rate limits

The script sleeps ~0.22s between requests. Large bases may take a few minutes on first run.

### Troubleshooting

- **HTTP 403/401**: Token scopes or base access.
- **Unknown field name**: Rename Airtable fields to match the tables above, or set `AIRTABLE_MEDIA_NAME_FIELD` / `AIRTABLE_CUE_PRIMARY_FIELD` if those differ.
- **Links missing**: `Media` cell in CSV must use the **exact** same string as `Media.Name`. Check warnings printed for unresolved names.

---

## 5. Suggested day-to-day workflow

1. Update Disguise exports in `assets/` (`all_content_table.txt`, `*cue_table*.txt`).
2. `python3 export_cues_csv.py`
3. `python3 push_airtable.py`

Commit CSVs to git if you want a history of exports; keep **`.env` out of git** (see `.gitignore`).
