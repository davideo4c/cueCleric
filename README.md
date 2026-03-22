# Cue Cleric

Export Disguise **cue tables** and **all content** into CSVs for **Airtable** (Filesets + Cues with linked filesets), with an optional API sync.

## Quick start

```bash
python3 export_cues_csv.py          # writes exports/media.csv, channels.csv, filesets.csv, cues.csv
python3 push_airtable.py --dry-run  # preview Airtable sync
python3 push_airtable.py            # requires .env (see docs/AIRTABLE.md)
```

Full Airtable field layout and env vars: **[docs/AIRTABLE.md](docs/AIRTABLE.md)**.

## Requirements

- Python 3.9+ (stdlib only; no `pip install` required)

## GitHub setup

1. Create a new repository on [github.com/new](https://github.com/new) (empty, no README if you already have one here).

2. In this folder:

```bash
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Use SSH if you prefer: `git@github.com:YOUR_USER/YOUR_REPO.git`

3. **Never commit `.env`** — it is listed in `.gitignore` (Airtable token). Use `.env.example` as a template.

## Repo layout

| Path | Purpose |
|------|---------|
| `assets/` | Disguise exports (`all_content_table.txt`, `*cue_table*.txt`) |
| `export_cues_csv.py` | Build `exports/media.csv`, `channels.csv`, `filesets.csv`, `cues.csv` |
| `push_airtable.py` | Sync CSVs to Airtable via API |
| `exports/` | Generated CSVs (optional to track in git) |
| `docs/AIRTABLE.md` | Base schema + workflow |
