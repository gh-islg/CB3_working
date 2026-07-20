from pathlib import Path
import pandas as pd
from src.cb3_utils import(
    load_cb3_tract_universe,
    clean_census_values,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_DIR / "data" / "raw" / "Economics and Food"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "economic-food.csv"

CLEAN_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------------------------
# Expected raw files - update is needed; a better way to map higher level dataset
# -----------------------------------------------------------------------------------------------
POVERTY_FILE = "acs_5yr_2024_B17020.csv"
FOOD_GAP_FILE = "Emergency_Food_Supply_Gap_20260420.csv"
DRS_FILE = "District_Resource_Statement_(DRS)_20260420.csv"


def _find_existing_file(raw_dir, preferred_name, keyword_options=None):
    """
    Find raw file by exact filename first, then by keyword fallback.
    This makes the script less brittle if file names differ slightly.
    """
    exact_path = raw_dir / preferred_name

    if exact_path.exists():
        return exact_path

    if keyword_options is None:
        keyword_options = []

    files = list(raw_dir.glob("*"))

    for keyword_group in keyword_options:
        keyword_group = [k.lower() for k in keyword_group]

        for file in files:
            name = file.name.lower()
            if all(k in name for k in keyword_group):
                return file

    raise FileNotFoundError(
        f"Could not find {preferred_name} in {raw_dir}. "
        f"Available files: {[f.name for f in files]}"
    )


def _standardize_columns(frame):
    frame = frame.copy()
    frame.columns = (
        frame.columns
        .astype(str)
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("(", "", regex=False)
        .str.replace(")", "", regex=False)
        .str.replace(".", "", regex=False)
    )
    return frame


def _find_col(frame, candidates):
    for col in candidates:
        if col in frame.columns:
            return col

    lower_lookup = {col.lower(): col for col in frame.columns}

    for col in candidates:
        if col.lower() in lower_lookup:
            return lower_lookup[col.lower()]

    return None


def _to_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )


def _ensure_geoid(frame):
    frame = frame.copy()

    geoid_col = _find_col(
        frame,
        [
            "GEOID",
            "geoid",
            "GeoID",
            "GEO_ID",
            "Geography_ID",
            "Census_Tract",
            "tract_geoid",
            "tract_fips",
        ],
    )

    if geoid_col is not None:
        frame["GEOID"] = (
            frame[geoid_col]
            .astype(str)
            .str.extract(r"(\d{11})")[0]
            .str.zfill(11)
        )
        return frame

    # Some Census API exports do not include GEOID directly.
    # They provide state, county, and tract separately.
    state_col = _find_col(frame, ["state", "STATE"])
    county_col = _find_col(frame, ["county", "COUNTY"])
    tract_col = _find_col(frame, ["tract", "TRACT"])

    if state_col is not None and county_col is not None and tract_col is not None:
        frame["GEOID"] = (
            frame[state_col].astype(str).str.zfill(2)
            + frame[county_col].astype(str).str.zfill(3)
            + frame[tract_col].astype(str).str.zfill(6)
        )
        return frame

    raise ValueError(
        "Could not identify or construct GEOID column. "
        "Expected either GEOID-like column or state/county/tract columns. "
        f"Columns found: {frame.columns.tolist()}"
    )

# ------------------------------------------------------------
# Metric 1: Poverty rate by tract
# ------------------------------------------------------------
def build_poverty_metrics():
    poverty_path = _find_existing_file(
        RAW_DIR,
        POVERTY_FILE,
        keyword_options=[
            ["B17020"],
            ["poverty"],
        ],
    )

    poverty = pd.read_csv(poverty_path, dtype=str)
    poverty = _standardize_columns(poverty)
    poverty = _ensure_geoid(poverty)
    poverty = clean_census_values(poverty)

    # ACS B17020 usual columns:
    # B17020_001E = total population for whom poverty status is determined
    # B17020_002E = income in past 12 months below poverty level
    total_col = _find_col(
        poverty,
        [
            "B17020_001E",
            "B17020_001_E",
            "Estimate_Total",
            "total",
        ],
    )

    below_col = _find_col(
        poverty,
        [
            "B17020_002E",
            "B17020_002_E",
            "Estimate_Total_Income_in_the_past_12_months_below_poverty_level",
            "below_poverty",
        ],
    )

    if total_col is None or below_col is None:
        raise ValueError(
            "Could not identify B17020 total/below poverty columns. "
            f"Columns found: {poverty.columns.tolist()}"
        )

    poverty["poverty_universe_population"] = _to_numeric(poverty[total_col])
    poverty["poverty_count"] = _to_numeric(poverty[below_col])

    poverty["poverty_rate_pct"] = (
        poverty["poverty_count"]
        / poverty["poverty_universe_population"]
        * 100
    )

    out = poverty[
        [
            "GEOID",
            "poverty_universe_population",
            "poverty_count",
            "poverty_rate_pct",
        ]
    ].copy()

    out = out.drop_duplicates(subset=["GEOID"])

    return out


