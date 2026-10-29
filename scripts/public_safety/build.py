"""Build pooled 2021–2025 CB3 public-safety metrics from local snapshots (no API calls).

Metrics:
- Traffic: NYC MVC Crashes with a pedestrian/cyclist injured or killed. Fatal
  crashes take precedence over nonfatal injury crashes in the pie. Retain raw
  casualty counts; the fallback covers any person injured
  or killed. No Person-file join.
- Violent crime: NYPD Historic felony murder/non-negligent manslaughter, rape,
  robbery and felony assault, including attempts; deduplicate by CMPLNT_NUM.
- Burglary: NYPD Historic felony complaints, including attempts, at RESIDENCE -
  APT. HOUSE, RESIDENCE - PUBLIC HOUSING or RESIDENCE-HOUSE; deduplicate by
  CMPLNT_NUM. Retain counts and calculate complaints / housing units * 1,000
  using local ACS 2024 five-year B25001_001E (occupied + vacant). Not annualized.
- Response: tract median (ARRIVD_TS - ADD_TS) in minutes, retaining positive
  intervals only; exclude missing/unparseable timestamps, equal times and
  negative intervals. ADD_TS is ICAD entry, not telephone-ring time. Filters
  reduce self-initiated entries but cannot identify every public 911 call.
  Require unique source row IDs; retain repeated CAD event IDs. Historic source:
  https://data.cityofnewyork.us/resource/d6zx-ckhd.json

Geography:
Use strict point-in-polygon joins to 2020 CB3 tracts (GEOID); exclude invalid,
unmatched and boundary points. Preserve MVC/complaint borough labels without
filtering on them. Calls were downloaded with Manhattan + CB3 bounding-box
filters, already excluding missing coordinates. Optional --mvc-geocodes uses
reviewed local address/intersection matches for unusable collision coordinates;
originals remain intact, with LOCATION_LATITUDE/LONGITUDE used for joining.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data/raw/Public Safety"
DEFAULT_HOUSING_PATH = PROJECT_ROOT / "data/raw/Housing and Affordability/acs_5yr_2024_B25001.csv"

DEFAULT_CALLS_CANDIDATES = [
    RAW_DIR / "nypd_calls_2021_2025.csv"
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


def read_analysis_period(input_path: Path, date_column: str, offenses: list[str] | None = None) -> pd.DataFrame:
    """Read citywide events by date, optionally limiting complaint offense types."""
    retained = []
    for chunk in pd.read_csv(input_path, chunksize=100_000, low_memory=False):
        dates = pd.to_datetime(chunk[date_column], format="%m/%d/%Y", errors="coerce")
        keep = dates.between(START_DATE, END_DATE)
        # Limit offenses early for memory; never filter on borough labels.
        if offenses is not None:
            keep &= normalize_upper(chunk["OFNS_DESC"]).isin(offenses)
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


def add_response_metrics(summary: pd.DataFrame, response: pd.DataFrame | None) -> pd.DataFrame:
    """Attach response metrics by GEOID, preserving missing medians and integer counts."""
    columns = ["nypd_entry_to_arrival_median_minutes", "nypd_response_valid_entries"]
    summary = summary.drop(columns=columns, errors="ignore")
    if response is None:
        summary[columns[0]] = float("nan")
        summary[columns[1]] = 0
    else:
        if set(summary["GEOID"]) != set(response["GEOID"]):
            raise ValueError("Response metrics must cover the same tract universe as the summary.")
        summary = summary.merge(response[["GEOID", *columns]], on="GEOID", how="left", validate="one_to_one")
        summary[columns[1]] = summary[columns[1]].fillna(0).astype(int)
    return summary


def add_burglary_rate(summary: pd.DataFrame, housing_path: Path) -> pd.DataFrame:
    """Join local ACS total housing units and calculate pooled complaints per 1,000 units."""
    housing = pd.read_csv(housing_path, dtype="string")
    if "GEOID" not in housing:
        housing["GEOID"] = (housing["state"].str.zfill(2)
                            + housing["county"].str.zfill(3)
                            + housing["tract"].str.zfill(6))
    housing["GEOID"] = housing["GEOID"].str.strip().str.zfill(11)
    source_column = "total_housing_units" if "total_housing_units" in housing else "B25001_001E"
    housing["total_housing_units"] = pd.to_numeric(housing[source_column], errors="coerce")
    if housing["GEOID"].duplicated().any():
        raise ValueError("Housing file contains duplicate GEOIDs.")
    if not set(summary["GEOID"]) <= set(housing["GEOID"]):
        raise ValueError("Housing file must include all CB3 tracts.")
    if "housing_units_vintage" not in housing:
        housing["housing_units_vintage"] = "ACS 2024 5-year (2020–2024)"
    columns = ["total_housing_units", "housing_units_vintage"]
    result = summary.drop(columns=[*columns, "residential_burglary_per_1000_units"], errors="ignore").merge(
        housing[["GEOID", *columns]], on="GEOID", how="left", validate="one_to_one")
    denominator = result["total_housing_units"].where(result["total_housing_units"] > 0)
    result["residential_burglary_per_1000_units"] = (
        result["residential_burglary_complaints"] / denominator * 1000
    )
    return result


def build_cb3_outputs(mvc: pd.DataFrame, violent: pd.DataFrame,
                      burglary: pd.DataFrame, output_dir: Path,
                      response_summary: pd.DataFrame | None = None,
                      housing_path: Path = DEFAULT_HOUSING_PATH) -> None:
    """Write CB3 event files and one tract table for all public-safety metrics."""
    tracts = gpd.read_file(PROJECT_ROOT / "data/raw/Geography/cb3_2020_census_tracts.geojson")
    tracts = tracts.rename(columns={"geoid": "GEOID"})[["GEOID", "geometry"]].to_crs("EPSG:4326")
    tracts["GEOID"] = tracts["GEOID"].astype("string").str.zfill(11)
    if not tracts["GEOID"].is_unique or not tracts.geometry.is_valid.all():
        raise ValueError("CB3 tract identifiers must be unique and geometries valid.")

    outputs = {}
    for name, events, lat, lon in [
        ("mvc_traffic_safety", mvc, "LOCATION_LATITUDE", "LOCATION_LONGITUDE"),
        ("nypd_violent_crime", violent, "Latitude", "Longitude"),
        ("nypd_residential_burglary", burglary, "Latitude", "Longitude"),
    ]:
        clean = join_cb3(events, lat, lon, tracts)
        if name == "mvc_traffic_safety":
            recovered = clean["COORDINATE_SOURCE"].eq("geocoded")
            print(f"CB3 collisions added by geocoding: {recovered.sum():,}; "
                  f"qualifying injury/fatal collisions: "
                  f"{(recovered & clean['PED_CYCLIST_INJURY_OR_FATAL_CRASH']).sum():,}")
        clean.to_csv(output_dir / f"{name}_clean.csv", index=False)
        outputs[name] = clean

    # Zero-fill pooled event counts for every tract before adding rates/medians.
    summary = tracts[["GEOID"]].set_index("GEOID")
    traffic = outputs["mvc_traffic_safety"].groupby("GEOID")
    summary["ped_cyclist_injury_crashes"] = traffic["PED_CYCLIST_INJURY_CRASH"].sum()
    summary["any_injury_crashes"] = traffic["ANY_INJURY_CRASH"].sum()
    summary["ped_cyclist_fatal_crashes"] = traffic["PED_CYCLIST_FATAL_CRASH"].sum()
    summary["ped_cyclist_nonfatal_injury_crashes"] = traffic["PED_CYCLIST_NONFATAL_INJURY_CRASH"].sum()
    summary["ped_cyclist_injury_or_fatal_crashes"] = traffic["PED_CYCLIST_INJURY_OR_FATAL_CRASH"].sum()
    summary["any_injury_or_fatal_crashes"] = traffic["ANY_INJURY_OR_FATAL_CRASH"].sum()
    summary["violent_crime_complaints"] = outputs["nypd_violent_crime"].groupby("GEOID").size()
    summary["residential_burglary_complaints"] = outputs["nypd_residential_burglary"].groupby("GEOID").size()
    summary = summary.fillna(0).astype(int).sort_index().reset_index()
    summary = add_response_metrics(summary, response_summary)
    summary = add_burglary_rate(summary, housing_path)
    summary.to_csv(output_dir / "public_safety_tract.csv", index=False)


def apply_mvc_geocodes(mvc: pd.DataFrame, geocodes_path: Path | None = None) -> pd.DataFrame:
    """Fill unusable coordinates from accepted address/intersection matches.

    Preserve originals. Required CSV fields are validated below; acceptance
    requires address-context and match-quality review (see geocoding.md).
    """
    result = mvc.copy()
    valid = result["LATITUDE"].between(40, 41.5) & result["LONGITUDE"].between(-75, -73)
    result["LOCATION_LATITUDE"] = result["LATITUDE"].where(valid)
    result["LOCATION_LONGITUDE"] = result["LONGITUDE"].where(valid)
    result["COORDINATE_SOURCE"] = "unresolved"
    result.loc[valid, "COORDINATE_SOURCE"] = "original"
    if geocodes_path is None:
        return result

    matches = pd.read_csv(geocodes_path, dtype={"COLLISION_ID": "string"})
    fields = ["geocoded_latitude", "geocoded_longitude", "match_status",
              "match_precision", "geocoder_source"]
    missing = sorted(set(["COLLISION_ID", *fields]) - set(matches.columns))
    if missing:
        raise ValueError(f"MVC geocoding file is missing columns: {missing}")
    if matches["COLLISION_ID"].isna().any() or matches["COLLISION_ID"].duplicated().any():
        raise ValueError("MVC geocoding file must contain unique, nonmissing collision IDs.")
    for column in ["geocoded_latitude", "geocoded_longitude"]:
        matches[column] = pd.to_numeric(matches[column], errors="coerce")
    accepted = matches["match_status"].eq("accepted")
    reliable = (
        matches["match_precision"].isin(["address", "intersection"])
        & matches["geocoded_latitude"].between(40, 41.5)
        & matches["geocoded_longitude"].between(-75, -73)
        & matches["geocoder_source"].fillna("").str.strip().ne("")
    )
    if (accepted & ~reliable).any():
        raise ValueError("Accepted MVC matches require valid coordinates, address/intersection precision, and a source.")
    # Match IDs without changing source ID types or row order.
    lookup = matches.set_index("COLLISION_ID")
    for column in fields:
        result[column] = result["COLLISION_ID"].astype("string").map(lookup[column])
    recovered = ~valid & result["match_status"].eq("accepted")
    result.loc[recovered, "LOCATION_LATITUDE"] = result.loc[recovered, "geocoded_latitude"]
    result.loc[recovered, "LOCATION_LONGITUDE"] = result.loc[recovered, "geocoded_longitude"]
    result.loc[recovered, "COORDINATE_SOURCE"] = "geocoded"
    print(f"MVC missing/unusable source coordinates: {(~valid).sum():,}")
    print(f"MVC recovered from accepted local geocodes: {recovered.sum():,}")
    print(f"MVC still unresolved: {result['COORDINATE_SOURCE'].eq('unresolved').sum():,}")
    return result


def clean_mvc(input_path: Path, geocodes_path: Path | None = None) -> pd.DataFrame:
    """Clean Motor Vehicle Collisions data for Public Safety analysis."""
    print(f"\nReading MVC data: {input_path}")
    # Missing borough does not imply missing coordinates; select CB3 spatially.
    mvc = read_analysis_period(input_path, "CRASH DATE")

    casualty_cols = [
        "NUMBER OF PERSONS INJURED",
        "NUMBER OF PEDESTRIANS INJURED",
        "NUMBER OF CYCLIST INJURED",
        "NUMBER OF PERSONS KILLED",
        "NUMBER OF PEDESTRIANS KILLED",
        "NUMBER OF CYCLIST KILLED",
    ]
    required_cols = [
        "CRASH DATE",
        "BOROUGH",
        "LATITUDE",
        "LONGITUDE",
        *casualty_cols,
        "COLLISION_ID",
    ]
    missing = [col for col in required_cols if col not in mvc.columns]
    if missing:
        raise ValueError(f"MVC file is missing required columns: {missing}")

    mvc["CRASH DATE"] = pd.to_datetime(mvc["CRASH DATE"], format="%m/%d/%Y", errors="coerce")

    mvc["BOROUGH"] = normalize_upper(mvc["BOROUGH"])

    for col in casualty_cols:
        mvc[col] = pd.to_numeric(mvc[col], errors="coerce").fillna(0)

    mvc["LATITUDE"] = pd.to_numeric(mvc["LATITUDE"], errors="coerce")
    mvc["LONGITUDE"] = pd.to_numeric(mvc["LONGITUDE"], errors="coerce")

    mvc["PED_CYCLIST_INJURY_CRASH"] = (
        (mvc["NUMBER OF PEDESTRIANS INJURED"] > 0)
        | (mvc["NUMBER OF CYCLIST INJURED"] > 0)
    )

    # Fallback metric if the preferred metric is too sparse.
    mvc["ANY_INJURY_CRASH"] = mvc["NUMBER OF PERSONS INJURED"] > 0

    # Pedestrian/cyclist fatalities take precedence in the pie slices.
    mvc["PED_CYCLIST_FATAL_CRASH"] = (
        mvc["NUMBER OF PEDESTRIANS KILLED"].gt(0)
        | mvc["NUMBER OF CYCLIST KILLED"].gt(0)
    )
    mvc["PED_CYCLIST_NONFATAL_INJURY_CRASH"] = (
        mvc["PED_CYCLIST_INJURY_CRASH"] & ~mvc["PED_CYCLIST_FATAL_CRASH"]
    )
    mvc["PED_CYCLIST_INJURY_OR_FATAL_CRASH"] = (
        mvc["PED_CYCLIST_INJURY_CRASH"] | mvc["PED_CYCLIST_FATAL_CRASH"]
    )
    mvc["ANY_INJURY_OR_FATAL_CRASH"] = (
        mvc["ANY_INJURY_CRASH"] | mvc["NUMBER OF PERSONS KILLED"].gt(0)
    )

    keep_cols = [
        "COLLISION_ID",
        "CRASH DATE",
        "BOROUGH",
        "LATITUDE",
        "LONGITUDE",
        *casualty_cols,
        "PED_CYCLIST_INJURY_CRASH",
        "ANY_INJURY_CRASH",
        "PED_CYCLIST_FATAL_CRASH",
        "PED_CYCLIST_NONFATAL_INJURY_CRASH",
        "PED_CYCLIST_INJURY_OR_FATAL_CRASH",
        "ANY_INJURY_OR_FATAL_CRASH",
    ]

    mvc_clean = mvc[keep_cols].copy()
    mvc_clean = mvc_clean.drop_duplicates(subset=["COLLISION_ID"])

    return apply_mvc_geocodes(mvc_clean, geocodes_path)


def clean_nypd(input_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean NYPD Complaint Historic data for violent crime and burglary analysis."""
    print(f"\nReading NYPD data: {input_path}")
    nypd = read_analysis_period(input_path, "CMPLNT_FR_DT", offenses=[*VIOLENT_OFFENSES, "BURGLARY"])

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

    nypd["BORO_NM"] = normalize_upper(nypd["BORO_NM"])

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

    violent = nypd[
        nypd["OFNS_DESC"].isin(VIOLENT_OFFENSES)
        & nypd["LAW_CAT_CD"].eq("FELONY")
    ].copy()

    complaint_keep = required_cols.copy()
    violent = violent[complaint_keep].copy()
    violent = violent.drop_duplicates(subset=["CMPLNT_NUM"])

    # Include attempts at residential premises.
    burglary = nypd[
        nypd["OFNS_DESC"].eq("BURGLARY")
        & nypd["LAW_CAT_CD"].eq("FELONY")
        & nypd["PREM_TYP_DESC"].isin([
            "RESIDENCE - APT. HOUSE", "RESIDENCE - PUBLIC HOUSING", "RESIDENCE-HOUSE",
        ])
    ].copy()

    # Keep the optional burglary field in its original column position.
    burglary_keep = complaint_keep.copy()
    if "LOC_OF_OCCUR_DESC" in burglary.columns:
        burglary_keep.insert(burglary_keep.index("PREM_TYP_DESC"), "LOC_OF_OCCUR_DESC")

    burglary = burglary[burglary_keep].copy()
    burglary = burglary.drop_duplicates(subset=["CMPLNT_NUM"])

    return violent, burglary


