"""Deterministic seed data — the single source of truth for the operational dataset.

Pure Python (no Spark, no Databricks). Imported by:
- `01_generate_genie_tables.py` — writes these rows to Delta (the 5 Genie tables + the
  materialized `inventory_current` / `open_pos` helper tables).
- `02_pre_seed_pgvector.py`     — loads `quality_incidents` (with embeddings) into Lakebase.
- `04_verify_hybrid_query.py`   — asserts the hero scenario reproduces.
- `eval_set.py`                 — derives the certified Genie Q→expected literals.

Everything is deterministic: a fixed RNG seed drives the filler, and the demo "hero" rows are
hand-set, so the demo and the evals never drift. Re-running the generators reproduces identical
data. See `README.md` and the architecture docs for why this dataset is shaped the way it is.

Narrative: **Apex Industrial** buys raw materials from upstream suppliers (the real names from
the `strategic_revenue_demo` knowledge corpus, so the Knowledge / Genie / Operational agents
describe the same world), produces/kits finished goods across 5 categories, and sells to OEMs.
"""

from __future__ import annotations

import random
from datetime import date, datetime

# ── Determinism + conventions ──────────────────────────────────────────────────────────────
RNG_SEED = 42
TODAY = date(2026, 6, 5)  # the demo's "today"; matches CLAUDE.md currentDate

CATEGORIES = ["adhesives", "fasteners", "abrasives", "safety", "tools"]
LOCATIONS = ["DC-EAST", "DC-WEST", "DC-CENTRAL"]
PO_STATUSES = ["open", "in_transit", "delivered", "cancelled"]

# ── Hero scenario anchors ────────────────────────────────────────────────────────────────────
HERO_SUPPLIER_ID = "SUP-001"  # Henkel AG — the recurring at-risk adhesive supplier
ALT_SUPPLIER_ID = "SUP-002"   # DuPont — the healthy re-source option
HERO_SKU = "SKU-1001"         # Structural Epoxy Adhesive (already in the Genie example SQLs)
HERO_CATEGORY = "adhesives"
HERO_ON_HAND = 40.0           # deliberately low → coverage gap that forces a decision
HERO_HENKEL_OPEN_PO_QTY = 500.0  # same risky source
HERO_DUPONT_OPEN_PO_QTY = 300.0  # alternate source

# ── Suppliers (real raw-material suppliers from the corpus) ──────────────────────────────────
# (supplier_id, name, country, categories). ≥2 suppliers per category so dual-sourcing is answerable.
SUPPLIERS = [
    {"supplier_id": "SUP-001", "name": "Henkel AG", "country": "Germany", "categories": "adhesives"},
    {"supplier_id": "SUP-002", "name": "DuPont de Nemours", "country": "United States", "categories": "adhesives"},
    {"supplier_id": "SUP-003", "name": "BASF Corporation", "country": "Germany", "categories": "adhesives"},
    {"supplier_id": "SUP-004", "name": "Dow Chemical", "country": "United States", "categories": "adhesives"},
    {"supplier_id": "SUP-005", "name": "Nucor Steel Corporation", "country": "United States", "categories": "fasteners"},
    {"supplier_id": "SUP-006", "name": "Allegheny Technologies", "country": "United States", "categories": "fasteners"},
    {"supplier_id": "SUP-007", "name": "Alcoa Corporation", "country": "United States", "categories": "fasteners"},
    {"supplier_id": "SUP-008", "name": "Saint-Gobain Abrasives", "country": "France", "categories": "abrasives"},
    {"supplier_id": "SUP-009", "name": "Washington Mills", "country": "United States", "categories": "abrasives"},
    {"supplier_id": "SUP-010", "name": "3M Industrial", "country": "United States", "categories": "safety,adhesives"},
    {"supplier_id": "SUP-011", "name": "Honeywell Safety", "country": "United States", "categories": "safety,tools"},
    {"supplier_id": "SUP-012", "name": "Snap-on Tools", "country": "United States", "categories": "tools"},
]


