# CB3 Equity Report — Collaborator Guide

## Overview

Each collaborator is responsible for one domain of the CB3 equity report. Your job is to:

1. Clean your domain's raw data into a tract-level CSV (plus a point-level CSV for any metric that has native, finer-grained coordinates — see [Output schema](#output-schema))
2. Register your metrics in the shared metadata file
3. Build interactive Folium maps from the clean data, using selectable demographic choropleths as backdrop and your metrics as bubble/point layers on top

All shared code lives in `src/`. You write two files: `scripts/<your_domain>/build.py` and `scripts/<your_domain>/maps.qmd`.

---

## Repository structure

```
CB3_working/
├── data/
│   ├── raw/
│   │   ├── Geography/          # shared — tract polygons and crosswalk
│   │   └── <Your Domain>/      # your raw source files go here
│   └── clean/                  # output CSVs written by build scripts
├── docs/
│   ├── metric_metadata.yml     # shared metadata for all metrics
│   ├── CB3_Data_Reference_by_Domain_v4.csv
│   └── proposed_concept_for_CB3_equity_report_v2.pdf
├── maps/                       # rendered HTML maps (git-ignored)
├── scripts/
│   └── <your_domain>/
│       ├── __init__.py         # empty — required for python -m
│       ├── build.py            # cleans raw data → clean CSV(s)
│       └── maps.qmd            # renders Folium maps from clean CSV(s)
└── src/
    ├── cb3_utils.py            # shared data utilities
    └── map_utils.py            # shared Folium helpers
```

---

## Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/gh-islg/CB3_working.git
cd CB3_working
pip install -r requirements.txt
```

---

## Output schema

**Not every metric is tract-level.** Some data is only meaningful at its native, finer-grained geography — an individual building or site — and collapsing it to a tract centroid would throw away real information (e.g. *which building* has subsidized units expiring, not just which tract they're in). Each domain therefore writes:

1. **One tract-level CSV** (always) — the wide table every domain has always produced.
2. **Zero or more point-level CSVs** — one per metric (or related group of metrics) that has native lat/lon coordinates, instead of forcing it into the tract table as a centroid-only value.

### Tract-level CSV

One row per census tract, one column per metric.

```
GEOID                      — 11-character 2020 Census tract identifier (string, zero-padded)
tract_label                — human-readable tract number (e.g. "2", "14.02")
tract_name                 — full name (e.g. "Census Tract 2, Manhattan County, New York")
nta_code                   — 2020 NTA code
nta_name                   — 2020 NTA name
cdta_code                  — 2020 CDTA code (all rows: MN03)
cdta_name                  — 2020 CDTA name
<metric_1>                 — numeric tract-level value
<metric_2>                 — ...
...
tract_centroid_latitude    — polygon centroid, for placing tract-level metrics as map points (see Step 3)
tract_centroid_longitude   — polygon centroid
<flag columns>              — text notes on geography, data availability, methodology
```

The output always has **31 rows** — one per official CB3 census tract — and is written to `data/clean/<your_domain>_tract.csv`.

### Point-level CSV (optional, one per building/site-level metric or related group)

One row per site (building, address, etc.), not per tract.

```
<id column>      — e.g. bbl, a unique site identifier
standard_address — human-readable address, for tooltips
GEOID             — tract the point falls in, for joining to demographic context
latitude          — native site coordinate (not a tract centroid)
longitude         — native site coordinate
<metric_1>        — numeric value at this site
<metric_2>        — ... (related metrics, e.g. two time windows, can share one file)
```

Written to `data/clean/<your_domain>_<metric_group>_points.csv`. See `housing_and_affordability.py` for two examples: `housing_and_affordability_subsidized_points.csv` and `housing_and_affordability_new_construction_points.csv`.

---

## Step 1 — Add raw data

Drop your source files into `data/raw/<Your Domain>/`. The Geography files should be placed in `data/raw/Geography/` and are shared across all domains. Since we are not hosting any data on GitHub, please utilize the folders in the project's Sharepoint.

---

## Step 2 — Register your metrics

Add an entry to [`docs/metric_metadata.yml`](metric_metadata.yml) for each metric you plan to map. This is the **only place** labels, units, and (for choropleths) palettes are defined — do not hardcode them in your scripts or notebooks.

```yaml
unemployment_rate:
  domain: economic_security
  short_label: Unemployment rate       # shown in the map layer control
  label: Civilian unemployment rate    # shown in tooltips and popups
  description: >
    Share of the civilian labor force aged 16+ that is unemployed.
  unit: "%"
  source: ACS 5-year estimates, table B23025
  year: 2023
  geography: Census tract
  higher_is: worse
  map_classification: quintile
  display_format: percent
  filename: unemployment_rate.html
