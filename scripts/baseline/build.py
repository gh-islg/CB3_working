import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests


PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Baseline"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"

OUTPUT_PATH = CLEAN_DIR / "nyc_tract_baseline_profile_clean.csv"
CB3_REFERENCE_PATH = CLEAN_DIR / "cb3_tract_baseline_profile_clean.csv"

ACS_YEAR = 2024
ACS_ENDPOINT = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
STATE = "36"

NYC_COUNTIES = {
    "005": "Bronx",
    "047": "Brooklyn",
    "061": "Manhattan",
    "081": "Queens",
    "085": "Staten Island",
}

# ---------------------------------------------------------------------------
# CB3 baseline source-variable logic
# ---------------------------------------------------------------------------

# B01001 — Sex by Age
AGE_0_19 = [
    *[f"B01001_{i:03d}E" for i in range(3, 8)],
    *[f"B01001_{i:03d}E" for i in range(27, 32)],
]
AGE_20_64 = [
    *[f"B01001_{i:03d}E" for i in range(8, 20)],
    *[f"B01001_{i:03d}E" for i in range(32, 44)],
]
AGE_65_PLUS = [
    *[f"B01001_{i:03d}E" for i in range(20, 26)],
    *[f"B01001_{i:03d}E" for i in range(44, 50)],
]

# B17020 — Poverty Status by Age existing CB3 baseline's "senior" poverty fields use AGE 60+,
# not 65+. B17020 groups seniors as 60–74, 75–84, and 85+.
SENIOR_BELOW_POVERTY = [
    "B17020_007E",  # 60 to 74 years, below poverty
    "B17020_008E",  # 75 to 84 years, below poverty
    "B17020_009E",  # 85 years and over, below poverty
]
SENIOR_AT_OR_ABOVE_POVERTY = [
    "B17020_015E",  # 60 to 74 years, at/above poverty
    "B17020_016E",  # 75 to 84 years, at/above poverty
    "B17020_017E",  # 85 years and over, at/above poverty
]

TABLE_VARIABLES = {
    "B01001": [
        "B01001_001E",  # total population
        "B01001_002E",  # male population
        "B01001_026E",  # female population
        *AGE_0_19,
        *AGE_20_64,
        *AGE_65_PLUS,
    ],
    "B03002": [
        "B03002_001E",  # race/ethnicity total
        "B03002_003E",  # White alone, not Hispanic
        "B03002_004E",  # Black alone, not Hispanic
        "B03002_006E",  # Asian alone, not Hispanic
        "B03002_012E",  # Hispanic or Latino
    ],
    "B19013": [
        "B19013_001E",  # median household income
    ],
    "B19001": [
        "B19001_001E",  # household income universe
    ],
    "B17020": [
        "B17020_001E",  # poverty-status total
        "B17020_002E",  # below poverty total
        "B17020_010E",  # at/above poverty total
        *SENIOR_BELOW_POVERTY,
        *SENIOR_AT_OR_ABOVE_POVERTY,
    ],
    "B16002": [
        "B16002_001E",  # total household-language universe
        "B16002_002E",  # English-only households
    ],
}


OUTPUT_COLUMNS = [
    "geoid",
    "tract_name",
    "total_population",
    "male_population",
    "female_population",
    "age_0_to_19_population",
    "age_20_to_64_population",
    "age_65_plus_population",
    "male_share",
    "female_share",
    "age_0_to_19_share",
    "age_20_to_64_share",
    "age_65_plus_share",
    "race_ethnicity_total_population",
    "white_non_hispanic_population",
    "black_non_hispanic_population",
    "asian_non_hispanic_population",
    "hispanic_population",
    "white_non_hispanic_share",
    "black_non_hispanic_share",
    "asian_non_hispanic_share",
    "hispanic_share",
    "median_household_income",
    "total_households_income_universe",
    "poverty_status_total",
    "below_poverty_total",
    "at_or_above_poverty_total",
    "senior_poverty_population",
    "senior_total_poverty_universe",
    "poverty_rate",
    "senior_poverty_rate",
    "total_households_language_universe",
    "lep_households",
    "lep_household_share",
]