def clean_nypd_response_times(input_path: Path, output_dir: Path) -> pd.DataFrame:
    """Write clean CB3 call entries and return response metrics for the unified tract table."""
    calls = pd.read_csv(input_path, low_memory=False)
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
    # Include the entire END_DATE via an exclusive next-day cutoff.
    end_exclusive = pd.Timestamp(END_DATE) + pd.Timedelta(days=1)
    missing_entry = calls["add_ts"].isna()
    outside_period = calls["add_ts"].notna() & (
        calls["add_ts"].lt(START_DATE) | calls["add_ts"].ge(end_exclusive)
    )
    print(f"NYPD response input: {len(calls):,} entries")
    print(f"Excluded missing/unparseable entry timestamps: {missing_entry.sum():,}")
    print(f"Excluded entry timestamps outside analysis period: {outside_period.sum():,}")
    calls = calls[calls["add_ts"].ge(START_DATE) & calls["add_ts"].lt(end_exclusive)].copy()
    # Disjoint in-period exclusions, before spatial matching; malformed times count as missing.
    print(f"In-period entries before response filtering: {len(calls):,}")
    print(f"Excluded missing/unparseable arrival timestamps: {calls['arrivd_ts'].isna().sum():,}")
    print(f"Excluded equal entry/arrival timestamps: {calls['arrivd_ts'].eq(calls['add_ts']).sum():,}")
    print(f"Excluded negative response intervals: {calls['arrivd_ts'].lt(calls['add_ts']).sum():,}")
    calls = calls[
        calls["arrivd_ts"].notna() & calls["arrivd_ts"].ne(calls["add_ts"])
    ].copy()
    calls["response_minutes"] = (calls["arrivd_ts"] - calls["add_ts"]).dt.total_seconds() / 60
    calls = calls[calls["response_minutes"].gt(0)].copy()
    print(f"Retained positive response intervals before spatial matching: {len(calls):,}")
    for column in ["latitude", "longitude"]:
        calls[column] = pd.to_numeric(calls[column], errors="coerce")
    tracts = gpd.read_file(PROJECT_ROOT / "data/raw/Geography/cb3_2020_census_tracts.geojson").rename(columns={"geoid": "GEOID"})
    tracts = tracts[["GEOID", "geometry"]].to_crs(4326)
    matched = join_cb3(calls, "latitude", "longitude", tracts)
    print(f"Excluded entries without a CB3 tract match: {len(calls) - len(matched):,}")
    output_dir.mkdir(parents=True, exist_ok=True)
    matched.to_csv(output_dir / "nypd_response_time_clean.csv", index=False)
    print(f"Saved {len(matched):,} clean response entries: {output_dir / 'nypd_response_time_clean.csv'}")
    stats = matched.groupby("GEOID")["response_minutes"].agg(
        nypd_entry_to_arrival_median_minutes="median", nypd_response_valid_entries="count")
    result = tracts[["GEOID"]].merge(stats, on="GEOID", how="left")
    result["nypd_response_valid_entries"] = result["nypd_response_valid_entries"].fillna(0).astype(int)
    return result


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
        "--calls-csv",
        type=Path,
        default=None,
        help=(
            "Previously downloaded NYPD Calls CSV. If omitted, the script checks "
            "the standard local filenames in data/raw/Public Safety."
        ),
    )
    parser.add_argument("--housing-units", type=Path, default=DEFAULT_HOUSING_PATH,
                        help="Local ACS B25001 CSV or prepared GEOID,total_housing_units CSV; no API fetch.")
    parser.add_argument("--mvc-geocodes", type=Path, default=None,
                        help="Reviewed local collision geocoding CSV; original valid coordinates take precedence.")
    parser.add_argument("--calls-only", action="store_true", help="Clean the saved calls snapshot without rereading MVC/NYPD complaint files.")
    args = parser.parse_args()

    summary_path = args.output_dir / "public_safety_tract.csv"
    if args.calls_only and not summary_path.is_file():
        parser.error("Run the full build first: --calls-only updates an existing public_safety_tract.csv.")
    response_summary = None
    if args.calls_csv is not None:
        calls_path = args.calls_csv
    else:
        calls_path = first_existing_path(DEFAULT_CALLS_CANDIDATES)

    if calls_path is not None:
        if not calls_path.is_file():
            parser.error(f"NYPD calls CSV not found: {calls_path}")
        print(f"Reusing local NYPD calls file: {calls_path}")
        response_summary = clean_nypd_response_times(calls_path, args.output_dir)
    else:
        print(
            "NYPD response-time step skipped: no local calls CSV found. "
            "Place the saved calls file in data/raw/Public Safety or pass --calls-csv PATH."
        )

    if args.calls_only:
        if calls_path is None:
            parser.error("No local NYPD Calls file found. Provide --calls-csv PATH.")
        summary = pd.read_csv(summary_path, dtype={"GEOID": "string"})
        add_response_metrics(summary, response_summary).to_csv(summary_path, index=False)
        print(f"Updated response metrics: {summary_path}")
        return

    for input_path in (args.mvc, args.nypd):
        if not input_path.is_file():
            parser.error(f"Input CSV not found: {input_path}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    mvc = clean_mvc(args.mvc, args.mvc_geocodes)
    violent, burglary = clean_nypd(args.nypd)
    build_cb3_outputs(mvc, violent, burglary, args.output_dir, response_summary, args.housing_units)
    print(f"\nBuild complete. CB3 event files and tract counts saved in {args.output_dir}")



if __name__ == "__main__":
    main()
