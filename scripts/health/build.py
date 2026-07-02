from pathlib import Path

import pandas as pd

from src.cb3_utils import (
    load_cb3_tract_universe,
    clean_census_values,
)


PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Health"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "health.csv"

CLEAN_DIR.mkdir(parents=True, exist_ok=True)


PLACES_FILE = "PLACES__Census_Tract_Data_(GIS_Friendly_Format),_2025_release_20260504.csv"


def _find_col(frame, candidates):
    for col in candidates:
        if col in frame.columns:
            return col
    return None


def _to_numeric(frame, columns):
    frame = frame.copy()

    for col in columns:
        if col in frame.columns:
            frame[col] = pd.to_numeric(
                frame[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )

    return frame


def build_places_health_metrics():
    places_path = RAW_DIR / PLACES_FILE

    if not places_path.exists():
        raise FileNotFoundError(
            f"Could not find PLACES file at: {places_path}"
        )

    places = pd.read_csv(places_path, dtype=str)
    places.columns = places.columns.str.strip()

    geoid_col = _find_col(
        places,
        [
            "TractFIPS",
            "LocationID",
            "GEOID",
            "geoid",
            "tract_fips",
        ],
    )

    if geoid_col is None:
        raise ValueError("Could not identify tract GEOID column in PLACES file.")

    places["GEOID"] = (
        places[geoid_col]
        .astype(str)
        .str.extract(r"(\d{11})")[0]
        .str.zfill(11)
    )

    uninsured_col = _find_col(
        places,
        [
            "ACCESS2_CrudePrev",
            "ACCESS2_crudeprev",
            "access2_crudeprev",
        ],
    )

    checkup_col = _find_col(
        places,
        [
            "CHECKUP_CrudePrev",
            "CHECKUP_crudeprev",
            "checkup_crudeprev",
        ],
    )

    total_pop_col = _find_col(
        places,
        [
            "TotalPopulation",
            "totalpopulation",
            "total_population",
        ],
    )

    adult_pop_col = _find_col(
        places,
        [
            "TotalPop18plus",
            "totalpop18plus",
            "total_pop_18plus",
            "adult_population",
        ],
    )

    if uninsured_col is None:
        raise ValueError("Missing ACCESS2_CrudePrev / uninsured adults column.")

    if checkup_col is None:
        raise ValueError("Missing CHECKUP_CrudePrev / routine checkup column.")

    keep_cols = ["GEOID"]
    rename_map = {}

    if total_pop_col is not None:
        keep_cols.append(total_pop_col)
        rename_map[total_pop_col] = "places_total_population"

    if adult_pop_col is not None:
        keep_cols.append(adult_pop_col)
        rename_map[adult_pop_col] = "places_adult_population"

    keep_cols.extend([uninsured_col, checkup_col])

    rename_map.update(
        {
            uninsured_col: "uninsured_adults_pct",
            checkup_col: "routine_checkup_past_year_pct",
        }
    )

    health = places[keep_cols].copy()
    health = health.rename(columns=rename_map)

    health = clean_census_values(health)

    numeric_cols = [
        "places_total_population",
        "places_adult_population",
        "uninsured_adults_pct",
        "routine_checkup_past_year_pct",
    ]

    health = _to_numeric(health, numeric_cols)
    health = clean_census_values(health)

    health["no_routine_checkup_pct"] = (
        100 - health["routine_checkup_past_year_pct"]
    )

    health = (
        health
        .dropna(subset=["GEOID"])
        .drop_duplicates(subset=["GEOID"])
        .copy()
    )

    output_cols = [
        "GEOID",
        "places_total_population",
        "places_adult_population",
        "uninsured_adults_pct",
        "routine_checkup_past_year_pct",
        "no_routine_checkup_pct",
    ]

    output_cols = [col for col in output_cols if col in health.columns]

    return health[output_cols].copy()


def main():
    tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS = (
        load_cb3_tract_universe(PROJECT_DIR)
    )

    clean = tracts[
        [
            "GEOID",
            "tract_label",
            "tract_name",
            "nta_code",
            "nta_name",
            "cdta_code",
            "cdta_name",
        ]
    ].copy()

    health_metrics = build_places_health_metrics()

    clean = clean.merge(
        health_metrics,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    clean["health_access_methodology_note"] = (
        "uninsured_adults_pct uses CDC PLACES ACCESS2_CrudePrev. "
        "no_routine_checkup_pct is calculated as 100 - CHECKUP_CrudePrev "
        "and is used as a proxy for limited primary care access."
    )

    clean["health_domain_qa_note"] = (
        "Health outputs should be cross-checked against the memo before finalizing, "
        "especially where memo values use NTA-level or non-tract-level framing."
    )

    assert len(clean) == 31, f"Expected 31 CB3 tracts, got {len(clean)}"
    assert clean["GEOID"].is_unique, "GEOID is not unique in clean health output."

    clean.to_csv(OUTPUT_PATH, index=False)

    print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")

    print("\nMissing values by key metric:")
    metric_cols = [
        "uninsured_adults_pct",
        "routine_checkup_past_year_pct",
        "no_routine_checkup_pct",
    ]

    for col in metric_cols:
        if col in clean.columns:
            print(f" - {col}: {clean[col].isna().sum()} missing")


if __name__ == "__main__":
    main()