```

Required keys: `domain`, `short_label`, `label`, `unit`. `filename` is required for your own domain's metrics (they each render their own map) but not for `domain: demographics` entries (those are backdrop layers shared across every domain's maps, not standalone files).

**`palette` (plus `positive_values_only`, `zero_color`, `zero_label`) is only required for `domain: demographics` entries** — those are still rendered as choropleths (`add_metric_layer()`). Your own domain's metrics render as bubbles (`build_metric_bubble_map()`/`build_grouped_bubble_map()`, see Step 4), which don't use a palette at all — bubble color is fixed and shared across metrics for legibility over any demographic backdrop, not per-metric. Add a `palette` to your entry only if you expect it might be rendered as a choropleth some other way later; it's otherwise unused.

`higher_is`, `map_classification`, and `display_format` are documentation only — no code currently reads them. They're worth filling in for future tooling, but leaving them out won't break anything.

---

## Step 3 — Build the clean CSV(s)

Create `scripts/<your_domain>/<your_domain>.py`. Start with the shared tract universe:

```python
from pathlib import Path
import pandas as pd
from src.cb3_utils import (
    load_cb3_tract_universe,
    load_cb3_acs,
    assign_points_to_cb3_tract,
    add_polygon_centroids,
    clean_census_values,
    percent,
    extract_year,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Your Domain"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "your_domain_tract.csv"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS = load_cb3_tract_universe(PROJECT_DIR)

# Thin wrappers so call sites don't repeat the shared arguments.
def _load_acs(filename):
    return load_cb3_acs(filename, RAW_DIR, CB3_TRACT_CODES)

def _assign(frame, lon_col, lat_col, source_tract_col=None):
    return assign_points_to_cb3_tract(
        frame, lon_col, lat_col, cb3_tract_geometry, CB3_TRACT_CODES, source_tract_col
    )
```

Then compute your metrics, assemble them into a clean DataFrame, attach tract centroids, and write:

```python
clean = tracts[["GEOID", "tract_label", "tract_name",
                "nta_code", "nta_name", "cdta_code", "cdta_name"]].copy()

for metric_frame in [metric_frame_1, metric_frame_2, ...]:
    clean = clean.merge(metric_frame, on="GEOID", how="left", validate="one_to_one")

assert len(clean) == 31
assert clean["GEOID"].is_unique

# Every tract-level metric needs a point to plot as a bubble — attach each
# tract's own centroid so maps.qmd doesn't have to recompute it.
clean = add_polygon_centroids(
    clean, cb3_tract_geometry, "GEOID",
    lat_col="tract_centroid_latitude", lon_col="tract_centroid_longitude",
)

clean.to_csv(OUTPUT_PATH, index=False)
print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")
```

**If a metric has native, finer-grained coordinates** (a building, a site — not just a tract), don't collapse it into the tract table. Write it as its own point-level CSV instead, keyed by whatever unique ID the source data has:

```python
POINTS_PATH = CLEAN_DIR / "your_domain_<metric_group>_points.csv"
points = source_frame_with_coordinates[
    ["site_id", "standard_address", "GEOID", "latitude", "longitude", "your_metric"]
]
points.to_csv(POINTS_PATH, index=False)
print(f"Wrote {len(points)} points to {POINTS_PATH}")
```

See `scripts/housing_and_affordability/housing_and_affordability.py` for a full working example of both patterns together.

Run with:

```bash
python -m scripts.<your_domain>.build
```

### Shared utilities in `src/cb3_utils.py`

| Function | What it does |
|---|---|
| `load_cb3_tract_universe(project_dir)` | Returns `(tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS)` |
| `load_cb3_acs(filename, raw_dir, CB3_TRACT_CODES)` | Loads and filters an ACS CSV to the 31 CB3 tracts |
| `assign_points_to_cb3_tract(frame, lon_col, lat_col, ...)` | Spatially joins point records to 2020 tract polygons |
| `add_polygon_centroids(frame, polygons, id_column, ...)` | Attaches each row's polygon centroid as lat/lon columns — works for any polygon geography (tract, ZCTA, NTA), not just tracts |
| `clean_census_values(frame)` | Replaces Census sentinel/suppression values with NaN |
| `percent(numerator, denominator)` | Safe percentage with NaN for zero denominators |
| `extract_year(value)` | Extracts a 4-digit year from various date formats |

---

## Step 4 — Build maps

Maps no longer render a single choropleth per metric. Every map now shows **selectable demographic choropleths as the backdrop** (income, age, race/ethnicity, poverty, LEP — one visible at a time via the layer control) with **your metric drawn as a bubble layer on top**, sized by magnitude. This makes the metric readable against demographic context without needing a second map or a click-through panel.

Create `scripts/<your_domain>/maps.qmd`. Load your metrics and specs from the YAML:

```python
import yaml
from src.map_utils import (
    build_metric_bubble_map,
    build_grouped_bubble_map,
    make_base_map,
    find_project_dir,
)

PROJECT_DIR = find_project_dir()

with open(PROJECT_DIR / "docs" / "metric_metadata.yml") as f:
    ALL_METRICS = yaml.safe_load(f)

def _to_map_spec(meta, include_filename=False):
    spec = {"dimension": meta["short_label"], "label": meta["label"],
            "unit": meta["unit"], "palette": meta["palette"]}
    if include_filename:
        spec["filename"] = meta["filename"]
    for key in ("positive_values_only", "zero_color", "zero_label"):
        if key in meta:
            spec[key] = meta[key]
    return spec

METRIC_SPECS = {
    var: _to_map_spec(meta, include_filename=True)
    for var, meta in ALL_METRICS.items()
    if meta["domain"] == "your_domain"
}
DEMOGRAPHIC_SPECS = {
    var: _to_map_spec(meta)
    for var, meta in ALL_METRICS.items()
    if meta["domain"] == "demographics"
}
```

Build one map per metric with `build_metric_bubble_map()`. By default the bubble sits at the tract centroid (`tract_centroid_latitude`/`tract_centroid_longitude` from Step 3):

```python
build_metric_bubble_map(
    tracts, METRIC_SPECS, DEMOGRAPHIC_SPECS, "your_metric",
    title="CB3 Your Domain: Your metric",
    output_path=MAP_OUTPUT_DIR / METRIC_SPECS["your_metric"]["filename"],
)
```

For a metric with native coordinates (a building-level points CSV from Step 3), pass `points`/`lat_col`/`lon_col` instead of letting it default to tract centroids:

```python
build_metric_bubble_map(
    tracts, METRIC_SPECS, DEMOGRAPHIC_SPECS, "your_site_metric",
    title="CB3 Your Domain: Your site-level metric",
    output_path=MAP_OUTPUT_DIR / METRIC_SPECS["your_site_metric"]["filename"],
    points=your_points_df,
    lat_col="latitude", lon_col="longitude",
    tooltip_fields=["standard_address", "tract_label"],
    tooltip_aliases=["Address", "Census tract"],
)
```

If you have two closely related metrics (two time windows, two severity classes) that are more useful compared side by side than viewed one at a time, use `build_grouped_bubble_map()` instead — it draws one bubble layer per metric, each with a distinct color and its own stacked size legend:

```python
build_grouped_bubble_map(
    tracts, METRIC_SPECS, DEMOGRAPHIC_SPECS,
    ["your_metric_window_a", "your_metric_window_b"],
    title="CB3 Your Domain: Your metric by window",
    subtitle="Bubbles: size shows magnitude, split into two selectable layers.",
    output_path=MAP_OUTPUT_DIR / "your_metric_by_window.html",
)
```

See `scripts/housing_and_affordability/housing_and_affordability_maps.qmd` for a full working example, including thin per-domain wrappers around these two functions so call sites don't repeat `tracts`/`METRIC_SPECS`/`DEMOGRAPHIC_SPECS`/`MAP_OUTPUT_DIR` every time.

Render with:

```bash
quarto render scripts/<your_domain>/maps.qmd
```

---

## Do not modify

- `src/cb3_utils.py` — shared tract universe and cleaning utilities
- `src/map_utils.py` — shared Folium helpers
- `data/raw/Geography/` — shared geography files
- Other domains' folders under `scripts/` and `data/raw/` (Sharepoint)
