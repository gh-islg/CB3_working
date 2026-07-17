"""Build CB3 Language clean file from ACS B16001 person-level language data.

Supervisor-directed logic:
- Use ACS B16001 person-level language data for mapped language metrics.
- Produce three mapped metrics: Spanish, Chinese, and Other.
- Other = Tagalog + Korean + French/Haitian/Cajun + Arabic + Russian/Slavic + Vietnamese.
- Keep C16002 household LEP only as demographic/context layer: lep_household_share.
- Write one map-ready clean file: data/clean/language.csv.
"""

from __future__ import annotations

import re
from pathlib import Path
import sys
from typing import Iterable

import geopandas as gpd
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.cb3_utils import clean_census_values, load_cb3_tract_universe

CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "language.csv"
LOG_PATH = CLEAN_DIR / "language_build_log.txt"

RAW_DIR_CANDIDATES = [
    PROJECT_DIR / "data" / "raw" / "Language",
    PROJECT_DIR / "data" / "raw" / "Demographics",
    PROJECT_DIR / "data" / "raw" / "ACS",
    PROJECT_DIR / "data" / "raw",
]

BASELINE_CANDIDATES = [
    CLEAN_DIR / "tract_baseline_profile_clean.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk_clean.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile_clean.csv",
]

ID_COLUMNS = {"GEOID", "NAME", "state", "county", "tract", "geoid", "GEOID20", "GEO_ID"}

B16001_LANGUAGE_GROUPS = {
    "spanish": {"speaker_col": "B16001_003E", "very_well_col": "B16001_004E", "lep_col": "B16001_005E"},
    "french_haitian_cajun": {"speaker_col": "B16001_006E", "very_well_col": "B16001_007E", "lep_col": "B16001_008E"},
    "russian_slavic": {"speaker_col": "B16001_012E", "very_well_col": "B16001_013E", "lep_col": "B16001_014E"},
    "korean": {"speaker_col": "B16001_018E", "very_well_col": "B16001_019E", "lep_col": "B16001_020E"},
    "chinese": {"speaker_col": "B16001_021E", "very_well_col": "B16001_022E", "lep_col": "B16001_023E"},
    "vietnamese": {"speaker_col": "B16001_024E", "very_well_col": "B16001_025E", "lep_col": "B16001_026E"},
    "tagalog": {"speaker_col": "B16001_027E", "very_well_col": "B16001_028E", "lep_col": "B16001_029E"},
    "arabic": {"speaker_col": "B16001_033E", "very_well_col": "B16001_034E", "lep_col": "B16001_035E"},
}

OTHER_LANGUAGE_COMPONENTS = [
    "tagalog",
    "korean",
    "french_haitian_cajun",
    "arabic",
    "russian_slavic",
    "vietnamese",
]


def normalize_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lookup = {normalize_column_name(column): column for column in df.columns}
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
        normalized = normalize_column_name(candidate)
        if normalized in lookup:
            return lookup[normalized]
    return None


def find_raw_acs_file(table_id: str) -> Path:
    table_id_lower = table_id.lower()
    matches = []
    for raw_dir in RAW_DIR_CANDIDATES:
        if not raw_dir.exists():
            continue
        for path in raw_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".csv":
                continue
            if table_id_lower in path.name.lower():
                matches.append(path)
    if not matches:
        searched = "\n".join(str(path) for path in RAW_DIR_CANDIDATES)
        raise FileNotFoundError(f"Missing required ACS {table_id} CSV. Searched under:\n{searched}")
    return sorted(matches, key=lambda path: ("mapping" in path.name.lower(), "metadata" in path.name.lower(), len(path.name), path.name.lower()))[0]


def normalize_geoid(value) -> str | None:
    if pd.isna(value):
        return None
    digits = re.sub(r"\D", "", str(value).strip())
    if not digits:
        return None
    return digits[-11:].zfill(11)


