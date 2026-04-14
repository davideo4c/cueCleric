# Cue Cleric

Disguise exports → CSVs → optional Airtable sync.

## Quick start

```bash
python3 export_cues_csv.py --input-dir /path/to/disguise-export
# Optional: --video-file-dir /path/to/VideoFile   (channel version text from disk)
# Or: --pick-video-file-dir   (Tk picker; needs python-tk)

python3 push_airtable.py --dry-run
python3 push_airtable.py   # needs .env (see docs/AIRTABLE.md)
```

Without `--input-dir`, export opens a folder picker (needs Tk).

**Requirements:** Python 3.9+. Stdlib only unless you use a venv for other tools.

**Secrets:** Do not commit `.env`. Copy from `.env.example`.

## Layout

| Path | Role |
|------|------|
| `export_cues_csv.py` | Writes `exports/*.csv` |
| `push_airtable.py` | Pushes CSVs to Airtable |
| `docs/AIRTABLE.md` | Base shape, env vars, sync behavior |

Optional: track `exports/` in git; `assets/` is gitignored by default for sample exports.
