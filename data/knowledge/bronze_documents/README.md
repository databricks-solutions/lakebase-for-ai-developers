# Bronze documents — UC Volume seed files

These are the **business documents** that live in the Bronze `documents` UC
Volume and feed the **Knowledge Assistant** (the Docs page assistant + a source
for the MAS supervisor). They are committed here so a **new workspace can be
stood up without running the heavy `bronze_pdf_documents` notebook** — just
upload this folder into the volume and point the Knowledge Assistant at it.

> These are pre-generated, deterministic (`seed=42`) copies of exactly what
> `notebooks/06_bronze_pdf_documents.py` and
> `data_generation/generate_market_event_pdfs.py` would produce. They are plain,
> text-extractable PDFs (the Knowledge Assistant only needs the text). If you'd
> rather regenerate them in-workspace, run the `unified_refresh` job's bronze
> branch instead — see `docs/PHASE1_DEPLOY.md`.

## What's here (67 PDFs)

| Folder | Count | Contents |
| --- | --- | --- |
| `contracts/` | 15 | Master supply agreements — volume pricing tiers, trade terms, payment terms |
| `competitor_catalogs/` | 8 | Competitor (TitanGrip, PrecisionBond, SafeEdge, AbrasiveTech) 2026 price catalogs |
| `promotion_briefs/` | 12 | Promotion proposals — budget, expected lift, ROI, competitive context |
| `supplier_notifications/` | 10 | Supplier raw-material price-change notices (steel, resin, abrasives, …) |
| `market_events/real/` | 14 | News-article PDFs for real macro events (tariffs, hurricanes, Fed moves, CHIPS/IRA) |
| `market_events/fictional/` | 8 | Press releases / recall notices for fictional Apex & competitor events |

`document_metadata.json` and `market_events/event_metadata.json` carry the
Q&A/guideline pairs used for RAG evaluation when wiring up the Knowledge
Assistant.

## Target volume

```
/Volumes/<catalog>/<bronze_schema>/documents/
```

With the bundle defaults that resolves to:

```
/Volumes/manufacturing/strategic_revenue_bronze/documents/
```

## Upload to the volume (one command)

The volume is created by the bronze pipeline / `CREATE VOLUME`; if it doesn't
exist yet, create it first:

```sql
CREATE VOLUME IF NOT EXISTS manufacturing.strategic_revenue_bronze.documents;
```

Then upload everything, preserving the folder structure:

```bash
./upload.sh                       # uses bundle defaults + $DATABRICKS_CONFIG_PROFILE
# or override:
CATALOG=manufacturing BRONZE_SCHEMA=strategic_revenue_bronze \
  PROFILE=my-fevm ./upload.sh
```

`upload.sh` is a thin wrapper around `databricks fs cp --recursive`. You can also
drag-and-drop the folders in the Catalog Explorer UI (Volume → Upload).

After upload, create the Knowledge Assistant over
`/Volumes/manufacturing/strategic_revenue_bronze/documents` — see the deployment
guide.
