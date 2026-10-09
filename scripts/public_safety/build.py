"""Build CB3 public-safety data from local raw files.

Housing input may be either:
  1. a prepared CSV with GEOID,total_housing_units; optional housing_units_vintage, or
  2. a raw ACS B25001 CSV containing B25001_001E plus GEOID or state/county/tract.
Census fetch requires CENSUS_API_KEY in the environment; 

Burglary rate = pooled 2021–2025 completed residential burglary complaints /
tract-level total housing units * 1000, not annualized. Preferred denominator:
ACS 2020–2024 five-year B25001_001E (2024 ACS 5-year release).

Calls source (Historic): https://data.cityofnewyork.us/resource/d6zx-ckhd.json
Response minutes = (ARRIVD_TS - ADD_TS).total_seconds() / 60. ADD_TS is the
ICAD entry timestamp; this is not an independently observed telephone-ring
start. The output is entry-based and includes officer-initiated entries and
zero intervals, not an official 911 response statistic. Missing/negative
intervals are excluded from clean response records and medians.
The API bounding-box filter excludes missing locations before downloading.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path

import geopandas as gpd
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data/raw/Public Safety"
HOUSING_RAW_DIR = PROJECT_ROOT / "data/raw/Housing and Affordability"

DEFAULT_CALLS_CANDIDATES = [
    RAW_DIR / "nypd_calls_2021_2025.csv"
]

DEFAULT_HOUSING_CANDIDATES = [
    RAW_DIR / "acs_2024_housing_units.csv",
    HOUSING_RAW_DIR / "acs_5yr_2024_B25001.csv",
]

START_DATE = "2021-01-01"
END_DATE = "2025-12-31"

VIOLENT_OFFENSES = [
    "MURDER & NON-NEGL. MANSLAUGHTER",
    "RAPE",
    "ROBBERY",
    "FELONY ASSAULT",
]


def first_existing_path(paths: list[Path]) -> Path | None:
    """Return the first existing local file from a prioritized candidate list."""
    return next((path for path in paths if path.is_file()), None)


def normalize_upper(series: pd.Series) -> pd.Series:
    """Trim whitespace and standardize text values to uppercase."""
    return series.astype("string").str.strip().str.upper()


def read_analysis_period(input_path: Path, date_column: str, borough_column: str) -> pd.DataFrame:
    """Read large sources in chunks, retaining Manhattan events from 2021–2025."""
    retained = []
    for chunk in pd.read_csv(input_path, chunksize=100_000, low_memory=False):
        dates = pd.to_datetime(chunk[date_column], format="%m/%d/%Y", errors="coerce")
        keep = dates.between(START_DATE, END_DATE) & normalize_upper(chunk[borough_column]).eq("MANHATTAN")
        retained.append(chunk.loc[keep].copy())
    return pd.concat(retained, ignore_index=True)


def join_cb3(events: pd.DataFrame, latitude: str, longitude: str,
             tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Retain points strictly within CB3 tracts and attach their GEOID."""
    valid = events[longitude].between(-75, -73) & events[latitude].between(40, 41.5)
    points = gpd.GeoDataFrame(
        events.loc[valid].copy(),
        geometry=gpd.points_from_xy(events.loc[valid, longitude], events.loc[valid, latitude]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, tracts, how="left", predicate="within")
    if joined.index.duplicated().any():
        raise ValueError("An event matched multiple CB3 tracts; inspect tract geometry.")
    matched = joined["GEOID"].notna()
    result = pd.DataFrame(joined.loc[matched].drop(columns=["geometry", "index_right"]))
    return result.reset_index(drop=True)


def build_cb3_outputs(mvc: pd.DataFrame, violent: pd.DataFrame,
                      burglary: pd.DataFrame, output_dir: Path) -> None:
    """Write CB3 event files and zero-filled five-year tract counts."""
    tracts = gpd.read_file(PROJECT_ROOT / "data/raw/Geography/cb3_2020_census_tracts.geojson")
    tracts = tracts.rename(columns={"geoid": "GEOID"})[["GEOID", "geometry"]].to_crs("EPSG:4326")
    tracts["GEOID"] = tracts["GEOID"].astype("string").str.zfill(11)
    if not tracts["GEOID"].is_unique or not tracts.geometry.is_valid.all():
        raise ValueError("CB3 tract identifiers must be unique and geometries valid.")

    # Residential premises define exposure at homes; retain completed felony
    # complaints at the three residential premise types.
    residential = burglary[
        burglary["PREM_TYP_DESC"].isin([
            "RESIDENCE - APT. HOUSE", "RESIDENCE - PUBLIC HOUSING", "RESIDENCE-HOUSE",
        ])
        & burglary["CRM_ATPT_CPTD_CD"].eq("COMPLETED")
        & burglary["LAW_CAT_CD"].eq("FELONY")
    ].copy()
    outputs = {}
    for name, events, lat, lon in [
        ("mvc_traffic_safety", mvc, "LATITUDE", "LONGITUDE"),
        ("nypd_violent_crime", violent, "Latitude", "Longitude"),
        ("nypd_residential_burglary", residential, "Latitude", "Longitude"),
    ]:
        clean = join_cb3(events, lat, lon, tracts)
        clean.to_csv(output_dir / f"{name}_clean.csv", index=False)
        outputs[name] = clean

    # Include every CB3 tract, even when it has no matched events. These are
    # five-year counts, not population-normalized rates or annual averages.
    summary = tracts[["GEOID"]].set_index("GEOID")
    traffic = outputs["mvc_traffic_safety"].groupby("GEOID")
    summary["ped_cyclist_injury_crashes"] = traffic["PED_CYCLIST_INJURY_CRASH"].sum()
    summary["any_injury_crashes"] = traffic["ANY_INJURY_CRASH"].sum()
    summary["violent_crime_complaints"] = outputs["nypd_violent_crime"].groupby("GEOID").size()
    summary["residential_burglary_complaints"] = outputs["nypd_residential_burglary"].groupby("GEOID").size()
    summary.fillna(0).astype(int).sort_index().reset_index().to_csv(
        output_dir / "public_safety_tract.csv", index=False)


def clean_mvc(input_path: Path) -> pd.DataFrame:
    """Clean Motor Vehicle Collisions data for Public Safety analysis."""
    print(f"\nReading MVC data: {input_path}")
    mvc = read_analysis_period(input_path, "CRASH DATE", "BOROUGH")

    required_cols = [
        "CRASH DATE",
        "BOROUGH",
        "LATITUDE",
        "LONGITUDE",
        "NUMBER OF PERSONS INJURED",
        "NUMBER OF PEDESTRIANS INJURED",
        "NUMBER OF CYCLIST INJURED",
        "COLLISION_ID",
    ]
    missing = [col for col in required_cols if col not in mvc.columns]
    if missing:
        raise ValueError(f"MVC file is missing required columns: {missing}")

    mvc["CRASH DATE"] = pd.to_datetime(mvc["CRASH DATE"], format="%m/%d/%Y", errors="coerce")

    # Restrict to requested analysis period.
    mvc = mvc[
        mvc["CRASH DATE"].between(START_DATE, END_DATE)
    ].copy()

    # Restrict to Manhattan.
    mvc["BOROUGH"] = normalize_upper(mvc["BOROUGH"])
    mvc = mvc[mvc["BOROUGH"].eq("MANHATTAN")].copy()

    injury_cols = [
        "NUMBER OF PERSONS INJURED",
        "NUMBER OF PEDESTRIANS INJURED",
        "NUMBER OF CYCLIST INJURED",
    ]
    for col in injury_cols:
        mvc[col] = pd.to_numeric(mvc[col], errors="coerce").fillna(0)

    mvc["LATITUDE"] = pd.to_numeric(mvc["LATITUDE"], errors="coerce")
    mvc["LONGITUDE"] = pd.to_numeric(mvc["LONGITUDE"], errors="coerce")

    # Kailey's preferred traffic-safety metric:
    # collision where at least one pedestrian or cyclist was injured.
    mvc["PED_CYCLIST_INJURY_CRASH"] = (
        (mvc["NUMBER OF PEDESTRIANS INJURED"] > 0)
        | (mvc["NUMBER OF CYCLIST INJURED"] > 0)
    )

    # Fallback metric if the preferred metric is too sparse.
    mvc["ANY_INJURY_CRASH"] = mvc["NUMBER OF PERSONS INJURED"] > 0

    keep_cols = [
        "COLLISION_ID",
        "CRASH DATE",
        "BOROUGH",
        "LATITUDE",
        "LONGITUDE",
        "NUMBER OF PERSONS INJURED",
        "NUMBER OF PEDESTRIANS INJURED",
        "NUMBER OF CYCLIST INJURED",
        "PED_CYCLIST_INJURY_CRASH",
        "ANY_INJURY_CRASH",
    ]

    mvc_clean = mvc[keep_cols].copy()
    mvc_clean = mvc_clean.drop_duplicates(subset=["COLLISION_ID"])

    return mvc_clean


def clean_nypd(input_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean NYPD Complaint Historic data for violent crime and burglary analysis."""
    print(f"\nReading NYPD data: {input_path}")
    nypd = read_analysis_period(input_path, "CMPLNT_FR_DT", "BORO_NM")

    required_cols = [
        "CMPLNT_NUM",
        "CMPLNT_FR_DT",
        "OFNS_DESC",
        "PD_DESC",
        "CRM_ATPT_CPTD_CD",
        "LAW_CAT_CD",
        "BORO_NM",
        "PREM_TYP_DESC",
        "Latitude",
        "Longitude",
    ]
    missing = [col for col in required_cols if col not in nypd.columns]
    if missing:
        raise ValueError(f"NYPD file is missing required columns: {missing}")

    nypd["CMPLNT_FR_DT"] = pd.to_datetime(
        nypd["CMPLNT_FR_DT"],
        format="%m/%d/%Y",
        errors="coerce",
    )

    # Restrict to requested analysis period.
    nypd = nypd[
        nypd["CMPLNT_FR_DT"].between(START_DATE, END_DATE)
    ].copy()

    # Restrict to Manhattan.
    nypd["BORO_NM"] = normalize_upper(nypd["BORO_NM"])
    nypd = nypd[nypd["BORO_NM"].eq("MANHATTAN")].copy()

    text_cols = [
        "OFNS_DESC",
        "PD_DESC",
        "PREM_TYP_DESC",
        "LAW_CAT_CD",
        "CRM_ATPT_CPTD_CD",
    ]
    for col in text_cols:
        nypd[col] = normalize_upper(nypd[col])

    nypd["Latitude"] = pd.to_numeric(nypd["Latitude"], errors="coerce")
    nypd["Longitude"] = pd.to_numeric(nypd["Longitude"], errors="coerce")

    # ------------------------------------------------------------------
    # Violent felony exposure
    # ------------------------------------------------------------------
    violent = nypd[
        nypd["OFNS_DESC"].isin(VIOLENT_OFFENSES)
        & nypd["LAW_CAT_CD"].eq("FELONY")
    ].copy()

    violent_keep = [
        "CMPLNT_NUM",
        "CMPLNT_FR_DT",
        "OFNS_DESC",
        "PD_DESC",
        "CRM_ATPT_CPTD_CD",
        "LAW_CAT_CD",
        "BORO_NM",
        "PREM_TYP_DESC",
        "Latitude",
        "Longitude",
    ]
    violent = violent[violent_keep].copy()
    violent = violent.drop_duplicates(subset=["CMPLNT_NUM"])

    # The final output function applies the residential-premise filter.
    burglary = nypd[nypd["OFNS_DESC"].eq("BURGLARY")].copy()

    burglary_keep = [
        "CMPLNT_NUM",
        "CMPLNT_FR_DT",
        "OFNS_DESC",
        "PD_DESC",
        "CRM_ATPT_CPTD_CD",
        "LAW_CAT_CD",
        "BORO_NM",
        "LOC_OF_OCCUR_DESC",
        "PREM_TYP_DESC",
        "Latitude",
        "Longitude",
    ]

    # script to continue if a future extract omits it.
    burglary_keep = [col for col in burglary_keep if col in burglary.columns]

    burglary = burglary[burglary_keep].copy()
    burglary = burglary.drop_duplicates(subset=["CMPLNT_NUM"])

    return violent, burglary


# %% Optional API inputs
# Normal builds reuse existing local calls/housing files when available.
# API downloads happen only when the corresponding --fetch-* flag is explicit.
# Housing can be supplied as a local GEOID,total_housing_units CSV, a raw ACS
# B25001 CSV, or fetched with --fetch-housing-units.
def api_json(url: str, params: dict, token: str | None = None):
    """Read JSON with bounded retries; never include credential-bearing URLs in errors."""
    headers = {"Accept": "application/json"}
    if token:
        headers["X-App-Token"] = token
    request = Request(url + "?" + urlencode(params), headers=headers)
    for attempt in range(4):
        try:
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError(f"API request failed (HTTP {error.code}).") from None
        except (URLError, TimeoutError):
            if attempt == 3:
                raise RuntimeError("API network request failed after four attempts.") from None
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("API returned non-JSON data; check access/key requirements.") from None
        time.sleep(2 ** attempt)


def fetch_housing_units(output_path: Path) -> None:
    """Cache ACS 2020–2024 five-year B25001 total housing units for Manhattan tracts.

    B25001_001E is the estimate of total housing units for each census tract.
    The API request returns all Manhattan tracts; add_burglary_rate() later keeps
    only the GEOIDs present in the 31-tract CB3 summary.
    """
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise ValueError(
            "Set CENSUS_API_KEY locally, or supply --housing-units with a "
            "prepared housing CSV or raw ACS B25001 CSV."
        )

    rows = api_json(
        "https://api.census.gov/data/2024/acs/acs5",
        {
            "get": "NAME,B25001_001E",
            "for": "tract:*",
            "in": "state:36 county:061",
            "key": key,
        },
    )

    frame = pd.DataFrame(rows[1:], columns=rows[0])
    frame["GEOID"] = frame["state"] + frame["county"] + frame["tract"]
    frame["total_housing_units"] = pd.to_numeric(
        frame["B25001_001E"], errors="raise"
    )
    frame["housing_units_vintage"] = "ACS 2020–2024 5-year"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame[["GEOID", "total_housing_units", "housing_units_vintage"]].to_csv(
        output_path, index=False
    )
    print(f"Saved tract-level ACS B25001 housing units: {output_path}")


def load_housing_units(housing_path: Path) -> pd.DataFrame:
    """Standardize a prepared housing file or raw ACS B25001 export.

    Accepted inputs
    ---------------
    Prepared:
        GEOID,total_housing_units[,housing_units_vintage]

    Raw ACS B25001:
        B25001_001E plus either GEOID or state/county/tract geography columns.
    """
    housing = pd.read_csv(housing_path, dtype="string", low_memory=False)

    if "GEOID" not in housing.columns:
        geo_parts = {"state", "county", "tract"}
        if geo_parts <= set(housing.columns):
            housing["GEOID"] = (
                housing["state"].str.zfill(2)
                + housing["county"].str.zfill(3)
                + housing["tract"].str.zfill(6)
            )
        else:
            raise ValueError(
                "Housing file must contain GEOID or state/county/tract columns."
            )

    housing["GEOID"] = (
        housing["GEOID"]
        .astype("string")
        .str.replace(r"\D", "", regex=True)
        .str.zfill(11)
    )

    if "total_housing_units" not in housing.columns:
        if "B25001_001E" not in housing.columns:
            raise ValueError(
                "Housing file must contain total_housing_units or ACS B25001_001E."
            )
        housing["total_housing_units"] = housing["B25001_001E"]

    housing["total_housing_units"] = pd.to_numeric(
        housing["total_housing_units"], errors="coerce"
    )

    if "housing_units_vintage" not in housing.columns:
        housing["housing_units_vintage"] = "ACS 2020–2024 5-year"
    else:
        housing["housing_units_vintage"] = (
            housing["housing_units_vintage"]
            .fillna("ACS 2020–2024 5-year")
            .astype("string")
        )

    housing = housing[[
        "GEOID", "total_housing_units", "housing_units_vintage"
    ]].copy()

    if housing["GEOID"].duplicated().any():
        duplicates = housing.loc[
            housing["GEOID"].duplicated(keep=False), "GEOID"
        ].dropna().unique().tolist()
        raise ValueError(
            "Housing denominator contains duplicate GEOIDs: "
            + ", ".join(duplicates[:10])
        )

    return housing


def add_burglary_rate(output_dir: Path, housing_path: Path) -> None:
    """Add pooled residential burglaries per 1,000 tract housing units.

    Numerator: completed residential burglary complaints pooled across 2021–2025.
    Denominator: tract-specific total housing units from ACS B25001_001E.
    The result is a pooled five-year rate, not an annualized rate.
    """
    summary_path = output_dir / "public_safety_tract.csv"
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"Missing {summary_path}; run the initial Public Safety build first."
        )

    summary = pd.read_csv(summary_path, dtype={"GEOID": "string"})
    summary["GEOID"] = summary["GEOID"].str.zfill(11)
    housing = load_housing_units(housing_path)

    cb3_geoids = set(summary["GEOID"].dropna())
    available_geoids = set(housing["GEOID"].dropna())
    missing_geoids = sorted(cb3_geoids - available_geoids)
    if missing_geoids:
        raise ValueError(
            "Housing denominator is missing CB3 GEOIDs: "
            + ", ".join(missing_geoids)
        )

    # Keep only the 31 CB3 tract denominators before the one-to-one merge.
    housing = housing[housing["GEOID"].isin(cb3_geoids)].copy()

    columns = [
        "total_housing_units",
        "housing_units_vintage",
        "residential_burglary_per_1000_units",
        "burglary_rate_numerator_period",
        "burglary_rate_denominator_source",
    ]
    summary = summary.drop(columns=columns, errors="ignore").merge(
        housing,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    denominator = summary["total_housing_units"].where(
        summary["total_housing_units"] > 0
    )
    summary["residential_burglary_per_1000_units"] = (
        summary["residential_burglary_complaints"] / denominator * 1000
    )
    summary["burglary_rate_numerator_period"] = "2021–2025 pooled"
    summary["burglary_rate_denominator_source"] = (
        "ACS B25001_001E total housing units"
    )

    summary.to_csv(summary_path, index=False)

    missing_denominator = int(denominator.isna().sum())
    print(
        "Burglary rate saved: pooled 2021–2025 completed residential "
        "burglary complaints per 1,000 tract housing units."
    )
    print(
        f"Housing denominator source: {housing_path} "
        f"({len(housing):,} CB3 tract rows)."
    )
    print(
        f"{missing_denominator} CB3 tracts lack a positive housing-unit denominator."
    )

def fetch_nypd_calls(output_path: Path, page_size: int = 10000) -> None:
    """Download 2021–2025 call entries in CB3's bounding box, one year/page at a time."""
    if not 1 <= page_size <= 50000:
        raise ValueError("page_size must be between 1 and 50000")
    tracts = gpd.read_file(PROJECT_ROOT / "data/raw/Geography/cb3_2020_census_tracts.geojson").to_crs(4326)
    west, south, east, north = tracts.total_bounds
    columns = ["objectid", "cad_evnt_id", "create_date", "add_ts", "disp_ts", "arrivd_ts",
               "closng_ts", "typ_desc", "radio_code", "boro_nm", "latitude", "longitude"]
    # Use Socrata's unique system row ID for consistent source-entry identity;
    # cad_evnt_id can repeat across source entries.
    select_columns = [":id AS objectid", *columns[1:]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".partial.csv")
    pd.DataFrame(columns=columns).to_csv(temporary, index=False)
    coverage = []
    # Socrata's default limit is too small: explicitly paginate and order by
    # the stable system row ID. Only fetch required fields and nearby points.
    for year in range(2021, 2026):
        where = (f"add_ts >= '{year}-01-01T00:00:00' AND add_ts < '{year + 1}-01-01T00:00:00' "
                 f"AND boro_nm = 'MANHATTAN' AND latitude BETWEEN {south} AND {north} "
                 f"AND longitude BETWEEN {west} AND {east}")
        offset = 0
        endpoint = "https://data.cityofnewyork.us/resource/d6zx-ckhd.json"
        token = os.environ.get("SOCRATA_APP_TOKEN")
        expected = int(api_json(endpoint, {"$select": "count(*) AS n", "$where": where}, token)[0]["n"])
        while offset < expected:
            rows = api_json(endpoint, {"$select": ",".join(select_columns), "$where": where,
                                      "$order": ":id", "$limit": page_size, "$offset": offset}, token)
            if not rows:
                break
            pd.DataFrame(rows).reindex(columns=columns).to_csv(temporary, mode="a", header=False, index=False)
            offset += len(rows)
            print(f"NYPD {year}: {offset:,}/{expected:,} bounding-box entries downloaded", flush=True)
            if len(rows) < page_size:
                break
        if offset != expected:
            raise RuntimeError("API row count changed during pagination; rerun to avoid an incomplete extract.")
        coverage.append({"year": year, "downloaded_entries": offset})
    if not any(row["downloaded_entries"] for row in coverage):
        temporary.unlink()
        raise ValueError(
            "Historic source d6zx-ckhd returned no matching 2021–2025 entries. "
            "Existing outputs were not replaced."
        )
    temporary.replace(output_path)
    missing_years = [str(row["year"]) for row in coverage if row["downloaded_entries"] == 0]
    print(f"Saved raw API records: {output_path}")
    if missing_years:
        print(f"Source returned no matching records for: {', '.join(missing_years)}.")


def clean_nypd_response_times(input_path: Path, output_dir: Path) -> None:
    """Read a saved raw NYPD Calls extract and write CB3 response-time outputs."""
    calls = pd.read_csv(input_path, low_memory=False)
    # NYC Open Data exports may preserve source casing differently. Standardize
    # the saved local file before validating required fields.
    calls.columns = [str(column).strip().lower() for column in calls.columns]

    required = {
        "objectid", "cad_evnt_id", "add_ts", "disp_ts", "arrivd_ts",
        "closng_ts", "latitude", "longitude",
    }
    missing = sorted(required - set(calls.columns))
    if missing:
        raise ValueError(
            f"NYPD calls file is missing required columns: {missing}. "
            "Use the saved raw Calls-for-Service extract, not a cleaned tract table."
        )

    calls["objectid"] = calls["objectid"].astype("string")
    calls["cad_evnt_id"] = calls["cad_evnt_id"].astype("string")
    if calls["objectid"].isna().any() or calls["objectid"].duplicated().any():
        raise ValueError("NYPD calls extract has missing/duplicate objectid values.")
    for column in ["add_ts", "disp_ts", "arrivd_ts", "closng_ts"]:
        calls[column] = pd.to_datetime(calls[column], errors="coerce")
    calls = calls[calls["add_ts"].ge(START_DATE) & calls["add_ts"].lt("2026-01-01")].copy()
    calls["response_minutes"] = (calls["arrivd_ts"] - calls["add_ts"]).dt.total_seconds() / 60
    calls = calls[calls["response_minutes"].ge(0)].copy()
    for column in ["latitude", "longitude"]:
        calls[column] = pd.to_numeric(calls[column], errors="coerce")
    tracts = gpd.read_file(PROJECT_ROOT / "data/raw/Geography/cb3_2020_census_tracts.geojson").rename(columns={"geoid": "GEOID"})
    tracts = tracts[["GEOID", "geometry"]].to_crs(4326)
    matched = join_cb3(calls, "latitude", "longitude", tracts)
    output_dir.mkdir(parents=True, exist_ok=True)
    matched.to_csv(output_dir / "nypd_response_time_clean.csv", index=False)
    print(f"Saved {len(matched):,} clean response entries: {output_dir / 'nypd_response_time_clean.csv'}")
    stats = matched.groupby("GEOID")["response_minutes"].agg(
        nypd_entry_to_arrival_median_minutes="median", nypd_response_valid_entries="count")
    result = tracts[["GEOID"]].merge(stats, on="GEOID", how="left")
    result["nypd_response_valid_entries"] = result["nypd_response_valid_entries"].fillna(0).astype(int)
    result.to_csv(output_dir / "nypd_response_time_tract.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build Public Safety event datasets and census-tract counts for CB3 "
            "from NYC Motor Vehicle Collisions and NYPD Complaint Historic."
        )
    )

    parser.add_argument(
        "--mvc",
        type=Path,
        default=RAW_DIR / "Motor_Vehicle_Collisions_-_Crashes_20261006.csv",
        help="Path to the raw Motor Vehicle Collisions CSV.",
    )
    parser.add_argument(
        "--nypd",
        type=Path,
        default=RAW_DIR / "NYPD_Complaint_Data_Historic_20261006.csv",
        help="Path to the raw NYPD Complaint Data Historic CSV.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data/clean",
        help="Directory for cleaned outputs.",
    )

    parser.add_argument(
        "--housing-units",
        type=Path,
        default=None,
        help=(
            "Prepared GEOID,total_housing_units CSV or raw ACS B25001 CSV. "
            "If omitted, the script reuses the first existing project B25001 file."
        ),
    )
    parser.add_argument(
        "--fetch-housing-units",
        action="store_true",
        help=(
            "Fetch ACS 2020–2024 5-year B25001_001E for Manhattan tracts; "
            "requires CENSUS_API_KEY."
        ),
    )
    parser.add_argument(
        "--fetch-calls",
        action="store_true",
        help=(
            "Reserved for a future NYPD Calls API refresh. The API invocation is "
            "currently disabled; local saved calls CSVs are reused instead."
        ),
    )
    parser.add_argument(
        "--calls-csv",
        type=Path,
        default=None,
        help=(
            "Previously downloaded NYPD Calls CSV. If omitted, the script checks "
            "the standard local filenames in data/raw/Public Safety."
        ),
    )
    parser.add_argument("--api-only", action="store_true", help="Process optional API inputs without rereading the MVC/NYPD complaint files.")
    parser.add_argument("--page-size", type=int, default=10000)
    args = parser.parse_args()

    # Resolve housing denominator. Reuse local project data unless the user
    # explicitly requests a fresh Census API pull.
    if args.fetch_housing_units:
        housing_path = args.housing_units or DEFAULT_HOUSING_CANDIDATES[0]
        fetch_housing_units(housing_path)
    elif args.housing_units is not None:
        housing_path = args.housing_units
    else:
        housing_path = first_existing_path(DEFAULT_HOUSING_CANDIDATES)

    # Resolve NYPD Calls source from LOCAL files only.
    #
    # The API downloader is intentionally retained above for future reuse, but
    # its invocation is commented out so this build cannot call the NYPD endpoint.
    # To re-enable a refresh later, restore the three commented lines below.
    #
    # if args.fetch_calls:
    #     calls_path = args.calls_csv or DEFAULT_CALLS_CANDIDATES[-1]
    #     fetch_nypd_calls(calls_path, args.page_size)
    #
    # Current behavior: always reuse a supplied or existing local raw CSV.
    if args.fetch_calls:
        print(
            "--fetch-calls is currently disabled; reusing a local NYPD Calls CSV instead."
        )

    if args.calls_csv is not None:
        calls_path = args.calls_csv
    else:
        calls_path = first_existing_path(DEFAULT_CALLS_CANDIDATES)

    if calls_path is not None:
        if not calls_path.is_file():
            parser.error(f"NYPD calls CSV not found: {calls_path}")
        print(f"Reusing local NYPD calls file: {calls_path}")
        clean_nypd_response_times(calls_path, args.output_dir)
    else:
        print(
            "NYPD response-time step skipped: no local calls CSV found. "
            "Place the saved calls file in data/raw/Public Safety or pass --calls-csv PATH."
        )

    if args.api_only:
        did_optional_work = calls_path is not None
        if housing_path is not None:
            if not housing_path.is_file():
                parser.error(f"Housing-units file not found: {housing_path}")
            print(f"Using tract housing denominator: {housing_path}")
            add_burglary_rate(args.output_dir, housing_path)
            did_optional_work = True
        if not did_optional_work:
            parser.error(
                "No optional local input found. Provide --housing-units or "
                "--calls-csv, or explicitly request --fetch-housing-units."
            )
        return

    for input_path in (args.mvc, args.nypd):
        if not input_path.is_file():
            parser.error(f"Input CSV not found: {input_path}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    mvc = clean_mvc(args.mvc)
    violent, burglary = clean_nypd(args.nypd)
    build_cb3_outputs(mvc, violent, burglary, args.output_dir)
    if housing_path is not None:
        if not housing_path.is_file():
            parser.error(f"Housing-units file not found: {housing_path}")
        print(f"Using tract housing denominator: {housing_path}")
        add_burglary_rate(args.output_dir, housing_path)
    else:
        print(
            "Burglary rate pending: no local ACS B25001 file found. "
            "Place acs_5yr_2024_B25001.csv under data/raw/Housing and Affordability, "
            "pass --housing-units PATH, or explicitly use --fetch-housing-units."
        )
    print(f"\nBuild complete. CB3 event files and tract counts saved in {args.output_dir}")



if __name__ == "__main__":
    main()