def suppliers_by_category(category: str) -> list[str]:
    """Supplier IDs that carry a category (for FK-consistent PO/incident generation)."""
    return [s["supplier_id"] for s in SUPPLIERS if category in s["categories"].split(",")]


# ── Products (~40 SKUs) ───────────────────────────────────────────────────────────────────────
_PRODUCT_NAMES = {
    "adhesives": ["Structural Epoxy Adhesive", "Cyanoacrylate Bonder", "Polyurethane Sealant",
                  "Threadlocker Compound", "Contact Cement", "Anaerobic Retainer"],
    "fasteners": ["Hex Cap Bolt M10", "Socket Head Screw", "Stainless Lock Nut", "Carriage Bolt",
                  "Self-Tapping Screw", "Structural Rivet"],
    "abrasives": ["Silicon Carbide Wheel", "Aluminum Oxide Disc", "Flap Sanding Wheel",
                  "Cut-Off Wheel 4in", "Diamond Grinding Cup"],
    "safety": ["Cut-Resistant Glove", "Safety Goggles", "Respirator Cartridge", "Hi-Vis Vest"],
    "tools": ["Torque Wrench 1/2in", "Impact Driver Bit Set", "Digital Caliper", "Pry Bar 18in"],
}


def build_products() -> list[dict]:
    """40 SKUs SKU-1001..SKU-1040. SKU-1001 is the hand-set hero adhesive SKU."""
    rng = random.Random(RNG_SEED)
    products: list[dict] = [
        {"sku": HERO_SKU, "name": "Structural Epoxy Adhesive, 50ml",
         "category": HERO_CATEGORY, "list_price": 18.50},
    ]
    n = 40
    for i in range(1, n):
        sku = f"SKU-{1001 + i}"
        category = CATEGORIES[i % len(CATEGORIES)]
        base = rng.choice(_PRODUCT_NAMES[category])
        name = f"{base} #{i:02d}"
        list_price = round(rng.uniform(2.0, 250.0), 2)
        products.append({"sku": sku, "name": name, "category": category, "list_price": list_price})
    return products


def _category_of(sku: str, products: list[dict]) -> str:
    return next(p["category"] for p in products if p["sku"] == sku)


# ── Inventory (~100 rows, with last_updated history so MAX(last_updated) matters) ─────────────
def build_inventory() -> list[dict]:
    rng = random.Random(RNG_SEED + 1)
    products = build_products()
    rows: list[dict] = []

    # Hero: SKU-1001 deliberately low. Two history rows at DC-EAST (older higher → latest 40)
    # proves the MAX(last_updated) snapshot logic; DC-WEST is 0.
    rows.append({"sku": HERO_SKU, "location": "DC-EAST", "on_hand_qty": 220.0,
                 "last_updated": datetime(2026, 4, 1, 8, 0, 0)})
    rows.append({"sku": HERO_SKU, "location": "DC-EAST", "on_hand_qty": HERO_ON_HAND,
                 "last_updated": datetime(2026, 6, 2, 8, 0, 0)})
    rows.append({"sku": HERO_SKU, "location": "DC-WEST", "on_hand_qty": 0.0,
                 "last_updated": datetime(2026, 6, 2, 8, 0, 0)})

    for p in products:
        if p["sku"] == HERO_SKU:
            continue
        for location in rng.sample(LOCATIONS, k=rng.randint(2, 3)):
            qty = round(rng.uniform(100.0, 5000.0), 0)
            rows.append({"sku": p["sku"], "location": location, "on_hand_qty": qty,
                         "last_updated": datetime(2026, 6, rng.randint(1, 4), 8, 0, 0)})
    return rows