def make_geoid(df: pd.DataFrame) -> pd.Series:
    geoid_col = find_column(df, ["GEOID", "GEO_ID", "geoid", "GEOID20"])
    if geoid_col:
        return df[geoid_col].map(normalize_geoid)

    lower_lookup = {str(col).lower(): col for col in df.columns}
    if {"state", "county", "tract"}.issubset(lower_lookup):
        state = df[lower_lookup["state"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
        county = df[lower_lookup["county"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
        tract = df[lower_lookup["tract"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        return state + county + tract

    raise KeyError(f"Could not create GEOID from columns: {list(df.columns)}")


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return (num / den).where(den.notna() & (den != 0))


def pct(num: pd.Series, den: pd.Series) -> pd.Series:
    return safe_div(num, den) * 100


def read_acs(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required ACS file: {path}")
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df["GEOID"] = make_geoid(df)
    id_cols = {column for column in df.columns if column in ID_COLUMNS}
    id_cols.add("GEOID")
    for column in df.columns:
        if column not in id_cols:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = clean_census_values(df)
    df = df.loc[df["GEOID"].notna()].copy()
    df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
    return df


def select_existing_columns(frame: pd.DataFrame, candidates: list[str]) -> list[str]:
    return [column for column in candidates if column in frame.columns]


def build_b16001_metrics() -> pd.DataFrame:
    b16001_path = find_raw_acs_file("B16001")
    print(f"Using B16001 person-level language file: {b16001_path}")
    b16001 = read_acs(b16001_path)
    total_col = find_column(b16001, ["B16001_001E"])
    if total_col is None:
        raise KeyError(f"B16001 total population age 5+ column not found in {b16001_path.name}.")

    output = b16001[["GEOID"]].drop_duplicates().copy()
    output["b16001_population_age_5_plus"] = pd.to_numeric(b16001[total_col], errors="coerce")

    for group_name, columns in B16001_LANGUAGE_GROUPS.items():
        speaker_col = find_column(b16001, [columns["speaker_col"]])
        very_well_col = find_column(b16001, [columns["very_well_col"]])
        lep_col = find_column(b16001, [columns["lep_col"]])
        if speaker_col is None or lep_col is None:
            raise KeyError(f"Missing B16001 columns for {group_name}: {columns}")

        output[f"{group_name}_speaker_population_age_5_plus"] = pd.to_numeric(b16001[speaker_col], errors="coerce")
        output[f"{group_name}_limited_english_population_age_5_plus"] = pd.to_numeric(b16001[lep_col], errors="coerce")
        if very_well_col:
            output[f"{group_name}_speaks_english_very_well_population_age_5_plus"] = pd.to_numeric(b16001[very_well_col], errors="coerce")
        output[f"{group_name}_limited_english_person_share"] = pct(output[f"{group_name}_limited_english_population_age_5_plus"], output["b16001_population_age_5_plus"])
        output[f"{group_name}_limited_english_within_speakers_share"] = pct(output[f"{group_name}_limited_english_population_age_5_plus"], output[f"{group_name}_speaker_population_age_5_plus"])

    output["other_speaker_population_age_5_plus"] = output[[f"{g}_speaker_population_age_5_plus" for g in OTHER_LANGUAGE_COMPONENTS]].sum(axis=1, min_count=1)
    output["other_limited_english_population_age_5_plus"] = output[[f"{g}_limited_english_population_age_5_plus" for g in OTHER_LANGUAGE_COMPONENTS]].sum(axis=1, min_count=1)
    output["other_limited_english_person_share"] = pct(output["other_limited_english_population_age_5_plus"], output["b16001_population_age_5_plus"])
    output["other_limited_english_within_speakers_share"] = pct(output["other_limited_english_population_age_5_plus"], output["other_speaker_population_age_5_plus"])
    return output


def build_c16002_context() -> pd.DataFrame:
    try:
        c16002_path = find_raw_acs_file("C16002")
    except FileNotFoundError:
        print("Warning: C16002 not found. lep_household_share will be blank.")
        return pd.DataFrame(columns=["GEOID", "households_language_universe", "lep_households", "lep_household_share"])

    print(f"Using C16002 household LEP context file: {c16002_path}")
    c16002 = read_acs(c16002_path)
    total_col = find_column(c16002, ["C16002_001E"])
    limited_cols = [find_column(c16002, [col]) for col in ["C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"]]
    limited_cols = [column for column in limited_cols if column is not None]
    if total_col is None or not limited_cols:
        print("Warning: C16002 columns not recognized. lep_household_share will be blank.")
        return pd.DataFrame(columns=["GEOID", "households_language_universe", "lep_households", "lep_household_share"])

    output = c16002[["GEOID"]].drop_duplicates().copy()
    output["households_language_universe"] = pd.to_numeric(c16002[total_col], errors="coerce")
    output["lep_households"] = c16002[limited_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
    output["lep_household_share"] = pct(output["lep_households"], output["households_language_universe"])
    return output


def load_optional_baseline() -> pd.DataFrame:
    for path in BASELINE_CANDIDATES:
        if path.exists():
            print(f"Using demographic baseline: {path}")
            baseline = pd.read_csv(path, dtype={"GEOID": str}, low_memory=False)
            baseline["GEOID"] = baseline["GEOID"].map(normalize_geoid)
            return baseline.dropna(subset=["GEOID"]).drop_duplicates("GEOID")
    print("Warning: no demographic baseline file found. Only language-created context columns will be available.")
    return pd.DataFrame(columns=["GEOID"])


def add_geometry_and_centroids(clean: pd.DataFrame) -> pd.DataFrame:
    _, cb3_tract_geometry, _, _ = load_cb3_tract_universe(PROJECT_DIR)
    geometry = cb3_tract_geometry[["GEOID", "geometry"]].copy()
    geometry["GEOID"] = geometry["GEOID"].map(normalize_geoid)
    geometry = geometry.dropna(subset=["GEOID"]).drop_duplicates("GEOID")
    geometry = gpd.GeoDataFrame(geometry, geometry="geometry", crs=cb3_tract_geometry.crs).to_crs("EPSG:4326")
    centroids = geometry.to_crs("EPSG:2263").copy()
    centroids["geometry"] = centroids.geometry.representative_point()
    centroids = centroids.to_crs("EPSG:4326")
    geometry["tract_centroid_latitude"] = centroids.geometry.y
    geometry["tract_centroid_longitude"] = centroids.geometry.x
    geometry["geometry_wkt"] = geometry.geometry.to_wkt()
    return clean.merge(pd.DataFrame(geometry.drop(columns="geometry")), on="GEOID", how="left", validate="one_to_one")


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    tracts, _, _, _ = load_cb3_tract_universe(PROJECT_DIR)
    base_columns = ["GEOID", "tract_label", "tract_name", "nta_code", "nta_name", "cdta_code", "cdta_name"]
    clean = tracts[select_existing_columns(tracts, base_columns)].copy()
    clean["GEOID"] = clean["GEOID"].map(normalize_geoid)

    clean = clean.merge(build_b16001_metrics(), on="GEOID", how="left", validate="one_to_one")
    clean = clean.merge(build_c16002_context(), on="GEOID", how="left", validate="one_to_one")

    baseline = load_optional_baseline()
    baseline_columns = [
        "GEOID", "median_household_income", "age_0_to_19_share", "age_20_to_64_share", "age_65_plus_share",
        "white_non_hispanic_share", "black_non_hispanic_share", "asian_non_hispanic_share", "hispanic_share", "poverty_rate",
    ]
    available_baseline_columns = select_existing_columns(baseline, baseline_columns)
    if available_baseline_columns != ["GEOID"] and "GEOID" in available_baseline_columns:
        clean = clean.merge(baseline[available_baseline_columns], on="GEOID", how="left", validate="one_to_one")

    clean = add_geometry_and_centroids(clean).sort_values("GEOID").reset_index(drop=True)
    assert len(clean) == 31, f"Expected 31 CB3 tracts, got {len(clean)}"
    assert clean["GEOID"].is_unique, "GEOID is not unique."
    assert clean["geometry_wkt"].notna().all(), "Missing geometry_wkt values."
    clean.to_csv(OUTPUT_PATH, index=False)

    mapped_metrics = ["spanish_limited_english_person_share", "chinese_limited_english_person_share", "other_limited_english_person_share"]
    log_lines = [
        "CB3 Language — Build Log", f"Output: {OUTPUT_PATH}", "", "Mapped B16001 person-level metrics:",
        *[f"- {m}: {clean[m].notna().sum()} tracts with data" for m in mapped_metrics if m in clean.columns],
        "", "Other language group collapse:", "- Tagalog", "- Korean", "- French/Haitian/Cajun", "- Arabic", "- Russian/Slavic", "- Vietnamese", "",
        "C16002 household-level metric kept as demographic/context layer:",
        f"- lep_household_share: {clean['lep_household_share'].notna().sum() if 'lep_household_share' in clean.columns else 0} tracts with data",
    ]
    LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")
    print(f"Wrote build log to {LOG_PATH}")
    print("\nMapped metrics:")
    for metric in mapped_metrics:
        if metric in clean.columns:
            print(f"  {metric}: {clean[metric].notna().sum()} tracts with data")


if __name__ == "__main__":
    main()