# ------------------------------------------------------------
# Metric 2: Food insecurity / emergency food supply gap
# ------------------------------------------------------------
def build_food_gap_metrics():
    food_path = _find_existing_file(
        RAW_DIR,
        FOOD_GAP_FILE,
        keyword_options=[
            ["food", "gap"],
            ["emergency", "food"],
        ],
    )

    food = pd.read_csv(food_path, dtype=str)
    food = _standardize_columns(food)
    food = clean_census_values(food)

    # This file may be NTA-level rather than tract-level
    nta_col = _find_col(
        food,
        [
            "NTA2020",
            "NTA_Code",
            "nta_code",
            "NTA",
            "NTAName",
            "nta_name",
            "Neighborhood_Tabulation_Area_NTA",
            "Neighborhood_Tabulation_Area_NTA_Name",
        ],
    )

    rank_col = _find_col(
        food,
        [
            "Rank",
            "rank",
            "Food_Insecurity_Rank",
            "food_insecurity_rank",
            "Need_Rank",
            "need_rank",
            "Supply_Gap_Rank",
            "supply_gap_rank",
        ],
    )

    score_col = _find_col(
        food,
        [
            "Score",
            "score",
            "Weighted_Score",
            "weighted_score",
            "Food_Insecurity_Score",
            "food_insecurity_score",
            "Supply_Gap_Score",
            "supply_gap_score",
            "Gap_Score",
            "gap_score",
        ],
    )

    if nta_col is None:
        raise ValueError(
            "Could not identify NTA column in food gap file. "
            f"Columns found: {food.columns.tolist()}"
        )

    food = food.rename(columns={nta_col: "nta_join_key"})
    food["nta_join_key"] = food["nta_join_key"].astype(str).str.strip()

    keep_cols = ["nta_join_key"]

    if rank_col is not None:
        food["food_supply_gap_rank"] = _to_numeric(food[rank_col])
        keep_cols.append("food_supply_gap_rank")

    if score_col is not None:
        food["food_supply_gap_score"] = _to_numeric(food[score_col])
        keep_cols.append("food_supply_gap_score")

    if len(keep_cols) == 1:
        raise ValueError(
            "Could not identify food gap rank or score column. "
            f"Columns found: {food.columns.tolist()}"
        )

    food = food[keep_cols].drop_duplicates(subset=["nta_join_key"]).copy()

    return food



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

    # --------------------------------------------------------
    # Add poverty tract-level metric
    # --------------------------------------------------------
    poverty = build_poverty_metrics()

    clean = clean.merge(
        poverty,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    # --------------------------------------------------------
    # Add food gap NTA-level metric to tracts
    # --------------------------------------------------------
    food_gap = build_food_gap_metrics()

    # Try joining food file to tract NTA code first.
    clean = clean.merge(
        food_gap,
        left_on="nta_code",
        right_on="nta_join_key",
        how="left",
        validate="many_to_one",
    )

    # If direct NTA code join failed, try NTA name join.
    if "food_supply_gap_rank" in clean.columns:
        missing_food = clean["food_supply_gap_rank"].isna().sum()
    else:
        missing_food = len(clean)

    if missing_food == len(clean):
        clean = clean.drop(columns=["nta_join_key"], errors="ignore")

        food_gap_name = food_gap.copy()
        food_gap_name["nta_join_key"] = (
            food_gap_name["nta_join_key"]
            .astype(str)
            .str.lower()
            .str.strip()
        )

        clean["_nta_name_join"] = (
            clean["nta_name"]
            .astype(str)
            .str.lower()
            .str.strip()
        )

        clean = clean.merge(
            food_gap_name,
            left_on="_nta_name_join",
            right_on="nta_join_key",
            how="left",
            validate="many_to_one",
        )

        clean = clean.drop(columns=["_nta_name_join"], errors="ignore")

    clean = clean.drop(columns=["nta_join_key"], errors="ignore")

    # --------------------------------------------------------
    # Notes
    # --------------------------------------------------------
    clean["economic_food_methodology_note"] = (
        "poverty_rate_pct uses ACS 5-year B17020 tract-level data. "
        "food_supply_gap_rank/score are joined from the emergency food supply gap file, "
        "which may be NTA-level and therefore repeated across tracts within the same NTA."
    )

    clean["economic_food_qa_note"] = (
        "Food supply gap metrics should be cross-checked against the memo and source file, "
        "especially if NTA names/codes differ across source versions."
    )

    assert len(clean) == 31, f"Expected 31 CB3 tracts, got {len(clean)}"
    assert clean["GEOID"].is_unique, "GEOID is not unique in clean economic_food output."

    clean.to_csv(OUTPUT_PATH, index=False)

    print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")

    print("\nMissing values by key metric:")
    metric_cols = [
        "poverty_rate_pct",
        "food_supply_gap_rank",
        "food_supply_gap_score",
    ]

    for col in metric_cols:
        if col in clean.columns:
            print(f" - {col}: {clean[col].isna().sum()} missing")

    print("\nColumns:")
    for col in clean.columns:
        print(f" - {col}")


if __name__ == "__main__":
    main()
