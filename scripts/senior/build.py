"""Build Senior domain clean files from raw source files.

Outputs
-------
data/clean/senior.csv
data/clean/senior_facilities.csv
data/clean/senior_pedestrian_crashes.csv
data/clean/senior_top_intersections.csv
data/clean/senior_build_log.txt

This script intentionally keeps mapping logic out of build.py. It prepares
map-ready clean files with GEOID, tract labels, centroids, and geometry_wkt so
scripts/senior/maps.qmd can render maps with src/map_utils.py.
"""

from pathlib import Path
import csv
import re
import sys

import geopandas as gpd
import numpy as np
import pandas as pd


def find_project_dir(required_dirs=("data", "docs", "src")):
    """Find project root by walking upward from current file/cwd."""
    start_points = [Path.cwd(), Path(__file__).resolve()]
    for start in start_points:
        for candidate in [start, *start.parents]:
            if all((candidate / directory).exists() for directory in required_dirs):
                return candidate
    required = ", ".join(required_dirs)
    raise FileNotFoundError(f"Could not find project root containing: {required}")


PROJECT_DIR = find_project_dir()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.cb3_utils import (  # noqa: E402
    add_acs_geoid,
    add_polygon_centroids,
    assign_points_to_cb3_tract,
    clean_census_values,
    load_cb3_tract_universe,
)

RAW = PROJECT_DIR / "data" / "raw" / "Seniors"
CLEAN = PROJECT_DIR / "data" / "clean"

SENIOR_OUTPUT = CLEAN / "senior.csv"
FACILITIES_OUTPUT = CLEAN / "senior_facilities.csv"
CRASHES_OUTPUT = CLEAN / "senior_pedestrian_crashes.csv"
TOP_INTERSECTIONS_OUTPUT = CLEAN / "senior_top_intersections.csv"
LOG_OUTPUT = CLEAN / "senior_build_log.txt"


DEMOGRAPHIC_SOURCE_CANDIDATES = [
    CLEAN / "tract_baseline_profile_clean.csv",
    CLEAN / "tract_baseline_profile_partial_before_crosswalk.csv",
    CLEAN / "tract_baseline_profile_partial_before_crosswalk_clean.csv",
    CLEAN / "cb3_tract_baseline_profile.csv",
    CLEAN / "cb3_tract_baseline_profile_clean.csv",
    CLEAN / "language.csv",
    CLEAN / "economic-food.csv",
    CLEAN / "health.csv",
]

DEMOGRAPHIC_COLUMNS = [
    "median_household_income",
    "age_0_to_19_share",
    "age_20_to_64_share",
    "age_65_plus_share",
    "white_non_hispanic_share",
    "black_non_hispanic_share",
    "asian_non_hispanic_share",
    "hispanic_share",
    "poverty_rate",
    "lep_household_share",
]

RACE_SPECS = {
    "asian": "Asian",
    "black": "Black",
    "hispanic": "Hispanic",
    "white_nh": "White non-Hispanic",
}

# ACS B17020 race suffixes used by Kailey's R memo.
RACE_TABLE_LETTERS = {
    "white": "A",
    "black": "B",
    "aian": "C",
    "asian": "D",
    "nhpi": "E",
    "other": "F",
    "two_or_more": "G",
    "white_nh": "H",
    "hispanic": "I",
}

# Memo/R file used 60+ poverty cells from B17020 race tables:
# below poverty = 007, 008, 009; at/above poverty = 015, 016, 017.
POVERTY_BELOW_CODES = ["007", "008", "009"]
POVERTY_ABOVE_CODES = ["015", "016", "017"]

# Memo/R file used age 65+ B16004 cells, including "well", "not well",
# and "not at all" for non-English language groups.
SENIOR_LEP_COLUMNS = [
    "B16004_050E", "B16004_051E", "B16004_052E",  # Spanish
    "B16004_055E", "B16004_056E", "B16004_057E",  # Other Indo-European
    "B16004_060E", "B16004_061E", "B16004_062E",  # Asian/Pacific Island
    "B16004_065E", "B16004_066E", "B16004_067E",  # Other languages
]