def build_inventory_current() -> list[dict]:
    """Latest row per (sku, location) by last_updated, summed to sku. Mirrors the gold view the
    operational join uses (`inventory_current(sku, on_hand_qty)`)."""
    latest: dict[tuple[str, str], dict] = {}
    for r in build_inventory():
        key = (r["sku"], r["location"])
        if key not in latest or r["last_updated"] > latest[key]["last_updated"]:
            latest[key] = r
    summed: dict[str, float] = {}
    for (sku, _loc), r in latest.items():
        summed[sku] = summed.get(sku, 0.0) + r["on_hand_qty"]
    return [{"sku": sku, "on_hand_qty": qty} for sku, qty in sorted(summed.items())]


# ── Purchase orders (~60) ─────────────────────────────────────────────────────────────────────
def build_purchase_orders() -> list[dict]:
    rng = random.Random(RNG_SEED + 2)
    products = build_products()
    rows: list[dict] = [
        # Hero: replacement from the same risky supplier (Henkel) ...
        {"po_id": "PO-2026-0042", "supplier_id": HERO_SUPPLIER_ID, "sku": HERO_SKU,
         "qty": HERO_HENKEL_OPEN_PO_QTY, "expected_date": date(2026, 7, 20), "status": "open"},
        # ... and the alternate-source option (DuPont) the planner can re-source to.
        {"po_id": "PO-2026-0043", "supplier_id": ALT_SUPPLIER_ID, "sku": HERO_SKU,
         "qty": HERO_DUPONT_OPEN_PO_QTY, "expected_date": date(2026, 6, 25), "status": "open"},
        # A delivered Henkel history row for SKU-1001 (so the recurrence has context).
        {"po_id": "PO-2026-0011", "supplier_id": HERO_SUPPLIER_ID, "sku": HERO_SKU,
         "qty": 400.0, "expected_date": date(2026, 3, 10), "status": "delivered"},
    ]

    next_id = 100
    # Filler POs, FK-consistent (supplier must carry the SKU's category). Spread dates Jun–Dec
    # 2026 with a healthy share in Q4 so the Genie "Q4 open PO by supplier" question returns rows.
    # Exclude the hero SKU from filler so SKU-1001 has EXACTLY the two hand-set open POs
    # (Henkel 500 + DuPont 300 = 800) — keeps the demo math clean and explainable.
    filler_products = [p for p in products if p["sku"] != HERO_SKU]
    for _ in range(57):
        p = rng.choice(filler_products)
        candidates = suppliers_by_category(p["category"])
        if not candidates:
            continue
        supplier_id = rng.choice(candidates)
        status = rng.choices(PO_STATUSES, weights=[40, 25, 25, 10])[0]
        month = rng.randint(6, 12)
        day = rng.randint(1, 28)
        rows.append({
            "po_id": f"PO-2026-{next_id:04d}",
            "supplier_id": supplier_id,
            "sku": p["sku"],
            "qty": round(rng.uniform(50.0, 2000.0), 0),
            "expected_date": date(2026, month, day),
            "status": status,
        })
        next_id += 1

    # Guarantee every ACTIVE incident's (supplier, sku) has an open PO. The operational hybrid
    # query INNER-JOINs open_pos on (supplier_id, sku), so without this the fastener/abrasive
    # incident clusters would be unreachable (no open PO → filtered out) and a non-hero planner
    # would see nothing for their own scope. Deterministic (sorted, fixed qty/date).
    open_pairs = {(r["supplier_id"], r["sku"]) for r in rows if r["status"] == "open"}
    incident_pairs = {(i["supplier_id"], i["sku"]) for i in build_quality_incidents() if not i["expired"]}
    gid = 900
    for supplier_id, sku in sorted(incident_pairs - open_pairs):
        rows.append({"po_id": f"PO-2026-{gid:04d}", "supplier_id": supplier_id, "sku": sku,
                     "qty": 250.0, "expected_date": date(2026, 8, 15), "status": "open"})
        gid += 1
    return rows


