"""Upload seed PDFs into the UC volume so the Knowledge agent can index them.

Idempotent — overwrites existing files in the volume so re-runs converge. Mirrors the
folder structure of `seed_data/bronze_documents/` (contracts/, supplier_notifications/,
competitor_catalogs/, promotion_briefs/, market_events/) plus the `document_metadata.json`
sidecar.

Runs both as a local script (uv run python ...) and as a Databricks notebook — no code
changes needed; auth is detected per `agent_server/config.py`.

Outputs (idempotent):
    /Volumes/{UC_CATALOG}/{UC_SCHEMA}/{UC_VOLUME}/<doc_type>/<filename>.pdf
    /Volumes/{UC_CATALOG}/{UC_SCHEMA}/{UC_VOLUME}/document_metadata.json
"""

from __future__ import annotations

import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient

from agent_server.config import settings


# Subfolders the upload preserves (matches DocType enum in contracts.py).
DOC_SUBFOLDERS = (
    "contracts",
    "supplier_notifications",
    "competitor_catalogs",
    "promotion_briefs",
    "market_events",
)


def _iter_pdfs(root: Path) -> list[tuple[Path, str]]:
    """Yield (local_path, volume_subpath) pairs for every PDF under root."""
    pairs: list[tuple[Path, str]] = []
    for sub in DOC_SUBFOLDERS:
        folder = root / sub
        if not folder.exists():
            print(f"  skip: {sub}/ not present under {root}")
            continue
        for pdf in folder.rglob("*.pdf"):
            rel = pdf.relative_to(root)  # e.g. contracts/CTR-...pdf or market_events/real/...pdf
            pairs.append((pdf, str(rel)))
    return pairs


def _ensure_volume(w: WorkspaceClient) -> None:
    """Create the catalog/schema/volume if missing. Cheap, idempotent."""
    catalog = settings.uc_catalog
    schema = settings.uc_schema
    volume = settings.uc_volume

    # Catalog / schema via SQL (the simplest cross-environment path).
    if not settings.warehouse_id:
        print(
            "  WARN: DATABRICKS_WAREHOUSE_ID not set — skipping catalog/schema creation; "
            "assuming they already exist. Set it in .env to auto-provision.",
            file=sys.stderr,
        )
    else:
        ws = w.statement_execution
        for stmt in (
            f"CREATE CATALOG IF NOT EXISTS `{catalog}`",
            f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
            f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.`{volume}`",
        ):
            print(f"  exec: {stmt}")
            ws.execute_statement(warehouse_id=settings.warehouse_id, statement=stmt, wait_timeout="30s")


def _upload(w: WorkspaceClient, local: Path, volume_subpath: str) -> None:
    dest = f"{settings.volume_uri}/{volume_subpath}"
    with local.open("rb") as fh:
        w.files.upload(file_path=dest, contents=fh, overwrite=True)


def main() -> None:
    src = settings.seed_data_dir.resolve()  # relative paths resolve against the repo root
    if not src.exists():
        sys.exit(
            f"SEED_DATA_PATH does not exist: {src}\n"
            "The seed PDFs are vendored at data/knowledge/bronze_documents/ — run this from the "
            "repo root, or set SEED_DATA_PATH in .env to point at the documents folder."
        )

    print(f"Source : {src}")
    print(f"Target : {settings.volume_uri}")
    print(f"Profile: {settings.databricks_profile or '(ambient — running on Databricks)'}")

    w = WorkspaceClient()
    _ensure_volume(w)

    pairs = _iter_pdfs(src)
    if not pairs:
        sys.exit(f"No PDFs found under {src} in {DOC_SUBFOLDERS!r}")

    print(f"\nUploading {len(pairs)} PDFs...")
    for local, sub in pairs:
        print(f"  → {sub}")
        _upload(w, local, sub)

    # Metadata sidecar — the document_metadata.json with Q&A pairs / customer / supplier.
    meta = src / "document_metadata.json"
    if meta.exists():
        print("  → document_metadata.json")
        _upload(w, meta, "document_metadata.json")

    print(f"\nDone. {len(pairs)} PDFs at {settings.volume_uri}")


if __name__ == "__main__":
    main()
