from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.cb3_utils import add_polygon_centroids, load_cb3_tract_universe

RAW_DIR_CANDIDATES = [
    PROJECT_DIR / "data" / "raw" / "Economics and Food",
    PROJECT_DIR / "data" / "raw" / "Economic and Food",
]
RAW_DIR = next((path for path in RAW_DIR_CANDIDATES if path.exists()), RAW_DIR_CANDIDATES[0])
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PATH = CLEAN_DIR / "economic-food.csv"
LOG_PATH = CLEAN_DIR / "economic_food_log.txt"
PROVIDER_OUTPUT_PATH = CLEAN_DIR / "emergency_food_providers.csv"

POVERTY_FILE = "acs_5yr_2024_B17020.csv"
UNEMPLOYMENT_FILE = "acs_5yr_2024_B23025.csv"
INCOME_FILE = "acs_5yr_2024_B19013.csv"
EARNINGS_FILE = "acs_5yr_2024_B20002.csv"

PLACES_FILE = "PLACES__Census_Tract_Data_GIS_Friendly_Format_2025_release_20260504.csv"
FOOD_GAP_FILE = "Emergency_Food_Supply_Gap_20260420.csv"
DRS_FILE_OPTIONS = [
    "District_Resource_Statement_DRS_20260420.csv",
    "District_Resource_Statement_(DRS)_20260420.csv",
]
PROVIDER_FILE = "Emergency_Food_Provider_Locations_20260420.csv"

BASELINE_CANDIDATES = [
    CLEAN_DIR / "tract_baseline_profile_clean.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk_clean.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile_clean.csv",
    CLEAN_DIR / "health_tract.csv",
    CLEAN_DIR / "health.csv",
]


def _log(lines, message):
    print(message)
    lines.append(str(message))


def _standardize_columns(frame):
    frame = frame.copy()
    frame.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")
        for col in frame.columns
    ]
    return frame


def _read_csv(path):
    return pd.read_csv(path, dtype=str, low_memory=False)


def _find_existing_file(filename, required=False):
    if not RAW_DIR.exists():
        if required:
            raise FileNotFoundError(f"Raw directory does not exist: {RAW_DIR}")
        return None

    exact = RAW_DIR / filename
    if exact.exists():
        return exact

    target = filename.lower()
    matches = [path for path in RAW_DIR.iterdir() if path.name.lower() == target]
    if matches:
        return matches[0]

    if required:
        available = sorted(path.name for path in RAW_DIR.iterdir())
        raise FileNotFoundError(
            f"Could not find required source file {filename} in {RAW_DIR}. "
            f"Available files: {available}"
        )

    return None


def _find_existing_file_any(filenames, required=False):
    for filename in filenames:
        path = _find_existing_file(filename, required=False)
        if path is not None:
            return path
    if required:
        available = sorted(path.name for path in RAW_DIR.iterdir()) if RAW_DIR.exists() else []
        raise FileNotFoundError(f"Could not find any of {filenames}. Available files: {available}")
    return None


def _find_col(frame, candidates):
    lower_lookup = {str(col).lower(): col for col in frame.columns}
    for candidate in candidates:
        key = str(candidate).lower()
        if key in lower_lookup:
            return lower_lookup[key]
    return None


def _find_col_contains(frame, required_terms, optional_terms=None):
    optional_terms = optional_terms or []
    for col in frame.columns:
        text = str(col).lower()
        if all(term.lower() in text for term in required_terms):
            if not optional_terms or any(term.lower() in text for term in optional_terms):
                return col
    return None


def _to_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
        errors="coerce",
    )


def _normalize_geoid(series):
    return (
        series.astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.extract(r"(\d+)")[0]
        .str.zfill(11)
    )