def build_open_pos() -> list[dict]:
    """status='open' aggregated per (supplier_id, sku): SUM(qty), MIN(expected_date). Mirrors the
    gold table the operational join uses (`open_pos(supplier_id, sku, open_po_qty, ...)`)."""
    agg: dict[tuple[str, str], dict] = {}
    for r in build_purchase_orders():
        if r["status"] != "open":
            continue
        key = (r["supplier_id"], r["sku"])
        cur = agg.setdefault(key, {"supplier_id": r["supplier_id"], "sku": r["sku"],
                                   "open_po_qty": 0.0, "next_expected_date": r["expected_date"]})
        cur["open_po_qty"] += r["qty"]
        cur["next_expected_date"] = min(cur["next_expected_date"], r["expected_date"])
    return [agg[k] for k in sorted(agg)]


# ── Supplier status (~24; 2 ratings/supplier so MAX(last_updated) matters) ────────────────────
def build_supplier_status() -> list[dict]:
    rng = random.Random(RNG_SEED + 3)
    rows: list[dict] = [
        # Hero: Henkel deteriorating watch → at_risk (latest wins).
        {"supplier_id": HERO_SUPPLIER_ID, "status": "watch", "risk_score": 58.0,
         "last_updated": datetime(2026, 3, 1, 0, 0, 0)},
        {"supplier_id": HERO_SUPPLIER_ID, "status": "at_risk", "risk_score": 82.0,
         "last_updated": datetime(2026, 5, 28, 0, 0, 0)},
    ]
    score_for = {"healthy": (5, 30), "watch": (30, 60), "at_risk": (60, 95)}
    for s in SUPPLIERS:
        if s["supplier_id"] == HERO_SUPPLIER_ID:
            continue
        # One other supplier kept at_risk for Genie ranking variety; rest healthy/watch.
        latest_status = "at_risk" if s["supplier_id"] == "SUP-008" else rng.choice(["healthy", "healthy", "watch"])
        older_status = "watch" if latest_status == "at_risk" else "healthy"
        lo, hi = score_for[older_status]
        rows.append({"supplier_id": s["supplier_id"], "status": older_status,
                     "risk_score": float(rng.randint(lo, hi)), "last_updated": datetime(2026, 3, 1, 0, 0, 0)})
        lo, hi = score_for[latest_status]
        rows.append({"supplier_id": s["supplier_id"], "status": latest_status,
                     "risk_score": float(rng.randint(lo, hi)), "last_updated": datetime(2026, 5, 28, 0, 0, 0)})
    return rows


# ── Quality incidents (the pgvector semantic table) ──────────────────────────────────────────
# Semantic CLUSTERS: each cluster is the SAME underlying defect described with VARIED vocabulary,
# so cosine similarity must group on *meaning* (not shared tokens). Distractors are singletons.
# Cluster A is the hero: Henkel (SUP-001) + SKU-1001, so it joins to inventory_current (on sku)
# and open_pos (on supplier+sku). Clusters B/C are other categories — the adhesive hero query
# ranks them below cluster A by vector similarity, so cluster A dominates the top-5.

