# Airtable integration

## 1. Export CSVs from Disguise data

From the project root:

```bash
python3 export_cues_csv.py
```

This writes:

| File | Purpose |
|------|---------|
| `exports/media.csv` | One row per unique **media file** (verbose Disguise filename + optional `v###`). **`Name`** = primary — human-readable, per-file identity. |
| `exports/channels.csv` | One row per unique **channel** code. **`Name`** = primary (e.g. `C01`, `C21`, `C01B`). |
| `exports/filesets.csv` | **`Name`** = fileset key; **`Channels`** = comma-separated channel **`Name`**s (→ **Channels**); **`Media`** = comma-separated **`Media.Name`** values for every file in that fileset. |
| `exports/cues.csv` | **`CUE_NUMBER`**, **`Act`**, **`CUE_NAME`**, **`Media`** (comma-separated **`Media.Name`** — verbose), **`Filesets`** (comma-separated **Filesets.Name** — grouped). |

**File naming:** Assets like `000-021-c11-grids_and_guides.mov` parse as `NNN-NNN-<channel>-<description>`. The fileset key is `000-021-grids_and_guides`. **Cues** get both **Media** (actual files) and **Filesets** (logical groups).

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
| **Name** | Primary field (Single line text) | Full filename string from `media.csv` (must match **Cues.Media** and **Filesets.Media** link text). |

### Table **Channels**

| Field | Type | Notes |
|-------|------|--------|
| **Name** | Primary field (Single line text) | Channel code, e.g. `C02`, `C21`. Must match `channels.csv` and **Filesets.Channels** link text. |

### Table **Filesets**

| Field | Type | Notes |
|-------|------|--------|
| **Name** | Primary field (Single line text) | Must match `filesets.csv` (e.g. `000-021-grids_and_guides`). |
| **Channels** | **Link to another record** → Channels | Allow **multiple**. Updated by `push_airtable.py`. |
| **Media** | **Link to another record** → Media | Allow **multiple** — all media files that belong to this fileset. Updated by script. |
| **Cues** | *(inverse link)* | When you add **Filesets** on **Cues** (below), Airtable adds a reverse linked field on **Filesets** (often named **Cues**). **You do not PATCH this from the script** — it fills automatically from **Cues → Filesets**. |

### Table **Cues**

| Field | Type | Notes |
|-------|------|--------|
| **CUE_NUMBER** | **Primary field** (Single line text) | Computed decimal from tag, e.g. `26.5`. Globally unique. |
| **Act** | Single line text (or Single select) | Updated by `push_airtable.py`. |
| **CUE_NAME** | Single line text | Updated by script. |
| **Media** | **Link to another record** → Media | Allow **multiple** — verbose list of files for this cue. |
| **Filesets** | **Link to another record** → Filesets | Allow **multiple** — grouped filesets for this cue. |
| **Description** | Single line text | Manual only — **not** written by the script. |
| **Call** | Single line text | Manual only — **not** written by the script. |
| **Page number** | Single line text | Manual only — **not** written by the script. |
| **Notes** | Link to **Notes** table (or your notes table) | Manual only — **not** written by the script. |

Field names for script-managed columns must match **`Act`**, **`CUE_NAME`**, **`Media`**, **`Filesets`** (or set env overrides; see below).

---

## 3. First-time import (optional, no code)

If you prefer the UI over the API:

1. Import **`media.csv`** into **Media** (`Name` → primary).
2. Import **`channels.csv`** into **Channels** (`Name` → primary).
3. Import **`filesets.csv`** into **Filesets** (`Name` → primary; **`Channels`** → link → **Channels**; **`Media`** → link → **Media**).
4. Import **`cues.csv`** into **Cues** (**`Media`** → **Media**; **`Filesets`** → **Filesets**).

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
| `AIRTABLE_MEDIA_NAME_FIELD` | `Name` |
| `AIRTABLE_CHANNELS_TABLE` | `Channels` |
| `AIRTABLE_CHANNELS_NAME_FIELD` | `Name` |
| `AIRTABLE_FILESETS_TABLE` | `Filesets` |
| `AIRTABLE_FILESETS_NAME_FIELD` | `Name` |
| `AIRTABLE_FILESETS_CHANNELS_FIELD` | `Channels` (Filesets → Channels) |
| `AIRTABLE_FILESETS_MEDIA_FIELD` | `Media` (Filesets → Media) |
| `AIRTABLE_CUES_TABLE` | `Cues` |
| `AIRTABLE_CUE_MEDIA_FIELD` | `Media` (Cues → Media) |
| `AIRTABLE_CUE_FILESETS_FIELD` | `Filesets` |
| `AIRTABLE_CUE_PRIMARY_FIELD` | `CUE_NUMBER` |
| `AIRTABLE_CUE_WRITABLE_FIELDS` | `Act,CUE_NAME,Media,Filesets` |
| `AIRTABLE_CUE_NOTES_FIELD` | `Notes` (orphan cleanup) |

### Sync

```bash
python3 export_cues_csv.py
python3 push_airtable.py --dry-run   # optional: show counts only
python3 push_airtable.py
```

What **`push_airtable.py`** does:

1. **Media:** Ensures every **`Name`** from `media.csv` (plus names referenced on **Filesets** / **Cues** rows) exists in **Media**.
2. **Channels:** Ensures every channel **`Name`** from `channels.csv` (plus codes in **Filesets**) exists in **Channels**.
3. **Filesets:** For each `filesets.csv` row, **POST** or **PATCH** **`Channels`** and **`Media`** link fields (record IDs from channel codes and **Media.Name**).
4. **Cue orphans** (in Airtable but not in `cues.csv`): same as before — name-matched **migration** of primary **`CUE_NUMBER`**, else keep if **Notes** set or **delete**.
5. **Cues:** **POST**/**PATCH** with `Act`, `CUE_NAME`, **`Media`**, **`Filesets`** (per `AIRTABLE_CUE_WRITABLE_FIELDS`).

**Links** use **record IDs** resolved from primary **`Name`** strings. **Cues that use a fileset** appear on **Filesets** via Airtable’s **inverse** of **Cues.Filesets** (name that field e.g. **Cues** in the UI); the script only writes **Cues → Filesets**, not the reverse.

**API usage:** **Filesets** and **Cues** are **PATCH**ed only when linked fields or writable text differ from what was read at the start of the run (no-op rows are skipped). After **POST** creates, new record **ids** are taken from the **create response** instead of re-listing the whole table.

**Notes:** Migration by name is only attempted when the CSV **`CUE_NAME`** is non-empty, so cues with blank names are never matched to an orphan for primary reassignment.

### Migrating older bases

Add **Media**, **Channels**, link fields **Filesets.Channels**, **Filesets.Media**, **Cues.Media**, **Cues.Filesets**, then re-export and push (or import the four CSVs in order: media, channels, filesets, cues).

### Rate limits

The script sleeps ~0.22s between requests. Large bases may take a few minutes on first run.

### Troubleshooting

- **HTTP 403/401**: Token scopes or base access.
- **Unknown field name**: Rename fields or set `AIRTABLE_MEDIA_*` / `AIRTABLE_CHANNELS_*` / `AIRTABLE_FILESETS_*` / `AIRTABLE_CUE_*` env vars.
- **Links missing**: **Media.Name** / **Channels.Name** / **Filesets.Name** must match CSV cells exactly. Check stderr warnings.

---

## 5. Suggested day-to-day workflow

1. Update Disguise exports in `assets/` (`all_content_table.txt`, `*cue_table*.txt`).
2. `python3 export_cues_csv.py`
3. `python3 push_airtable.py`

Commit CSVs to git if you want a history of exports; keep **`.env` out of git** (see `.gitignore`).