SENIOR_LEP_LANGUAGE_SPECS = {
    "spanish": {
        "label": "Spanish",
        "columns": ["B16004_050E", "B16004_051E", "B16004_052E"],
    },
    "other_indo_european": {
        "label": "Other Indo-European languages",
        "columns": ["B16004_055E", "B16004_056E", "B16004_057E"],
    },
    "asian_pacific": {
        "label": "Asian and Pacific Island languages",
        "columns": ["B16004_060E", "B16004_061E", "B16004_062E"],
    },
    "other_languages": {
        "label": "Other languages",
        "columns": ["B16004_065E", "B16004_066E", "B16004_067E"],
    },
}


def _log(lines, message):
    print(message)
    lines.append(message)


def _normalise_name(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def resolve_file(patterns, required=True):
    """Find the first raw Senior file whose normalized name contains a pattern."""
    if not RAW.exists():
        if required:
            raise FileNotFoundError(f"Missing raw Seniors folder: {RAW}")
        return None

    files = [path for path in RAW.iterdir() if path.is_file()]
    normalized = {path: _normalise_name(path.name) for path in files}

    for pattern in patterns:
        pattern_norm = _normalise_name(pattern)
        matches = [path for path, name in normalized.items() if pattern_norm in name]
        if matches:
            # Prefer the newest dated filename if multiple versions exist.
            return sorted(matches, key=lambda path: path.name)[-1]

    if required:
        raise FileNotFoundError(
            "Could not find raw Senior file matching any of: "
            + ", ".join(patterns)
        )
    return None


def as_numeric(frame, columns):
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def percent(numerator, denominator):
    denominator = denominator.replace(0, np.nan)
    return numerator.div(denominator).mul(100)


def standardize_tract_to_geoid(value):
    """Convert tract values like 201, 2.01, or 00201 to NYC 11-char GEOID."""
    if pd.isna(value):
        return pd.NA

    text = str(value).strip().replace(".0", "")
    text = re.sub(r"[^0-9]", "", text)

    if not text:
        return pd.NA

    # Already a GEOID.
    if len(text) == 11 and text.startswith("36061"):
        return text

    # ACS/R memo tract codes are usually 201, 600, 1001, 2902, etc.
    return "36061" + text.zfill(6)


def attach_geometry_and_centroids(frame, cb3_tract_geometry):
    result = frame.copy()
    result = add_polygon_centroids(
        result,
        cb3_tract_geometry,
        id_column="GEOID",
        lat_col="tract_centroid_latitude",
        lon_col="tract_centroid_longitude",
        validate="one_to_one",
    )
    result = result.merge(
        cb3_tract_geometry.assign(
            geometry_wkt=cb3_tract_geometry.geometry.to_wkt()
        )[["GEOID", "geometry_wkt"]],
        on="GEOID",
        how="left",
        validate="one_to_one",
    )
    return result


def build_tract_base(tracts, cb3_tract_geometry):
    base = tracts[["GEOID", "tract", "tract_label", "nta_code", "nta_name", "cdta_code", "cdta_name"]].copy()
    return attach_geometry_and_centroids(base, cb3_tract_geometry)


def build_senior_lep_and_population(CB3_TRACT_CODES, log_lines):
    path = resolve_file(["acs_5yr_2024_B16004", "B16004"])
    _log(log_lines, f"Reading senior LEP / senior population source: {path.relative_to(PROJECT_DIR)}")

    frame = clean_census_values(pd.read_csv(path, low_memory=False))
    frame = frame.query("state == 36 and county == 61").copy()
    frame["tract"] = pd.to_numeric(frame["tract"], errors="coerce").astype("Int64")
    frame = frame[frame["tract"].isin(CB3_TRACT_CODES)].copy()
    frame = add_acs_geoid(frame)

    needed = ["B16004_001E", "B16004_046E"] + SENIOR_LEP_COLUMNS
    frame = as_numeric(frame, needed)

    # If a source is missing one of the LEP components, treat it as zero and
    # log it, rather than failing the whole domain.
    for column in SENIOR_LEP_COLUMNS:
        if column not in frame.columns:
            _log(log_lines, f"Warning: missing {column}; senior LEP component set to 0.")
            frame[column] = 0

    result = frame[["GEOID"]].copy()
    result["senior_population_65plus"] = frame.get("B16004_046E")
    result["senior_population_share"] = percent(
        result["senior_population_65plus"],
        frame.get("B16004_001E"),
    )
    result["senior_lep_count_65plus"] = frame[SENIOR_LEP_COLUMNS].sum(axis=1, min_count=1)
    result["senior_lep_rate_65plus"] = percent(
        result["senior_lep_count_65plus"],
        result["senior_population_65plus"],
    )

    for language_key, spec in SENIOR_LEP_LANGUAGE_SPECS.items():
        count_col = f"{language_key}_senior_lep_count_65plus"
        rate_col = f"{language_key}_senior_lep_rate_65plus"
        result[count_col] = frame[spec["columns"]].sum(axis=1, min_count=1)
        result[rate_col] = percent(
            result[count_col],
            result["senior_population_65plus"],
        )

    return result


def build_senior_poverty_by_race(CB3_TRACT_CODES, log_lines):
    path = resolve_file(["acs_5yr_2024_B17020", "B17020"])
    _log(log_lines, f"Reading senior poverty-by-race source: {path.relative_to(PROJECT_DIR)}")

    frame = clean_census_values(pd.read_csv(path, low_memory=False))
    frame = frame.query("state == 36 and county == 61").copy()
    frame["tract"] = pd.to_numeric(frame["tract"], errors="coerce").astype("Int64")
    frame = frame[frame["tract"].isin(CB3_TRACT_CODES)].copy()
    frame = add_acs_geoid(frame)

    result = frame[["GEOID"]].copy()

    for race, letter in RACE_TABLE_LETTERS.items():
        below_cols = [f"B17020{letter}_{code}E" for code in POVERTY_BELOW_CODES]
        total_cols = below_cols + [f"B17020{letter}_{code}E" for code in POVERTY_ABOVE_CODES]

        for column in total_cols:
            if column not in frame.columns:
                frame[column] = np.nan
                _log(log_lines, f"Warning: missing {column}; poverty component set to NA.")

        below = frame[below_cols].sum(axis=1, min_count=1)
        total = frame[total_cols].sum(axis=1, min_count=1)

        result[f"{race}_senior_poverty_below_60plus"] = below
        result[f"{race}_senior_population_60plus_poverty_universe"] = total
        result[f"{race}_senior_poverty_rate_60plus"] = percent(below, total)

    return result


def parse_walkup_from_clean_tract_rates(log_lines):
    """Fallback parser for Kailey's tract-rates CSV, whose geometry column is malformed.

    The raw memo source for walk-ups is MapPLUTO + HPD BIS elevator records.
    Those files were not among the uploaded raw files, so this parser only
    recovers the already-created tract-level walk-up/elevator values if
    SENIOR_DOMAIN_senior_tract_rates.csv is present.
    """
    path = resolve_file(["SENIOR_DOMAIN_senior_tract_rates"], required=False)
    if path is None:
        _log(log_lines, "Walk-up fallback not available: SENIOR_DOMAIN_senior_tract_rates.csv not found.")
        return pd.DataFrame(columns=[
            "GEOID",
            "total_mltfam_bldgs_3plus",
            "mltfam_bldgs_with_elevator",
            "mltfam_elevator_rate",
            "mltfam_walkup_rate",
        ])

    _log(log_lines, f"Reading walk-up/elevator fallback source: {path.relative_to(PROJECT_DIR)}")

    lines = path.read_text(errors="replace").splitlines()
    if not lines:
        return pd.DataFrame()

    header = next(csv.reader([lines[0]]))
    keep_cols = [
        "tract",
        "total_mltfam_bldgs_3plus",
        "mltfam_bldgs_with_elevator",
        "mltfam_elevator_rate",
    ]

    rows = []
    for line in lines[1:]:
        if not re.match(r'^"?[0-9]', line.strip()):
            continue

        prefix = line.split(",c(", 1)[0]
        try:
            values = next(csv.reader([prefix]))
        except Exception:
            continue

        if len(values) < 16:
            continue

        row = dict(zip(header[:len(values)], values))
        if not row.get("tract"):
            continue

        rows.append({column: row.get(column) for column in keep_cols})

    frame = pd.DataFrame(rows)
    if frame.empty:
        _log(log_lines, "Warning: no walk-up/elevator rows could be recovered from fallback tract file.")
        return frame

    frame["GEOID"] = frame["tract"].map(standardize_tract_to_geoid)
    frame = as_numeric(
        frame,
        ["total_mltfam_bldgs_3plus", "mltfam_bldgs_with_elevator", "mltfam_elevator_rate"],
    )
    frame["mltfam_walkup_rate"] = (1 - frame["mltfam_elevator_rate"]).mul(100)
    frame["mltfam_elevator_rate"] = frame["mltfam_elevator_rate"].mul(100)

    return frame[[
        "GEOID",
        "total_mltfam_bldgs_3plus",
        "mltfam_bldgs_with_elevator",
        "mltfam_elevator_rate",
        "mltfam_walkup_rate",
    ]].drop_duplicates("GEOID")


def build_facilities(cb3_tract_geometry, CB3_TRACT_CODES, log_lines):
    path = resolve_file([
        "List_of_NYC_Aging_Providers_with_Sites_Open_to_the_Public",
        "aging_providers",
        "senior_facilities",
    ])
    _log(log_lines, f"Reading senior service facilities source: {path.relative_to(PROJECT_DIR)}")

    frame = pd.read_csv(path, low_memory=False)
    frame.columns = [column.strip() for column in frame.columns]

    # Support both raw NYC Aging names and Kailey/R cleaned names.
    provider_col = "Provider Type" if "Provider Type" in frame.columns else "Provider.Type"
    cd_col = "Community District" if "Community District" in frame.columns else "Community.District"
    lat_col = "Latitude"
    lon_col = "Longitude"

    frame = frame[
        frame[provider_col].isin([
            "Older Adult Center",
            "Naturally Occurring Retirement Community (NORC)",
        ])
    ].copy()

    frame[cd_col] = frame[cd_col].astype(str).str.replace(r"\.0$", "", regex=True)
    frame = frame[frame[cd_col].eq("103")].copy()

    frame["latitude"] = pd.to_numeric(frame[lat_col], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame[lon_col], errors="coerce")
    frame = frame.dropna(subset=["latitude", "longitude"]).copy()

    frame["facility_type"] = np.where(
        frame[provider_col].eq("Older Adult Center"),
        "Older Adult Center",
        "NORC",
    )

    name_col = "Site Name" if "Site Name" in frame.columns else "Site.Name"
    address_col = "Site Address" if "Site Address" in frame.columns else "Site.Address"
    frame["facility_name"] = frame[name_col].astype(str)
    frame["facility_address"] = frame[address_col].astype(str) if address_col in frame.columns else ""

    frame["GEOID"] = assign_points_to_cb3_tract(
        frame,
        longitude_column="longitude",
        latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry,
        CB3_TRACT_CODES=CB3_TRACT_CODES,
        source_tract_column=None,
    )
    frame = frame[frame["GEOID"].notna()].copy()

    output = frame[[
        "facility_name",
        "facility_type",
        "facility_address",
        "latitude",
        "longitude",
        "GEOID",
    ]].copy()

    _log(log_lines, f"Senior facilities retained in CB3: {len(output)}")
    return output


def choose_crashes_source(log_lines):
    matches = []
    for pattern in ["Motor_Vehicle_Collisions_-_Crashes", "Motor Vehicle Collisions Crashes"]:
        try:
            path = resolve_file([pattern], required=False)
        except Exception:
            path = None
        if path is not None:
            matches.append(path)

    # Include all crash files matching normalized phrase, then prefer latest filename.
    if RAW.exists():
        for path in RAW.iterdir():
            if path.is_file() and "motor_vehicle_collisions_crashes" in _normalise_name(path.name):
                matches.append(path)

    matches = sorted(set(matches), key=lambda path: path.name)
    if not matches:
        raise FileNotFoundError("Missing raw Vision Zero crashes file.")

    selected = matches[-1]
    _log(log_lines, f"Reading Vision Zero crashes source: {selected.relative_to(PROJECT_DIR)}")
    return selected


def build_senior_pedestrian_crashes(cb3_tract_geometry, CB3_TRACT_CODES, log_lines):
    crashes_path = choose_crashes_source(log_lines)
    persons_path = resolve_file(["Motor_Vehicle_Collisions_-_Person", "Motor Vehicle Collisions Person"])
    _log(log_lines, f"Reading Vision Zero person source: {persons_path.relative_to(PROJECT_DIR)}")

    person_usecols = [
        "COLLISION_ID",
        "CRASH_DATE",
        "CRASH_TIME",
        "PERSON_TYPE",
        "PERSON_INJURY",
        "PERSON_AGE",
        "PED_LOCATION",
        "PED_ACTION",
        "COMPLAINT",
        "PED_ROLE",
        "PERSON_SEX",
    ]
    persons = pd.read_csv(persons_path, usecols=lambda c: c in person_usecols, low_memory=False)
    persons["PERSON_AGE"] = pd.to_numeric(persons["PERSON_AGE"], errors="coerce")
    senior_peds = persons[
        persons["PERSON_TYPE"].eq("Pedestrian")
        & persons["PERSON_AGE"].ge(65)
    ].copy()

    if senior_peds.empty:
        _log(log_lines, "Warning: no senior pedestrian person records found.")
        return pd.DataFrame(), pd.DataFrame()

    crash_usecols = [
        "COLLISION_ID",
        "CRASH DATE",
        "CRASH TIME",
        "BOROUGH",
        "ZIP CODE",
        "LATITUDE",
        "LONGITUDE",
        "ON STREET NAME",
        "CROSS STREET NAME",
        "OFF STREET NAME",
        "NUMBER OF PEDESTRIANS INJURED",
        "NUMBER OF PEDESTRIANS KILLED",
    ]
    crashes = pd.read_csv(crashes_path, usecols=lambda c: c in crash_usecols, low_memory=False)

    crashes["CRASH DATE"] = pd.to_datetime(crashes["CRASH DATE"], errors="coerce")
    crashes["LATITUDE"] = pd.to_numeric(crashes["LATITUDE"], errors="coerce")
    crashes["LONGITUDE"] = pd.to_numeric(crashes["LONGITUDE"], errors="coerce")
    crashes = crashes[
        crashes["CRASH DATE"].between(pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31"))
        & crashes["LATITUDE"].notna()
        & crashes["LONGITUDE"].notna()
    ].copy()

    joined = senior_peds.merge(crashes, on="COLLISION_ID", how="inner", suffixes=("_person", "_crash"))

    if joined.empty:
        _log(log_lines, "Warning: senior pedestrian records did not match 2024-2025 crash rows.")
        return pd.DataFrame(), pd.DataFrame()

    joined["GEOID"] = assign_points_to_cb3_tract(
        joined,
        longitude_column="LONGITUDE",
        latitude_column="LATITUDE",
        cb3_tract_geometry=cb3_tract_geometry,
        CB3_TRACT_CODES=CB3_TRACT_CODES,
        source_tract_column=None,
    )
    joined = joined[joined["GEOID"].notna()].copy()

    joined["fatality"] = joined["PERSON_INJURY"].astype(str).str.lower().eq("killed")
    joined["incident_type"] = np.where(joined["fatality"], "Senior pedestrian fatality", "Senior pedestrian injury/incident")
    joined["crash_date"] = joined["CRASH DATE"].dt.date.astype(str)
    joined["latitude"] = joined["LATITUDE"]
    joined["longitude"] = joined["LONGITUDE"]
    joined["intersection"] = (
        joined.get("ON STREET NAME", "").fillna("").astype(str).str.strip()
        + " / "
        + joined.get("CROSS STREET NAME", "").fillna("").astype(str).str.strip()
    )
    joined["intersection"] = joined["intersection"].str.replace(r"^ / | / $", "", regex=True)
    joined.loc[joined["intersection"].eq(""), "intersection"] = joined.get("OFF STREET NAME", "").fillna("Unknown location")

    crash_output = joined[[
        "COLLISION_ID",
        "crash_date",
        "CRASH_TIME",
        "PERSON_AGE",
        "PERSON_INJURY",
        "fatality",
        "incident_type",
        "intersection",
        "latitude",
        "longitude",
        "GEOID",
    ]].copy()

    top = (
        crash_output
        .groupby("intersection", dropna=False)
        .agg(
            senior_pedestrian_incidents=("COLLISION_ID", "count"),
            senior_pedestrian_fatalities=("fatality", "sum"),
            latitude=("latitude", "mean"),
            longitude=("longitude", "mean"),
        )
        .reset_index()
        .sort_values(
            ["senior_pedestrian_incidents", "senior_pedestrian_fatalities"],
            ascending=False,
        )
        .head(5)
        .copy()
    )
    if not top.empty:
        top["point_type"] = "Top senior pedestrian crash intersection"
        top["label"] = (
            top["intersection"].astype(str)
            + " — "
            + top["senior_pedestrian_incidents"].astype(int).astype(str)
            + " senior pedestrian incidents"
        )

    _log(
        log_lines,
        "Senior pedestrian crashes retained in CB3, 2024-2025: "
        f"{len(crash_output)}; fatalities: {int(crash_output['fatality'].sum()) if not crash_output.empty else 0}",
    )
    return crash_output, top



def load_demographic_context(log_lines):
    """Load shared demographic context columns for map backdrop layers.

    Senior-domain raw files create senior-specific metrics. The broader map
    backdrop layers used across Health/Economic-Food/Language usually come
    from a shared tract baseline or another domain clean output. This function
    adds those columns to data/clean/senior.csv when a shared clean source is
    already available, without making maps.qmd read or merge extra files.
    """
    for path in DEMOGRAPHIC_SOURCE_CANDIDATES:
        if not path.exists():
            continue

        try:
            frame = pd.read_csv(path, dtype={"GEOID": str}, low_memory=False)
        except Exception as exc:
            _log(log_lines, f"Warning: could not read demographic source {path.relative_to(PROJECT_DIR)}: {exc}")
            continue

        if "GEOID" not in frame.columns:
            continue

        frame["GEOID"] = frame["GEOID"].astype(str).str.zfill(11)
        available = [column for column in DEMOGRAPHIC_COLUMNS if column in frame.columns]
        if not available:
            continue

        context = frame[["GEOID"] + available].drop_duplicates("GEOID").copy()
        for column in available:
            context[column] = pd.to_numeric(context[column], errors="coerce")

        _log(
            log_lines,
            "Loaded shared demographic map backdrop source: "
            f"{path.relative_to(PROJECT_DIR)} with columns: {', '.join(available)}",
        )
        return context

    _log(
        log_lines,
        "Warning: no shared demographic backdrop source found. Senior maps will "
        "still use senior_population_share as their default backdrop, but broader "
        "demographic layers such as income, race/ethnicity, poverty, and LEP will be unavailable.",
    )
    return pd.DataFrame(columns=["GEOID"])

def main():
    log_lines = []
    CLEAN.mkdir(parents=True, exist_ok=True)

    _log(log_lines, f"Project directory: {PROJECT_DIR}")
    tracts, cb3_tract_geometry, CB3_TRACT_CODES, _ = load_cb3_tract_universe(PROJECT_DIR)

    senior = build_tract_base(tracts, cb3_tract_geometry)

    senior_population = build_senior_lep_and_population(CB3_TRACT_CODES, log_lines)
    senior_poverty = build_senior_poverty_by_race(CB3_TRACT_CODES, log_lines)
    walkup = parse_walkup_from_clean_tract_rates(log_lines)
    demographic_context = load_demographic_context(log_lines)

    for frame, name in [
        (senior_population, "senior population / LEP"),
        (senior_poverty, "senior poverty by race"),
        (walkup, "walk-up/elevator fallback"),
        (demographic_context, "shared demographic map backdrop"),
    ]:
        if not frame.empty:
            overlap = [column for column in frame.columns if column != "GEOID" and column in senior.columns]
            if overlap:
                frame = frame.drop(columns=overlap)
                _log(log_lines, f"Skipped duplicate columns from {name}: {', '.join(overlap)}")
            if len(frame.columns) > 1:
                senior = senior.merge(frame, on="GEOID", how="left", validate="one_to_one")
                _log(log_lines, f"Merged {name}: {len(frame)} rows")
            else:
                _log(log_lines, f"Skipped {name}: no new columns to merge")
        else:
            _log(log_lines, f"Skipped empty {name} table")

    facilities = build_facilities(cb3_tract_geometry, CB3_TRACT_CODES, log_lines)
    crashes, top_intersections = build_senior_pedestrian_crashes(
        cb3_tract_geometry,
        CB3_TRACT_CODES,
        log_lines,
    )

    senior.to_csv(SENIOR_OUTPUT, index=False)
    facilities.to_csv(FACILITIES_OUTPUT, index=False)
    crashes.to_csv(CRASHES_OUTPUT, index=False)
    top_intersections.to_csv(TOP_INTERSECTIONS_OUTPUT, index=False)

    missing_housing_sources = [
        "MapPLUTO raw lot file",
        "HPD BIS elevator inspection records",
        "Furman SHD senior-targeted unit file",
        "HPD Local Law 44 senior-targeted affordable unit file",
    ]
    _log(log_lines, "Note: senior housing unit-location layer is not built because these memo sources are not present:")
    for item in missing_housing_sources:
        _log(log_lines, f"  - {item}")

    LOG_OUTPUT.write_text("\n".join(log_lines) + "\n")

    print("\nWrote Senior clean outputs:")
    for path in [SENIOR_OUTPUT, FACILITIES_OUTPUT, CRASHES_OUTPUT, TOP_INTERSECTIONS_OUTPUT, LOG_OUTPUT]:
        print(f"  {path.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