def safe_pct(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    out = pd.Series(np.nan, index=denominator.index, dtype="float64")
    valid = denominator > 0
    out.loc[valid] = numerator.loc[valid] / denominator.loc[valid] * 100
    return out


def fetch_table(table: str, variables: list[str]) -> pd.DataFrame:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. Export your Census API key before "
            "running this script."
        )

    frames = []

    for county, borough in NYC_COUNTIES.items():
        params = {
            "get": ",".join(["NAME", *variables]),
            "for": "tract:*",
            "in": f"state:{STATE} county:{county}",
            "key": key,
        }

        response = requests.get(ACS_ENDPOINT, params=params, timeout=120)
        response.raise_for_status()

        try:
            rows = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Census API returned non-JSON data for {table} / {borough}. "
                f"HTTP {response.status_code}: {response.text[:500]}"
            ) from exc

        frame = pd.DataFrame(rows[1:], columns=rows[0])
        frame["geoid"] = frame["state"] + frame["county"] + frame["tract"]
        frame["borough"] = borough

        for col in variables:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        frames.append(frame)
        print(f"{table}: fetched {len(frame):,} tracts for {borough}")

    out = pd.concat(frames, ignore_index=True)
    out["geoid"] = out["geoid"].astype("string").str.zfill(11)

    if out["geoid"].duplicated().any():
        duplicates = out.loc[out["geoid"].duplicated(), "geoid"].tolist()
        raise ValueError(f"{table}: duplicate GEOIDs found: {duplicates[:10]}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"acs_5yr_{ACS_YEAR}_{table}.csv"
    out.to_csv(raw_path, index=False)
    print(f"{table}: wrote {raw_path}")

    return out


def build_baseline(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # -----------------------------------------------------------------------
    # Population / sex / age — B01001
    # -----------------------------------------------------------------------
    age = tables["B01001"].copy()

    out = age[["geoid", "NAME"]].rename(columns={"NAME": "tract_name"}).copy()

    out["total_population"] = age["B01001_001E"]
    out["male_population"] = age["B01001_002E"]
    out["female_population"] = age["B01001_026E"]

    out["age_0_to_19_population"] = age[AGE_0_19].sum(axis=1, min_count=1)
    out["age_20_to_64_population"] = age[AGE_20_64].sum(axis=1, min_count=1)
    out["age_65_plus_population"] = age[AGE_65_PLUS].sum(axis=1, min_count=1)

    out["male_share"] = safe_pct(out["male_population"], out["total_population"])
    out["female_share"] = safe_pct(out["female_population"], out["total_population"])
    out["age_0_to_19_share"] = safe_pct(out["age_0_to_19_population"], out["total_population"])
    out["age_20_to_64_share"] = safe_pct(out["age_20_to_64_population"], out["total_population"])
    out["age_65_plus_share"] = safe_pct(out["age_65_plus_population"], out["total_population"])

    # -----------------------------------------------------------------------
    # Race / ethnicity — B03002
    # -----------------------------------------------------------------------
    race = tables["B03002"].copy()
    race_metrics = pd.DataFrame({
        "geoid": race["geoid"],
        "race_ethnicity_total_population": race["B03002_001E"],
        "white_non_hispanic_population": race["B03002_003E"],
        "black_non_hispanic_population": race["B03002_004E"],
        "asian_non_hispanic_population": race["B03002_006E"],
        "hispanic_population": race["B03002_012E"],
    })

    race_metrics["white_non_hispanic_share"] = safe_pct(
        race_metrics["white_non_hispanic_population"],
        race_metrics["race_ethnicity_total_population"],
    )
    race_metrics["black_non_hispanic_share"] = safe_pct(
        race_metrics["black_non_hispanic_population"],
        race_metrics["race_ethnicity_total_population"],
    )
    race_metrics["asian_non_hispanic_share"] = safe_pct(
        race_metrics["asian_non_hispanic_population"],
        race_metrics["race_ethnicity_total_population"],
    )
    race_metrics["hispanic_share"] = safe_pct(
        race_metrics["hispanic_population"],
        race_metrics["race_ethnicity_total_population"],
    )

    out = out.merge(race_metrics, on="geoid", how="left", validate="one_to_one")

    # -----------------------------------------------------------------------
    # Household income — B19013 + B19001
    # -----------------------------------------------------------------------
    median_income = tables["B19013"][["geoid", "B19013_001E"]].rename(
        columns={"B19013_001E": "median_household_income"}
    )
    median_income.loc[
        median_income["median_household_income"] < 0,
        "median_household_income"
    ] = np.nan

    income_universe = tables["B19001"][["geoid", "B19001_001E"]].rename(
        columns={"B19001_001E": "total_households_income_universe"}
    )

    out = out.merge(median_income, on="geoid", how="left", validate="one_to_one")
    out = out.merge(income_universe, on="geoid", how="left", validate="one_to_one")

    # -----------------------------------------------------------------------
    # Poverty / senior poverty — B17020
    #
    # Match the existing CB3 baseline exactly:
    # - overall poverty = all ages
    # - "senior" poverty = age 60+ (60–74, 75–84, 85+)
    # -----------------------------------------------------------------------
    poverty = tables["B17020"].copy()
    poverty_metrics = pd.DataFrame({
        "geoid": poverty["geoid"],
        "poverty_status_total": poverty["B17020_001E"],
        "below_poverty_total": poverty["B17020_002E"],
        "at_or_above_poverty_total": poverty["B17020_010E"],
    })

    poverty_metrics["senior_poverty_population"] = poverty[
        SENIOR_BELOW_POVERTY
    ].sum(axis=1, min_count=1)

    senior_at_or_above = poverty[
        SENIOR_AT_OR_ABOVE_POVERTY
    ].sum(axis=1, min_count=1)

    poverty_metrics["senior_total_poverty_universe"] = (
        poverty_metrics["senior_poverty_population"] + senior_at_or_above
    )

    poverty_metrics["poverty_rate"] = safe_pct(
        poverty_metrics["below_poverty_total"],
        poverty_metrics["poverty_status_total"],
    )
    poverty_metrics["senior_poverty_rate"] = safe_pct(
        poverty_metrics["senior_poverty_population"],
        poverty_metrics["senior_total_poverty_universe"],
    )

    out = out.merge(poverty_metrics, on="geoid", how="left", validate="one_to_one")

    # -----------------------------------------------------------------------
    # Household language — B16002
    #
    # In the CB3 baseline, "lep_households" is effectively all households
    # that are NOT English-only:
    #
    #     total household language universe - English-only households
    #
    # This is intentionally retained here so the NYC baseline matches CB3.
    # It is different from the later Language-domain C16002 LEP calculation.
    # -----------------------------------------------------------------------
    language = tables["B16002"].copy()

    language_metrics = pd.DataFrame({
        "geoid": language["geoid"],
        "total_households_language_universe": language["B16002_001E"],
    })
    language_metrics["lep_households"] = (
        language["B16002_001E"] - language["B16002_002E"]
    )
    language_metrics["lep_household_share"] = safe_pct(
        language_metrics["lep_households"],
        language_metrics["total_households_language_universe"],
    )

    out = out.merge(language_metrics, on="geoid", how="left", validate="one_to_one")

    out = out[OUTPUT_COLUMNS].sort_values("geoid").reset_index(drop=True)

    assert out["geoid"].is_unique
    assert out["geoid"].str.fullmatch(r"\d{11}").all()

    return out


def validate_against_cb3(nyc: pd.DataFrame) -> None:
    """Fail if the NYC build does not reproduce the existing CB3 clean file."""
    if not CB3_REFERENCE_PATH.exists():
        print(f"CB3 reference not found; skipping parity check: {CB3_REFERENCE_PATH}")
        return

    cb3 = pd.read_csv(CB3_REFERENCE_PATH, dtype={"geoid": "string"})
    cb3["geoid"] = cb3["geoid"].str.zfill(11)

    missing_geoids = sorted(set(cb3["geoid"]) - set(nyc["geoid"]))
    if missing_geoids:
        raise AssertionError(
            f"NYC output is missing {len(missing_geoids)} CB3 GEOIDs: {missing_geoids}"
        )

    merged = cb3.merge(
        nyc,
        on="geoid",
        how="left",
        suffixes=("_cb3", "_nyc"),
        validate="one_to_one",
    )

    mismatches = []

    for col in cb3.columns:
        if col == "geoid":
            continue

        cb3_col = f"{col}_cb3"
        nyc_col = f"{col}_nyc"

        if cb3_col not in merged.columns or nyc_col not in merged.columns:
            mismatches.append((col, "missing column"))
            continue

        if col == "tract_name":
            same = (
                merged[cb3_col].fillna("").astype(str)
                == merged[nyc_col].fillna("").astype(str)
            )
        else:
            left = pd.to_numeric(merged[cb3_col], errors="coerce")
            right = pd.to_numeric(merged[nyc_col], errors="coerce")
            same = np.isclose(left, right, rtol=0, atol=1e-9, equal_nan=True)

        if not np.all(same):
            bad = merged.loc[~same, ["geoid", cb3_col, nyc_col]].head(10)
            mismatches.append((col, bad.to_dict("records")))

    if mismatches:
        print("\nCB3 parity check FAILED.")
        for col, detail in mismatches:
            print(f"\n{col}:")
            print(detail)
        raise AssertionError(
            f"NYC baseline does not exactly reproduce CB3 logic for "
            f"{len(mismatches)} column(s)."
        )

    print("\nCB3 parity check PASSED:")
    print(f"  {len(cb3)} / {len(cb3)} CB3 tracts found")
    print(f"  {len(cb3.columns)} / {len(cb3.columns)} CB3 columns matched")
    print("  All CB3 values reproduced by the NYC build")


def main() -> None:
    tables = {
        table: fetch_table(table, variables)
        for table, variables in TABLE_VARIABLES.items()
    }

    baseline = build_baseline(tables)

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    baseline.to_csv(OUTPUT_PATH, index=False)

    print(f"\nWrote NYC baseline: {OUTPUT_PATH}")
    print(f"Rows: {len(baseline):,}")
    print(f"Columns: {len(baseline.columns)}")

    validate_against_cb3(baseline)


if __name__ == "__main__":
    main()
