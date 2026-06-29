# CB3 Equity Report — Collaborator Guide

## Overview

Each collaborator is responsible for one domain of the CB3 equity report. Your job is to:

1. Clean your domain's raw data into a tract-level CSV
2. Register your metrics in the shared metadata file
3. Build interactive Folium maps from the clean data

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
│       ├── build.py            # cleans raw data → clean CSV
│       └── maps.qmd            # renders Folium maps from clean CSV
└── src/
    ├── cb3_utils.py            # shared data utilities
    ├── map_utils.py            # shared Folium helpers
    └── demographic_profile_panel.py
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

Each domain's clean CSV follows a **wide schema**: one row per census tract, one column per metric.

```
GEOID            — 11-character 2020 Census tract identifier (string, zero-padded)
tract_label      — human-readable tract number (e.g. "2", "14.02")
tract_name       — full name (e.g. "Census Tract 2, Manhattan County, New York")
nta_code         — 2020 NTA code
nta_name         — 2020 NTA name
cdta_code        — 2020 CDTA code (all rows: MN03)
cdta_name        — 2020 CDTA name
<metric_1>       — numeric tract-level value
<metric_2>       — ...
...
<flag columns>   — text notes on geography, data availability, methodology
```

The output always has **31 rows** — one per official CB3 census tract — and is written to `data/clean/<your_domain>.csv`.

---

## Step 1 — Add raw data

Drop your source files into `data/raw/<Your Domain>/`. The Geography files should be placed in `data/raw/Geography/` and are shared across all domains. Since we are not hosting any data on GitHub, please utilize the folders in the project's Sharepoint.

---

## Step 2 — Register your metrics

Add an entry to [`docs/metric_metadata.yml`](metric_metadata.yml) for each metric you plan to map. This is the **only place** labels, units, palettes, and display options are defined — do not hardcode them in your scripts or notebooks.

```yaml
unemployment_rate:
  domain: economic_security
  short_label: Unemployment rate       # shown in the map layer control
  label: Civilian unemployment rate    # shown in tooltips and popups
  description: >
    Share of the civilian labor force aged 16+ that is unemployed.
  unit: "%"
  palette: ["#ffffcc", "#feb24c", "#f03b20", "#bd0026", "#7a0177"]
  source: ACS 5-year estimates, table B23025
  year: 2023
  geography: Census tract
  higher_is: worse
  map_classification: quintile
  display_format: percent
  filename: unemployment_rate.html
```

Required keys: `domain`, `short_label`, `label`, `unit`, `palette`, `filename`.  
Optional keys: `positive_values_only: true`, `zero_color`, `zero_label` (for metrics where zero needs a separate color).

---

## Step 3 — Build the clean tract table

Create `scripts/<your_domain>/economity_security.py`. Start with the shared tract universe:

```python
from pathlib import Path
import pandas as pd
from src.cb3_utils import (
    load_cb3_tract_universe,
    load_cb3_acs,
    assign_points_to_cb3_tract,
    clean_census_values,
    percent,
    extract_year,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Your Domain"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "your_domain.csv"
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

Then compute your metrics, assemble them into a clean DataFrame, and write:

```python
clean = tracts[["GEOID", "tract_label", "tract_name",
                "nta_code", "nta_name", "cdta_code", "cdta_name"]].copy()

for metric_frame in [metric_frame_1, metric_frame_2, ...]:
    clean = clean.merge(metric_frame, on="GEOID", how="left", validate="one_to_one")

assert len(clean) == 31
assert clean["GEOID"].is_unique

clean.to_csv(OUTPUT_PATH, index=False)
print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")
```

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
| `clean_census_values(frame)` | Replaces Census sentinel/suppression values with NaN |
| `percent(numerator, denominator)` | Safe percentage with NaN for zero denominators |
| `extract_year(value)` | Extracts a 4-digit year from various date formats |

---

## Step 4 — Build maps

Create `scripts/<your_domain>/maps.qmd`. Load your metrics and specs from the YAML:

```python
import yaml
from src.map_utils import add_metric_layer, make_base_map, add_map_title, find_project_dir
from src.demographic_profile_panel import PROFILE_CLICK_CALLBACK, add_profile_click_layer, add_demographic_profile_panel

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
```

Add a layer per metric:

```python
m = make_base_map(tracts)
for i, (metric, spec) in enumerate(METRIC_SPECS.items()):
    add_metric_layer(m, tracts, metric, spec, show=i == 0, overlay=False)
```

Render with:

```bash
quarto render scripts/<your_domain>/maps.qmd
```

---

## Do not modify

- `src/cb3_utils.py` — shared tract universe and cleaning utilities
- `src/map_utils.py` — shared Folium helpers
- `src/demographic_profile_panel.py` — shared demographic profile panel (TBD if we want this)
- `data/raw/Geography/` — shared geography files
- Other domains' folders under `scripts/` and `data/raw/` (Sharepoint)
