# Airtable integration

## 1. Export CSVs from Disguise data

From the project root:

```bash
python3 export_cues_csv.py
```

Optional: scan a **VideoFile** tree so **Filesets → Channel versions** reflects the latest `_vNNN` on disk per channel:

```bash
python3 export_cues_csv.py --video-file-dir "/path/to/VideoFile"
```

If you omit `--video-file-dir`, a **second folder picker** asks for the VideoFile root (starts in **`<input-dir>/VideoFile`** when that folder exists). **Cancel** skips the scan (version summary uses default **v001** per channel).

**No `media.csv`** is written — the default workflow does **not** grow a **Media** table in Airtable.

This writes:

| File | Purpose |
|------|---------|
| `exports/channels.csv` | One row per **channel** used by **filesets that appear in the show**. **`Name`** = primary (e.g. `C01`, `C21`). |
| `exports/filesets.csv` | **Only filesets referenced** in `all_content_table.txt` videos. Columns: **`Name`**, **`Channels`** (comma-separated → **Channels** links), **`Channel versions`** (single line of text, e.g. `C01-v003,C11-v001` — max version per channel from VideoFile scan), **`Used in show`** (`TRUE`). |
| `exports/cues.csv` | **`CUE_NUMBER`**, **`Track`**, **`CUE_NAME`**, **`Filesets`** only (no per-cue **Media** column). |

**File naming:** Assets like `000-021-c11-grids_and_guides.mov` parse as `NNN-NNN-<channel>-<description>`. The fileset key is `000-021-grids_and_guides`.

**Filesets not in the export** (unused in the show) are **not** updated or deleted by `push_airtable.py`. If you add manual content on those rows in Airtable (e.g. **Notes**), they are left alone on each push.

**Canonical media / disk versions:** Filenames are aligned with the VideoFile scanner (disk-style `_vNNN` on the stem) when computing **Channel versions**.

**Omitted from `cues.csv`:** cues with no tag / empty computed `CUE_NUMBER` (count is printed when you export).

**Fatal error:** if the same non-empty `CUE_NUMBER` appears twice (Airtable primary must be globally unique).

Optional legacy file (newline-separated list in one column):

```bash
python3 export_cues_csv.py --combined-out exports/cues_with_videos.csv
```

---

## 2. Create the Airtable base (once)

### Table **Media** *(optional)*

Only if you set **`AIRTABLE_SYNC_MEDIA=1`** and maintain a **`media.csv`**. The default workflow skips the Media table entirely.

### Table **Channels**

| Field | Type | Notes |
|-------|------|--------|
| **Name** | Primary field (Single line text) | Channel code, e.g. `C02`, `C21`. Must match `channels.csv` and **Filesets.Channels** link text. |

### Table **Filesets**

| Field | Type | Notes |
|-------|------|--------|
| **Name** | Primary field (Single line text) | Must match `filesets.csv` (e.g. `000-021-grids_and_guides`). |
| **Channels** | **Link to another record** → Channels | Allow **multiple**. Updated by `push_airtable.py`. |
| **Media** | **Link to another record** → Media *(optional)* | If present, the script **clears** this field (`[]`) on each push when **Media** is listed in `AIRTABLE_FILESETS_WRITABLE_FIELDS` — use **Channel versions** instead of per-file links. |
| **Channel versions** | Single line text | e.g. `C01-v003,C11-v001`. Filled from `filesets.csv`. Rename in Airtable? Set `AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD`. |
| **Used in show** | Checkbox | Exported as `TRUE` for rows in `filesets.csv`. |
| **Notes** | *(your manual field)* | Not written by the script. Unused filesets stay in the base with their notes intact because they are not in the CSV. |
| **Cues** | *(inverse link)* | Fills from **Cues → Filesets**. |

### Table **Cues**

| Field | Type | Notes |
|-------|------|--------|
| **CUE_NUMBER** | **Primary field** (Single line text) | Computed decimal from tag, e.g. `26.5`. Globally unique. |
| **Track** | Single line text | Updated by `push_airtable.py`. |
| **CUE_NAME** | Single line text | Updated by script. |
| **Filesets** | **Link to another record** → Filesets | Allow **multiple**. |
| **Media** | **Link to another record** → Media *(optional)* | Omit from the base if you don’t use it. To sync links, add **`Media`** to `AIRTABLE_CUE_WRITABLE_FIELDS` and enable **`AIRTABLE_SYNC_MEDIA`** + `media.csv`. |
| **Description** | Single line text | Manual only — **not** written by the script. |
| **Call** | Single line text | Manual only — **not** written by the script. |
| **Page number** | Single line text | Manual only — **not** written by the script. |
| **Notes** | Link to **Notes** table (or your notes table) | Manual only — orphan **cue** retention (see push behavior). |

Field names for script-managed columns must match **`Track`**, **`CUE_NAME`**, **`Filesets`** (and optionally **`Media`** for clearing links) or set env overrides.

---

## 3. First-time import (optional, no code)

If you prefer the UI over the API:

1. Import **`channels.csv`** into **Channels** (`Name` → primary).
2. Import **`filesets.csv`** into **Filesets** (`Name` → primary; **`Channels`** → **Channels**; **`Channel versions`** → Single line text; **`Used in show`** → Checkbox).
3. Import **`cues.csv`** into **Cues** (**`Filesets`** → **Filesets**).

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
| `AIRTABLE_SYNC_MEDIA` | *(unset / false)* — set to `1` / `true` / `yes` to sync **Media** + require `media.csv` |
| `AIRTABLE_MEDIA_*` | *(see script header)* — only when `AIRTABLE_SYNC_MEDIA` is on |
| `AIRTABLE_CHANNELS_TABLE` | `Channels` |
| `AIRTABLE_CHANNELS_NAME_FIELD` | `Name` |
| `AIRTABLE_FILESETS_TABLE` | `Filesets` |
| `AIRTABLE_FILESETS_NAME_FIELD` | `Name` |
| `AIRTABLE_FILESETS_CHANNELS_FIELD` | `Channels` |
| `AIRTABLE_FILESETS_MEDIA_FIELD` | `Media` (cleared to `[]` when listed writable) |
| `AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD` | `Channel versions` |
| `AIRTABLE_FILESETS_USED_FIELD` | `Used in show` |
| `AIRTABLE_FILESETS_WRITABLE_FIELDS` | `Channels,Used in show,Channel versions` ( **Media** omitted — add only if you want to clear links; empty `[]` can **422** ) |
| `AIRTABLE_CUES_TABLE` | `Cues` |
| `AIRTABLE_CUE_MEDIA_FIELD` | `Media` |
| `AIRTABLE_CUE_FILESETS_FIELD` | `Filesets` |
| `AIRTABLE_CUE_PRIMARY_FIELD` | `CUE_NUMBER` |
| `AIRTABLE_CUE_WRITABLE_FIELDS` | `Track,CUE_NAME,Filesets` — add **`,Media`** only if that linked field exists |
| `AIRTABLE_CUE_NOTES_FIELD` | `Notes` (orphan cue cleanup) |

Leave **`Media`** out of **`AIRTABLE_CUE_*`** / **`AIRTABLE_FILESETS_*`** writable lists if those linked fields do not exist in your base (avoids **UNKNOWN_FIELD_NAME** on GET/PATCH).

### Sync

```bash
python3 export_cues_csv.py
python3 push_airtable.py --dry-run   # optional: show counts only
python3 push_airtable.py
```

What **`push_airtable.py`** does:

1. **Media:** **Skipped** unless **`AIRTABLE_SYNC_MEDIA=1`** and `media.csv` exists (legacy / opt-in).
2. **Channels:** Ensures every channel **`Name`** from `channels.csv` exists.
3. **Filesets:** For each `filesets.csv` row (used-in-show only), **POST** or **PATCH** per `AIRTABLE_FILESETS_WRITABLE_FIELDS` — by default **Channels**, **Used in show**, and **Channel versions** text. **Filesets.Media** is not sent unless you add **Media** to the env list (optional `[]` to clear links; some bases return **422** for empty linked records).
4. **Cue orphans:** Same as before — name-matched **CUE_NUMBER** migration, else keep if **Notes** set or **delete**.
5. **Cues:** **POST**/**PATCH** `Track`, `CUE_NAME`, **`Filesets`** by default. **`Media`** is only read/written if it appears in **`AIRTABLE_CUE_WRITABLE_FIELDS`** (requires that field in the base).

**Channel / Fileset links** use **record IDs** from **Channels.Name** and **Filesets.Name**. **Cues → Filesets** drives the inverse link on **Filesets**.

**API usage:** **PATCH** only when writable fields differ. **POST** responses merge new record ids without re-listing entire tables.

**Notes:** Migration by name is only attempted when the CSV **`CUE_NAME`** is non-empty, so cues with blank names are never matched to an orphan for primary reassignment.

### Migrating older bases

Add **Filesets.Channel versions** (single line text), keep **Filesets.Channels** and **Cues.Filesets**. Remove or clear **Filesets.Media** / **Cues.Media** links via push (defaults), or drop **Media** from writable env lists to preserve old links. Re-export and push **channels → filesets → cues** (no `media.csv` by default).

### Rate limits

The script sleeps ~0.22s between requests. Large bases may take a few minutes on first run.

### Troubleshooting

- **HTTP 403/401**: Token scopes or base access.
- **Unknown field name**: Rename fields or set `AIRTABLE_MEDIA_*` / `AIRTABLE_CHANNELS_*` / `AIRTABLE_FILESETS_*` / `AIRTABLE_CUE_*` env vars.
- **Links missing**: **Channels.Name** / **Filesets.Name** must match CSV cells exactly. Check stderr warnings.
- **Field name errors**: Match **Channel versions** or set `AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD`.

---

## 5. Suggested day-to-day workflow

1. Update Disguise exports in `assets/` (`all_content_table.txt`, `*cue_table*.txt`) and refresh **VideoFile** on disk if you use versioning.
2. `python3 export_cues_csv.py` (use the VideoFile picker, or pass `--video-file-dir` to skip the dialog / pin a path).
3. `python3 push_airtable.py`

Commit CSVs to git if you want a history of exports; keep **`.env` out of git** (see `.gitignore`).