def _ensure_geoid(frame):
    frame = frame.copy()

    if "GEOID" in frame.columns:
        frame["GEOID"] = _normalize_geoid(frame["GEOID"])
        return frame

    if "geoid" in frame.columns:
        frame["GEOID"] = _normalize_geoid(frame["geoid"])
        return frame

    if "GEOID20" in frame.columns:
        frame["GEOID"] = _normalize_geoid(frame["GEOID20"])
        return frame

    lower = {str(col).lower(): col for col in frame.columns}

    for candidate in ["locationid", "location_id", "tractfips", "tract_fips", "tractfips20"]:
        if candidate in lower:
            frame["GEOID"] = _normalize_geoid(frame[lower[candidate]])
            return frame

    if {"state", "county", "tract"}.issubset(lower):
        frame["GEOID"] = (
            frame[lower["state"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
            + frame[lower["county"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
            + frame[lower["tract"]].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        )
        return frame

    raise KeyError(f"Could not create GEOID from columns: {list(frame.columns)}")


def _safe_pct(numerator, denominator):
    numerator = _to_numeric(numerator)
    denominator = _to_numeric(denominator)
    return np.where(denominator > 0, numerator / denominator * 100, np.nan)


def _safe_rate(numerator, denominator):
    numerator = _to_numeric(numerator)
    denominator = _to_numeric(denominator)
    return np.where(denominator > 0, numerator / denominator, np.nan)


def _collapse_to_geoid(frame, value_columns):
    keep = ["GEOID"] + [col for col in value_columns if col in frame.columns]
    out = frame[keep].copy()
    for col in keep:
        if col != "GEOID":
            out[col] = _to_numeric(out[col])
    return out.groupby("GEOID", as_index=False).first()


def build_poverty_metrics(log_lines):
    path = _find_existing_file(POVERTY_FILE, required=True)
    _log(log_lines, f"Using poverty source: {path}")
    poverty = _ensure_geoid(_read_csv(path))

    total_col = _find_col(poverty, ["B17020_001E", "b17020_001e"])
    below_col = _find_col(poverty, ["B17020_002E", "b17020_002e"])

    if not total_col or not below_col:
        raise KeyError(f"Could not find B17020_001E/B17020_002E. Columns: {list(poverty.columns)}")

    poverty["poverty_universe_population"] = _to_numeric(poverty[total_col])
    poverty["poverty_count"] = _to_numeric(poverty[below_col])
    poverty["poverty_rate_pct"] = _safe_pct(poverty["poverty_count"], poverty["poverty_universe_population"])

    return _collapse_to_geoid(poverty, ["poverty_universe_population", "poverty_count", "poverty_rate_pct"])


def build_unemployment_metrics(log_lines):
    path = _find_existing_file(UNEMPLOYMENT_FILE, required=False)
    if path is None:
        _log(log_lines, f"Skipping unemployment_rate_pct: missing {UNEMPLOYMENT_FILE}")
        return None

    _log(log_lines, f"Using unemployment source: {path}")
    unemployment = _ensure_geoid(_read_csv(path))

    labor_force_col = _find_col(unemployment, ["B23025_003E", "b23025_003e"])
    unemployed_col = _find_col(unemployment, ["B23025_005E", "b23025_005e"])

    if not labor_force_col or not unemployed_col:
        _log(log_lines, "Skipping unemployment_rate_pct: missing B23025_003E or B23025_005E")
        return None

    unemployment["civilian_labor_force"] = _to_numeric(unemployment[labor_force_col])
    unemployment["unemployed_population"] = _to_numeric(unemployment[unemployed_col])
    unemployment["unemployment_rate_pct"] = _safe_pct(
        unemployment["unemployed_population"],
        unemployment["civilian_labor_force"],
    )

    return _collapse_to_geoid(unemployment, ["civilian_labor_force", "unemployed_population", "unemployment_rate_pct"])


def build_income_metrics(log_lines):
    path = _find_existing_file(INCOME_FILE, required=False)
    if path is None:
        _log(log_lines, f"Skipping median_household_income: missing {INCOME_FILE}")
        return None

    _log(log_lines, f"Using income source: {path}")
    income = _ensure_geoid(_read_csv(path))

    income_col = _find_col(income, ["B19013_001E", "b19013_001e"])
    if not income_col:
        _log(log_lines, "Skipping median_household_income: missing B19013_001E")
        return None

    income["median_household_income"] = _to_numeric(income[income_col])
    income.loc[income["median_household_income"] < 0, "median_household_income"] = np.nan

    return _collapse_to_geoid(income, ["median_household_income"])


def build_earnings_metrics(log_lines):
    path = _find_existing_file(EARNINGS_FILE, required=False)
    if path is None:
        _log(log_lines, f"Skipping median_earnings_all_workers: missing {EARNINGS_FILE}")
        return None

    _log(log_lines, f"Using earnings source: {path}")
    earnings = _ensure_geoid(_read_csv(path))

    earnings_col = _find_col(earnings, ["B20002_001E", "b20002_001e"])
    if not earnings_col:
        _log(log_lines, "Skipping median_earnings_all_workers: missing B20002_001E")
        return None

    earnings["median_earnings_all_workers"] = _to_numeric(earnings[earnings_col])
    earnings.loc[earnings["median_earnings_all_workers"] < 0, "median_earnings_all_workers"] = np.nan

    return _collapse_to_geoid(earnings, ["median_earnings_all_workers"])


def build_places_metrics(log_lines):
    path = _find_existing_file(PLACES_FILE, required=False)
    if path is None:
        _log(log_lines, f"Skipping PLACES food insecurity / utility shutoff metrics: missing {PLACES_FILE}")
        return None

    _log(log_lines, f"Using combined PLACES source: {path}")
    raw = _read_csv(path)
    places = _standardize_columns(raw)
    places = _ensure_geoid(places)

    food_col = _find_col(places, ["foodinsecu_crudeprev", "foodinsecu_crude_prev"])
    utility_col = _find_col(places, ["shututility_crudeprev", "shututility_crude_prev"])

    value_cols = []

    if food_col:
        places["food_insecurity_rate_pct"] = _to_numeric(places[food_col])
        value_cols.append("food_insecurity_rate_pct")
    else:
        _log(log_lines, "PLACES file found, but FOODINSECU_CrudePrev was not detected.")

    if utility_col:
        places["utility_shutoff_risk_pct"] = _to_numeric(places[utility_col])
        value_cols.append("utility_shutoff_risk_pct")
    else:
        _log(log_lines, "PLACES file found, but SHUTUTILITY_CrudePrev was not detected.")

    for col in value_cols:
        if places[col].max(skipna=True) <= 1:
            places[col] = places[col] * 100

    if not value_cols:
        _log(log_lines, f"Skipping PLACES metrics. Raw columns: {list(raw.columns)}")
        return None

    return _collapse_to_geoid(places, value_cols)


def add_baseline_demographics(log_lines, output):
    for path in BASELINE_CANDIDATES:
        if not path.exists():
            continue

        try:
            baseline = _ensure_geoid(pd.read_csv(path, dtype=str, low_memory=False))
        except Exception as exc:
            _log(log_lines, f"Skipping baseline candidate {path}: {exc}")
            continue

        demo_cols = [
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

        found = [col for col in demo_cols if col in baseline.columns]
        if not found:
            continue

        _log(log_lines, f"Adding demographic context from {path}: {found}")
        keep = ["GEOID"] + [col for col in found if col not in output.columns]
        if len(keep) == 1:
            return output

        baseline = baseline[keep].copy()
        for col in keep:
            if col != "GEOID":
                baseline[col] = _to_numeric(baseline[col])

        return output.merge(baseline, on="GEOID", how="left", validate="one_to_one")

    _log(log_lines, "No demographic baseline file found for age/race/LEP background layers.")
    return output


def build_food_gap_context(log_lines, base):
    path = _find_existing_file(FOOD_GAP_FILE, required=False)
    if path is None:
        _log(log_lines, f"Skipping food supply gap context: missing {FOOD_GAP_FILE}")
        return base

    _log(log_lines, f"Using food supply gap context source: {path}")
    food_gap = _standardize_columns(_read_csv(path))

    nta_code_col = (
        _find_col(food_gap, ["nta_code", "ntacode", "nta2020", "nta"])
        or _find_col_contains(food_gap, ["neighborhood", "tabulation", "area", "nta"])
    )
    nta_name_col = (
        _find_col(food_gap, ["nta_name", "ntaname", "neighborhood"])
        or _find_col_contains(food_gap, ["nta", "name"])
    )
    rank_col = _find_col_contains(food_gap, ["rank"])
    score_col = (
        _find_col(food_gap, ["weighted_score"])
        or _find_col_contains(food_gap, ["weighted", "score"])
        or _find_col_contains(food_gap, ["score"])
    )
    food_pct_col = _find_col(food_gap, ["food_insecure_percentage"]) or _find_col_contains(food_gap, ["food", "insecure", "percentage"])
    gap_lbs_col = _find_col(food_gap, ["supply_gap_lbs"]) or _find_col_contains(food_gap, ["supply", "gap"])

    rename_cols = {}
    if nta_code_col:
        rename_cols[nta_code_col] = "nta_code"
    if nta_name_col and nta_name_col != nta_code_col:
        rename_cols[nta_name_col] = "nta_name"
    if rank_col:
        rename_cols[rank_col] = "food_supply_gap_rank_nta"
    if score_col:
        rename_cols[score_col] = "food_supply_gap_score_nta"
    if food_pct_col:
        rename_cols[food_pct_col] = "food_supply_gap_food_insecure_pct_nta"
    if gap_lbs_col:
        rename_cols[gap_lbs_col] = "food_supply_gap_lbs_nta"

    food_gap = food_gap.rename(columns=rename_cols)

    keep = [
        col for col in [
            "nta_code",
            "nta_name",
            "food_supply_gap_rank_nta",
            "food_supply_gap_score_nta",
            "food_supply_gap_food_insecure_pct_nta",
            "food_supply_gap_lbs_nta",
        ]
        if col in food_gap.columns
    ]

    if not {"food_supply_gap_rank_nta", "food_supply_gap_score_nta", "food_supply_gap_food_insecure_pct_nta"}.intersection(keep):
        _log(log_lines, "Skipping food supply gap context: no rank/score/food insecurity context column found.")
        return base

    context = food_gap[keep].copy()
    out = base.copy()

    if "nta_code" in context.columns and "nta_code" in out.columns:
        context["nta_code"] = context["nta_code"].astype(str).str.strip()
        out["nta_code"] = out["nta_code"].astype(str).str.strip()
        out = out.merge(context.drop_duplicates("nta_code"), on="nta_code", how="left")
    elif "nta_name" in context.columns and "nta_name" in out.columns:
        context["nta_name"] = context["nta_name"].astype(str).str.strip().str.lower()
        out["_nta_name_join"] = out["nta_name"].astype(str).str.strip().str.lower()
        context = context.rename(columns={"nta_name": "_nta_name_join"})
        out = out.merge(context.drop_duplicates("_nta_name_join"), on="_nta_name_join", how="left")
        out = out.drop(columns=["_nta_name_join"], errors="ignore")
    else:
        _log(log_lines, "Skipping food supply gap context: no NTA join key found.")
        return base

    # Clean merge suffix if needed.
    if "nta_name_x" in out.columns and "nta_name" not in out.columns:
        out = out.rename(columns={"nta_name_x": "nta_name"})
    if "nta_name_y" in out.columns:
        out = out.drop(columns=["nta_name_y"])

    for col in [
        "food_supply_gap_rank_nta",
        "food_supply_gap_score_nta",
        "food_supply_gap_food_insecure_pct_nta",
        "food_supply_gap_lbs_nta",
    ]:
        if col in out.columns:
            out[col] = _to_numeric(out[col])

    return out


def build_safety_net_metric(log_lines, base):
    path = _find_existing_file_any(DRS_FILE_OPTIONS, required=False)
    if path is None:
        _log(log_lines, f"Skipping safety_net_enrollment_per_capita: missing DRS file")
        return base

    _log(log_lines, f"Using safety-net source: {path}")
    drs = _standardize_columns(_read_csv(path))

    medicaid_col = (
        _find_col_contains(drs, ["total", "medicaid", "enrollees"])
        or _find_col_contains(drs, ["medicaid"], ["enrollees", "recipients", "persons", "count"])
    )
    snap_col = (
        _find_col_contains(drs, ["snap", "recipients"])
        or _find_col_contains(drs, ["supplemental", "nutrition", "recipients"])
    )
    cash_col = (
        _find_col_contains(drs, ["cash", "assistance", "recipients"])
        or _find_col_contains(drs, ["public", "assistance"], ["recipients", "persons", "count"])
    )

    if any(col is None for col in [medicaid_col, snap_col, cash_col]):
        _log(log_lines, "Skipping safety_net_enrollment_per_capita: could not find Medicaid, SNAP, and Cash Assistance recipient columns.")
        _log(log_lines, f"DRS columns after cleanup: {list(drs.columns)}")
        return base

    drs["medicaid_enrollment"] = _to_numeric(drs[medicaid_col])
    drs["snap_enrollment"] = _to_numeric(drs[snap_col])
    drs["cash_assistance_enrollment"] = _to_numeric(drs[cash_col])
    drs["safety_net_enrollment"] = (
        drs["medicaid_enrollment"].fillna(0)
        + drs["snap_enrollment"].fillna(0)
        + drs["cash_assistance_enrollment"].fillna(0)
    )

    cd_col = _find_col(drs, ["community_district"])
    borough_col = _find_col(drs, ["borough"])

    cb3_row = None
    if cd_col and borough_col:
        cd_text = drs[cd_col].astype(str).str.lower()
        borough_text = drs[borough_col].astype(str).str.lower()
        mask = borough_text.str.contains("manhattan", na=False) & (
            cd_text.str.contains(r"\b3\b", regex=True, na=False)
            | cd_text.str.contains("03", na=False)
            | cd_text.str.contains("mn03", na=False)
        )
        if mask.any():
            cb3_row = drs.loc[mask].iloc[0]

    if cb3_row is None:
        nonnull = drs[drs["safety_net_enrollment"].notna()]
        if nonnull.empty:
            _log(log_lines, "Skipping safety_net_enrollment_per_capita: DRS recipient columns are all null.")
            return base
        cb3_row = nonnull.iloc[0]
        _log(log_lines, "DRS CB3 row was not uniquely detected; using first non-null DRS row as context.")

    out = base.copy()
    for col in ["medicaid_enrollment", "snap_enrollment", "cash_assistance_enrollment", "safety_net_enrollment"]:
        out[col] = cb3_row[col]

    if "poverty_universe_population" in out.columns:
        out["drs_population"] = out["poverty_universe_population"]
        out["safety_net_enrollment_per_capita"] = _safe_rate(out["safety_net_enrollment"], out["drs_population"])
    else:
        out["drs_population"] = np.nan
        out["safety_net_enrollment_per_capita"] = out["safety_net_enrollment"]

    return out


def _normalize_join_text(series):
    """Normalize text values used to join source NTAs to CB3 NTA geography."""
    return (
        series.astype("string")
        .str.strip()
        .str.casefold()
        .str.replace(r"\s+", " ", regex=True)
    )


def _build_nta_representative_points(tract_universe, cb3_tract_geometry):
    """Create one in-polygon representative point for each CB3 NTA."""
    nta_columns = [
        column
        for column in ["nta_code", "nta_name"]
        if column in tract_universe.columns
    ]
    if not nta_columns:
        raise KeyError(
            "CB3 tract universe has no nta_code or nta_name column for "
            "placing NTA-level emergency food providers."
        )

    attributes = tract_universe[["GEOID"] + nta_columns].copy()
    attributes["GEOID"] = _normalize_geoid(attributes["GEOID"])
    attributes = attributes.drop_duplicates("GEOID")

    geometry = cb3_tract_geometry[["GEOID", "geometry"]].copy()
    geometry["GEOID"] = _normalize_geoid(geometry["GEOID"])
    geometry = geometry.merge(
        attributes,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    join_column = "nta_code" if "nta_code" in geometry.columns else "nta_name"
    geometry["_nta_join"] = _normalize_join_text(geometry[join_column])
    geometry = geometry.dropna(subset=["_nta_join", "geometry"]).copy()

    nta_geometry = geometry[
        ["_nta_join"] + nta_columns + ["geometry"]
    ].dissolve(
        by="_nta_join",
        as_index=False,
        aggfunc="first",
    )

    projected = nta_geometry.to_crs("EPSG:2263")
    projected["geometry"] = projected.geometry.representative_point()
    points = projected.to_crs("EPSG:4326")

    points["latitude"] = points.geometry.y
    points["longitude"] = points.geometry.x

    return points[
        ["_nta_join"] + nta_columns + ["latitude", "longitude"]
    ].copy()


def _choose_nta_join(providers, nta_points):
    """Choose the source NTA column with the highest match to CB3 geography."""
    source_candidates = [
        column
        for column in [
            "nta_code",
            "ntacode",
            "nta2020",
            "nta_name",
            "ntaname",
            "nta",
            "neighborhood",
            "neighborhood_tabulation_area",
            "neighborhood_tabulation_area_nta_name",
        ]
        if column in providers.columns
    ]

    target_candidates = [
        column
        for column in ["nta_code", "nta_name"]
        if column in nta_points.columns
    ]

    best = None
    for source_column in source_candidates:
        source_values = _normalize_join_text(providers[source_column])
        for target_column in target_candidates:
            target_values = set(
                _normalize_join_text(nta_points[target_column])
                .dropna()
                .tolist()
            )
            match_count = int(source_values.isin(target_values).sum())
            candidate = (match_count, source_column, target_column)
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None or best[0] == 0:
        return None, None

    _, source_column, target_column = best
    return source_column, target_column


def build_provider_points(log_lines, tract_universe, cb3_tract_geometry):
    """Create map-ready emergency food provider points.

    Exact source coordinates are retained when available. When the provider
    source is only available at NTA level, one representative point is placed
    inside each matched NTA and the provider count is retained in the tooltip.
    """
    path = _find_existing_file(PROVIDER_FILE, required=False)
    if path is None:
        if PROVIDER_OUTPUT_PATH.exists():
            PROVIDER_OUTPUT_PATH.unlink()
            _log(
                log_lines,
                "Removed stale provider output because the raw provider "
                f"source is missing: {PROVIDER_OUTPUT_PATH}",
            )

        _log(
            log_lines,
            "Emergency food provider layer not built: "
            f"missing {PROVIDER_FILE}.",
        )
        return

    _log(log_lines, f"Using provider source: {path}")

    providers = _standardize_columns(_read_csv(path))
    if providers.empty:
        raise ValueError(f"Provider source is empty: {path}")

    lat_col = _find_col(providers, ["latitude", "lat"])
    lon_col = _find_col(providers, ["longitude", "lon", "lng", "long"])

    name_col = _find_col(
        providers,
        ["provider_name", "name", "organization", "agency", "program_name"],
    )
    address_col = _find_col(
        providers,
        ["address", "street_address", "location"],
    )
    subtype_col = _find_col(
        providers,
        ["provider_type", "type", "service_type", "program_type"],
    )
    count_col = (
        _find_col(
            providers,
            [
                "provider_count",
                "number_of_providers",
                "total_providers",
                "emergency_food_provider_count",
                "number_of_emergency_food_providers",
                "efp_count",
                "total_efp",
                "count",
            ],
        )
        or _find_col_contains(
            providers,
            ["provider"],
            ["count", "number", "total"],
        )
    )

    if count_col:
        providers["_provider_count"] = _to_numeric(
            providers[count_col]
        ).fillna(0)
    else:
        providers["_provider_count"] = 1

    output_parts = []

    if lat_col and lon_col:
        providers["_latitude"] = _to_numeric(providers[lat_col])
        providers["_longitude"] = _to_numeric(providers[lon_col])

        exact_mask = (
            providers["_latitude"].between(40.4, 41.1)
            & providers["_longitude"].between(-74.4, -73.5)
        )

        exact = providers.loc[exact_mask].copy()
        if not exact.empty:
            exact_output = pd.DataFrame(
                {
                    "provider_name": (
                        exact[name_col]
                        if name_col
                        else "Emergency food provider"
                    ),
                    "address": (
                        exact[address_col]
                        if address_col
                        else ""
                    ),
                    "provider_subtype": (
                        exact[subtype_col]
                        if subtype_col
                        else ""
                    ),
                    "provider_count": exact["_provider_count"],
                    "provider_type": "Emergency food provider",
                    "location_level": "Exact provider coordinates",
                    "latitude": exact["_latitude"],
                    "longitude": exact["_longitude"],
                }
            )

            source_nta_col = _find_col(
                exact,
                [
                    "nta_name",
                    "ntaname",
                    "nta",
                    "neighborhood",
                    "neighborhood_tabulation_area",
                    "neighborhood_tabulation_area_nta_name",
                ],
            )
            if source_nta_col:
                exact_output["nta_name"] = exact[source_nta_col]

            output_parts.append(exact_output)
            _log(
                log_lines,
                "Retained exact emergency food provider points: "
                f"{len(exact_output)}",
            )
    else:
        exact_mask = pd.Series(False, index=providers.index)

    missing_coordinate_rows = providers.loc[~exact_mask].copy()
    if not missing_coordinate_rows.empty:
        nta_points = _build_nta_representative_points(
            tract_universe,
            cb3_tract_geometry,
        )
        source_nta_col, target_nta_col = _choose_nta_join(
            missing_coordinate_rows,
            nta_points,
        )

        if source_nta_col is None:
            raise ValueError(
                "Emergency food provider rows do not have usable coordinates "
                "and no NTA field could be matched to the CB3 tract universe. "
                f"Columns found: {providers.columns.tolist()}"
            )

        missing_coordinate_rows["_nta_join"] = _normalize_join_text(
            missing_coordinate_rows[source_nta_col]
        )

        nta_lookup = nta_points.copy()
        nta_lookup["_nta_join"] = _normalize_join_text(
            nta_lookup[target_nta_col]
        )

        matched = missing_coordinate_rows.merge(
            nta_lookup.drop_duplicates("_nta_join"),
            on="_nta_join",
            how="inner",
            suffixes=("", "_cb3"),
        )

        if matched.empty:
            raise ValueError(
                "No NTA-level emergency food provider records matched CB3 NTA "
                "geography."
            )

        canonical_nta_name = None
        for candidate in [
            "nta_name_cb3",
            "nta_name",
            source_nta_col,
        ]:
            if candidate in matched.columns:
                canonical_nta_name = candidate
                break

        canonical_nta_code = None
        for candidate in ["nta_code_cb3", "nta_code"]:
            if candidate in matched.columns:
                canonical_nta_code = candidate
                break

        group_columns = ["_nta_join", "latitude", "longitude"]
        if canonical_nta_code:
            group_columns.append(canonical_nta_code)
        if canonical_nta_name and canonical_nta_name not in group_columns:
            group_columns.append(canonical_nta_name)

        nta_output = (
            matched.groupby(group_columns, dropna=False, as_index=False)
            .agg(provider_count=("_provider_count", "sum"))
        )

        if canonical_nta_code and canonical_nta_code != "nta_code":
            nta_output = nta_output.rename(
                columns={canonical_nta_code: "nta_code"}
            )
        if canonical_nta_name and canonical_nta_name != "nta_name":
            nta_output = nta_output.rename(
                columns={canonical_nta_name: "nta_name"}
            )
        if "nta_name" not in nta_output.columns:
            nta_output["nta_name"] = nta_output["_nta_join"]

        nta_output["provider_name"] = (
            "Emergency food providers in "
            + nta_output["nta_name"].fillna("NTA").astype(str)
        )
        nta_output["address"] = ""
        nta_output["provider_subtype"] = ""
        nta_output["provider_type"] = "Emergency food provider"
        nta_output["location_level"] = "NTA representative point"

        output_parts.append(
            nta_output[
                [
                    "provider_name",
                    "address",
                    "provider_subtype",
                    "provider_count",
                    "provider_type",
                    "location_level",
                    "nta_name",
                    "latitude",
                    "longitude",
                ]
            ]
        )

        matched_source_values = set(matched["_nta_join"].dropna())
        all_source_values = set(
            missing_coordinate_rows["_nta_join"].dropna()
        )
        unmatched_count = len(all_source_values - matched_source_values)

        _log(
            log_lines,
            "Created NTA representative provider points: "
            f"{len(nta_output)} NTA markers; "
            f"{int(nta_output['provider_count'].sum())} providers represented.",
        )
        if unmatched_count:
            _log(
                log_lines,
                "Warning: unmatched NTA provider values: "
                f"{unmatched_count}",
            )

    if not output_parts:
        raise ValueError(
            "No emergency food provider points could be created from the "
            f"source file: {path}"
        )

    output = pd.concat(output_parts, ignore_index=True, sort=False)
    output["provider_name"] = (
        output["provider_name"]
        .fillna("Emergency food provider")
        .astype(str)
        .str.strip()
    )
    output["provider_type"] = "Emergency food provider"
    output["provider_count"] = (
        pd.to_numeric(output["provider_count"], errors="coerce")
        .fillna(1)
    )
    output = output.dropna(
        subset=["latitude", "longitude"]
    ).copy()

    if output.empty:
        raise ValueError(
            "Emergency food provider output has no usable map coordinates."
        )

    output.to_csv(PROVIDER_OUTPUT_PATH, index=False)

    _log(
        log_lines,
        f"Wrote provider points: {PROVIDER_OUTPUT_PATH} "
        f"({len(output)} map markers; "
        f"{int(output['provider_count'].sum())} providers represented)",
    )


def attach_map_geometry(output, cb3_tract_geometry):
    """Attach map-ready centroid and polygon geometry columns to the clean CSV.

    maps.qmd should render from data/clean/economic-food.csv only. This helper
    makes that possible by storing one centroid pair and one polygon WKT per
    tract in the clean output.
    """
    out = output.copy()
    geometry = cb3_tract_geometry.copy()
    geometry["GEOID"] = _normalize_geoid(geometry["GEOID"])

    out = add_polygon_centroids(
        out,
        geometry,
        "GEOID",
        lat_col="tract_centroid_latitude",
        lon_col="tract_centroid_longitude",
    )

    geometry = geometry.to_crs("EPSG:4326")
    geometry["geometry_wkt"] = geometry.geometry.to_wkt()

    out = out.merge(
        geometry[["GEOID", "geometry_wkt"]],
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    missing_geometry = int(out["geometry_wkt"].isna().sum())
    if missing_geometry:
        raise ValueError(f"Missing geometry_wkt for {missing_geometry} tract rows.")

    return out


def main():
    log_lines = []

    _log(log_lines, f"Project directory: {PROJECT_DIR}")
    _log(log_lines, f"Raw directory: {RAW_DIR}")

    tract_universe, cb3_tract_geometry, _, _ = load_cb3_tract_universe(PROJECT_DIR)
    tract_universe = tract_universe.copy()
    tract_universe["GEOID"] = _normalize_geoid(tract_universe["GEOID"])

    base_cols = [
        col for col in ["GEOID", "tract_label", "tract_name", "nta_code", "nta_name", "cdta_code", "cdta_name"]
        if col in tract_universe.columns
    ]
    output = tract_universe[base_cols].drop_duplicates("GEOID").copy()

    if "tract_label" not in output.columns:
        output["tract_label"] = output["GEOID"].str[-6:].str.lstrip("0")

    poverty = build_poverty_metrics(log_lines)
    output = output.merge(poverty, on="GEOID", how="left", validate="one_to_one")

    optional_builders = [
        build_unemployment_metrics,
        build_income_metrics,
        build_earnings_metrics,
        build_places_metrics,
    ]

    for builder in optional_builders:
        metrics = builder(log_lines)
        if metrics is not None:
            output = output.merge(metrics, on="GEOID", how="left", validate="one_to_one")

    output = build_food_gap_context(log_lines, output)
    output = build_safety_net_metric(log_lines, output)
    output = add_baseline_demographics(log_lines, output)
    build_provider_points(
        log_lines,
        tract_universe,
        cb3_tract_geometry,
    )
    output = attach_map_geometry(output, cb3_tract_geometry)

    assert len(output) == 31, f"Expected 31 CB3 tracts, got {len(output)}"
    assert output["GEOID"].is_unique, "GEOID is not unique."

    output.to_csv(OUTPUT_PATH, index=False)
    _log(log_lines, f"Wrote {OUTPUT_PATH} ({len(output)} rows, {len(output.columns)} columns)")

    tracked_metrics = [
        "safety_net_enrollment_per_capita",
        "unemployment_rate_pct",
        "median_household_income",
        "median_earnings_all_workers",
        "food_insecurity_rate_pct",
        "utility_shutoff_risk_pct",
        "poverty_rate_pct",
        "food_supply_gap_rank_nta",
        "food_supply_gap_score_nta",
    ]

    _log(log_lines, "Missing values by key metric:")
    for metric in tracked_metrics:
        if metric in output.columns:
            _log(log_lines, f" - {metric}: {output[metric].isna().sum()} missing / {output[metric].notna().sum()} non-null")
        else:
            _log(log_lines, f" - {metric}: missing column")

    _log(log_lines, "Columns:")
    for col in output.columns:
        _log(log_lines, f" - {col}")

    LOG_PATH.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    _log(log_lines, f"Wrote log: {LOG_PATH}")


if __name__ == "__main__":
    main()