_CLUSTER_A_ADHESIVE = [  # Henkel / SKU-1001 — adhesive brittleness/cracking (the hero cluster)
    ("Cured epoxy bead cracked under vibration on the assembly line.", "high", False),
    ("Adhesive joint failed the pull test at roughly 40% of rated load.", "critical", False),
    ("Bondline turned brittle and flaked after thermal cycling.", "high", False),
    ("Field report: bonded bracket delaminated within weeks of install.", "critical", False),
    ("Samples snapped at the seam under torque; poor adhesion observed.", "high", False),
    ("Shore-D hardness out of spec; cured film fractured when flexed.", "medium", False),
    ("SUPERSEDED duplicate of the Q1 cracking report — closed.", "low", True),  # expired_at set
]
_CLUSTER_B_FASTENER = [  # Nucor / fasteners — thread / torque failures
    ("Bolt threads stripped below the rated torque during fit-up.", "high"),
    ("Fasteners sheared at the head under nominal preload.", "critical"),
    ("Galling on stainless lock nuts during installation.", "medium"),
    ("Suspected hydrogen embrittlement; cracked after plating.", "high"),
    ("Torque-to-failure measured well under the spec minimum.", "high"),
]
_CLUSTER_C_ABRASIVE = [  # Saint-Gobain / abrasives — wheel wear / contamination
    ("Grinding wheel shed grit prematurely, contaminating the part.", "medium"),
    ("Cut-off wheel glazed over and stopped cutting after minutes.", "low"),
    ("Wheel ran out-of-round, leaving dimensional defects.", "medium"),
    ("Silicon-carbide disc wore at twice the expected rate.", "low"),
]
_DISTRACTORS = [
    ("Pallet arrived with crushed packaging; outer cartons damaged.", "low"),
    ("Certificate of analysis missing from the shipment paperwork.", "low"),
    ("Lot was mislabeled with the wrong batch number.", "medium"),
    ("Moisture ingress found in a sealed container on receipt.", "medium"),
    ("Barcode label unreadable at the receiving scanner.", "low"),
    ("Short shipment: received 90 of 100 ordered units.", "low"),
    ("Color of the safety vests differed from the approved sample.", "low"),
    ("Calibration certificate for the torque wrench had expired.", "medium"),
]


def build_quality_incidents() -> list[dict]:
    """Returns rows WITHOUT embeddings. `02_pre_seed_pgvector.py` computes the embedding of
    `description` via the endpoint and inserts the vector. `summary` flows to OperationalRow.summary;
    `category` groups the semantic clusters (never surfaced as a row field)."""
    products = build_products()
    # Pick a representative fastener and abrasive SKU for the distractor clusters.
    fastener_sku = next(p["sku"] for p in products if p["category"] == "fasteners")
    abrasive_sku = next(p["sku"] for p in products if p["category"] == "abrasives")

    rows: list[dict] = []
    n = 0

    def add(supplier_id: str, sku: str, category: str, text: str, severity: str, expired: bool):
        nonlocal n
        n += 1
        rows.append({
            "incident_id": f"QI-2026-{n:04d}",
            "supplier_id": supplier_id,
            "sku": sku,
            "category": category,
            "summary": text if len(text) <= 80 else text[:77] + "...",
            "description": text,
            "severity": severity,
            "status": "closed" if expired else "investigating",
            "incident_date": date(2026, 5, 20),
            "expired": expired,  # → expired_at timestamp on insert when True
        })

    for text, severity, expired in _CLUSTER_A_ADHESIVE:
        add(HERO_SUPPLIER_ID, HERO_SKU, "adhesives", text, severity, expired)
    for text, severity in _CLUSTER_B_FASTENER:
        add("SUP-005", fastener_sku, "fasteners", text, severity, False)
    for text, severity in _CLUSTER_C_ABRASIVE:
        add("SUP-008", abrasive_sku, "abrasives", text, severity, False)
    for i, (text, severity) in enumerate(_DISTRACTORS):
        # Spread distractors across suppliers/categories deterministically — but NEVER onto the
        # hero SKU, so SKU-1001's incidents stay pure cluster-A and its PO math stays clean (800).
        s = SUPPLIERS[i % len(SUPPLIERS)]
        cat = s["categories"].split(",")[0]
        cat_skus = [p["sku"] for p in products if p["category"] == cat and p["sku"] != HERO_SKU]
        sku = cat_skus[i % len(cat_skus)] if cat_skus else HERO_SKU
        add(s["supplier_id"], sku, cat, text, severity, False)
    return rows


# A natural-language query that should rank Cluster A in the top-5 (used by 04_verify).
HERO_QUERY_TEXT = "brittle structural adhesive that cracks under load and fails the pull test"
