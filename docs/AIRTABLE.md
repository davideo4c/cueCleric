# Airtable

## Export

```bash
python3 export_cues_csv.py --input-dir /path/to/folder   # all_content + *cue_table*.txt
```

- **`--video-file-dir`** — scan disk for `_vNNN` → **Channel versions** in `filesets.csv`.
- **`--pick-video-file-dir`** — choose that folder in a dialog (Tk). Default is no scan.
- **`--combined-out path.csv`** — legacy cue CSV with a **VIDEOS** column.

Writes **`exports/channels.csv`**, **`filesets.csv`**, **`cues.csv`**. No `media.csv`.

| CSV | Primary / notes |
|-----|-----------------|
| channels | **Name** = channel code (`C01`, …) |
| filesets | **Name**, **Channels**, **Channel versions**, **Used in show** |
| cues | **CUE_NUMBER**, **Track**, **CUE_NAME**, **Filesets**, **Video layers used** (long text, newline-separated media filenames) |

Cues without a usable tag / **CUE_NUMBER** are skipped. Duplicate non-empty **CUE_NUMBER** aborts the export.

Media naming: `NNN-NNN-cXX-description.ext` → fileset key `NNN-NNN-description`, channel `CXX`.

## Base (once)

**Channels:** **Name** (primary).

**Filesets:** **Name** (primary), **Channels** → Channels, **Channel versions** (text), **Used in show** (checkbox). **Media** link is optional; default push does not maintain it.

**Cues:** **CUE_NUMBER** (primary), **Track**, **CUE_NAME**, **Filesets** → Filesets, **Video layers used** (long text). Add **Media** → Media only if you use sync (see env). Rename **Video layers used** in Airtable? Set `AIRTABLE_CUE_VIDEO_LAYERS_FIELD` and list that name in `AIRTABLE_CUE_WRITABLE_FIELDS`. Other fields are yours.

Field names must match the CSV columns above, or override with `AIRTABLE_*_FIELD` env vars.

## Credentials

1. Token: [airtable.com/create/tokens](https://airtable.com/create/tokens) with read/write on the base.
2. Base ID: **Help → API documentation** (`app…`).

```bash
# .env (loaded automatically) or export in shell:
AIRTABLE_TOKEN=pat...
AIRTABLE_BASE_ID=app...
```

## Env reference

| Variable | Role |
|----------|------|
| `AIRTABLE_SYNC_MEDIA` | Set `1` to sync **Media** (requires `media.csv`). |
| `AIRTABLE_MEDIA_*` | Table/field names when media sync is on. |
| `AIRTABLE_CHANNELS_TABLE` | Default `Channels` |
| `AIRTABLE_FILESETS_TABLE` | Default `Filesets` |
| `AIRTABLE_FILESETS_WRITABLE_FIELDS` | Default `Channels,Used in show,Channel versions` |
| `AIRTABLE_FILESETS_CHANNEL_VERSIONS_FIELD` | Default `Channel versions` |
| `AIRTABLE_CUES_TABLE` | Default `Cues` |
| `AIRTABLE_CUE_PRIMARY_FIELD` | Default `CUE_NUMBER` |
| `AIRTABLE_CUE_VIDEO_LAYERS_FIELD` | Default `Video layers used` (must match a long text field on Cues) |
| `AIRTABLE_CUE_WRITABLE_FIELDS` | Default `Track,CUE_NAME,Filesets` + video layers field |
| `AIRTABLE_CUE_NOTES_FIELD` | Default `Notes` (orphan handling) |

Rename a column in Airtable → set the matching `AIRTABLE_*_FIELD`. Omit **Media** from writable lists if that field does not exist.

## Push

```bash
python3 push_airtable.py --dry-run
python3 push_airtable.py
```

Ensures channels and filesets rows exist/updates writable fields, then upserts cues by **CUE_NUMBER**. **Media** runs only with `AIRTABLE_SYNC_MEDIA=1` and `media.csv`.

**Troubleshooting:** 401 → token/base access. **UNKNOWN_FIELD_NAME** → field names vs env. Missing links → CSV **Name** / **Filesets** cells must match Airtable primaries exactly (see stderr warnings).

## Workflow

1. Refresh Disguise text exports (and run VideoFile scan if you use `--video-file-dir`).
2. `python3 export_cues_csv.py …`
3. `python3 push_airtable.py`
