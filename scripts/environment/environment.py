"""Build the CB3 Environmental Conditions tract-level metrics table.
It uses only local project data and writes:
    data/clean/environment.csv
"""
#%%
from pathlib import Path
import datetime
import sys

# Make src/ importable when running interactively in VS Code (# %% cells),
# where the working directory may be the script folder rather than project root.
_project_dir = Path(__file__).resolve().parents[2]
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))

import numpy as np
import pandas as pd
import geopandas as gpd

from src.cb3_utils import (
    assign_points_to_cb3_tract,
    load_cb3_tract_universe,
)

# Define project paths and create the clean-data directory.
PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Environment"
GEOGRAPHY_DIR = PROJECT_DIR / "data" / "raw" / "Geography"
RELATIONSHIP_DIR = GEOGRAPHY_DIR / "GeographicRelationshipFiles"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "environment.csv"
LOG_PATH = CLEAN_DIR / "environment_log.txt"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)
#%%


# Establish the 31-tract universe
tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS = load_cb3_tract_universe(
    PROJECT_DIR
)
print(f"Validated CB3 tract universe: {len(tracts)} tracts")


# Wrap the domain-aware point-assignment helper so call sites below stay unchanged.
def _assign(frame, lon_col, lat_col, source_tract_col=None):
    return assign_points_to_cb3_tract(
        frame, lon_col, lat_col, cb3_tract_geometry, CB3_TRACT_CODES, source_tract_col
    )
#%%


# Environmental Justice Areas (2024)

# The EJ Areas file is keyed by 2010 GEOID10 and covers all of NYC, with every
# tract flagged Designated/Not Designated. Crosswalk each 2020 CB3 tract to its
# primary contributing 2010 tract (largest population-proportion source), then
# look up that 2010 tract's designation.
crosswalk_2010_2020 = pd.read_excel(
    RELATIONSHIP_DIR / "nyc_2010to2020_split_census_tract_pop_proportion.xlsx"
)
crosswalk_2010_2020["GEOID2010"] = (
    "36" + crosswalk_2010_2020["FIPSCT2010"].astype(str).str.zfill(9)
)
crosswalk_2010_2020["GEOID2020"] = (
    "36" + crosswalk_2010_2020["FIPSCT2020"].astype(str).str.zfill(9)
)
cb3_crosswalk_2010_2020 = crosswalk_2010_2020[
    crosswalk_2010_2020["GEOID2020"].isin(CB3_GEOIDS)
].copy()
primary_source_tract = cb3_crosswalk_2010_2020.loc[
    cb3_crosswalk_2010_2020.groupby("GEOID2020")["Prptn10t20"].idxmax()
][["GEOID2020", "GEOID2010"]].rename(columns={"GEOID2020": "GEOID"})

ej_areas = pd.read_csv(RAW_DIR / "EJ_Areas_1364276073866452550.csv")
ej_areas["GEOID10"] = ej_areas["GEOID10"].astype(str)
designated_2010_geoids = set(
    ej_areas.loc[ej_areas["DAC_Design"].eq("Designated as DAC"), "GEOID10"]
)

ej_area_metrics = primary_source_tract.copy()
ej_area_metrics["ej_area_flag"] = (
    ej_area_metrics["GEOID2010"].isin(designated_2010_geoids).astype(int)
)
ej_area_metrics = ej_area_metrics[["GEOID", "ej_area_flag"]]

#%%
# Rodent activity (311 service requests)

# File is pre-filtered to CB3 rodent complaints but lacks a Census Tract column,
# so assign each record to a tract by spatially joining its coordinates.
rodent_311 = pd.read_csv(
    RAW_DIR / "311_Service_Requests_from_2020_to_Present_20260428.csv",
    low_memory=False,
)
rodent_311 = rodent_311[
    rodent_311["Borough"].eq("MANHATTAN")
    & rodent_311["Community Board"].eq("03 MANHATTAN")
    & rodent_311["Problem (formerly Complaint Type)"].eq("Rodent")
].copy()
rodent_311["GEOID"] = _assign(rodent_311, "Longitude", "Latitude")
rodent_311_unallocated_count = int(rodent_311["GEOID"].isna().sum())
rodent_311_mapped = rodent_311[rodent_311["GEOID"].isin(CB3_GEOIDS)].copy()

rodent_311_metrics = (
    rodent_311_mapped.assign(rodent_311_calls=1)
    .groupby("GEOID", as_index=False)["rodent_311_calls"]
    .sum()
)
#%%


# Rodent inspections (DOHMH)

# File is pre-filtered to CB3 Manhattan (all rows are BOROUGH=Manhattan, COMMUNITY BOARD=3).
# Keep only inspections with confirmed rat activity: "Rat Activity" and
# "Failed for Rat Activity and Other Reason" both mean rats were found on-site.
# "Bait applied" is a treatment visit, not a new confirmation, so it is excluded.
ACTIVE_RODENT_RESULTS = {"Rat Activity", "Failed for Rat Activity and Other Reason"}
rodent_insp = pd.read_csv(
    RAW_DIR / "Rodent_Inspection_20260428.csv",
    low_memory=False,
)
rodent_insp_active = rodent_insp[rodent_insp["RESULT"].isin(ACTIVE_RODENT_RESULTS)].copy()
rodent_insp_active["GEOID"] = _assign(
    rodent_insp_active, "LONGITUDE", "LATITUDE", "CENSUS TRACT"
)
rodent_insp_unallocated_count = int(rodent_insp_active["GEOID"].isna().sum())
rodent_insp_mapped = rodent_insp_active[rodent_insp_active["GEOID"].isin(CB3_GEOIDS)].copy()

rodent_insp_metrics = (
    rodent_insp_mapped.assign(rodent_active_inspections=1)
    .groupby("GEOID", as_index=False)["rodent_active_inspections"]
    .sum()
)
#%%


# Indoor environmental complaints (DOHMH)

# Has both coordinates and a Census Tract field; coordinates are the primary
# assignment method and the source tract is used only as a fallback.
# Note: the staged file here is the 20260420 vintage; the Housing domain uses
# the 20260428 vintage (15 additional records). Flag if vintages are reconciled.
indoor = pd.read_csv(
    RAW_DIR / "DOHMH_Indoor_Environmental_Complaints_20260420.csv",
    low_memory=False,
)
indoor = indoor[
    indoor["Incident_Address_Borough"].str.upper().eq("MANHATTAN")
    & indoor["Community Board"].eq(3)
    & indoor["Deleted"].ne("Yes")
].copy()
indoor["GEOID"] = _assign(indoor, "Longitude", "Latitude", "Census Tract")
indoor_unallocated_count = int(indoor["GEOID"].isna().sum())
indoor_mapped = indoor[indoor["GEOID"].isin(CB3_GEOIDS)].copy()

indoor_metrics = (
    indoor_mapped.assign(
        indoor_environmental_complaints=1,
        indoor_air_quality_complaints=indoor_mapped["Complaint_Type_311"]
        .str.upper()
        .eq("INDOOR AIR QUALITY")
        .astype(int),
        mold_complaints=indoor_mapped["Complaint_Type_311"]
        .str.upper()
        .eq("MOLD")
        .astype(int),
        asbestos_complaints=indoor_mapped["Complaint_Type_311"]
        .str.upper()
        .eq("ASBESTOS")
        .astype(int),
        indoor_sewage_complaints=indoor_mapped["Complaint_Type_311"]
        .str.upper()
        .eq("INDOOR SEWAGE")
        .astype(int),
    )
    .groupby("GEOID", as_index=False)[
        [
            "indoor_environmental_complaints",
            "indoor_air_quality_complaints",
            "mold_complaints",
            "asbestos_complaints",
            "indoor_sewage_complaints",
        ]
    ]
    .sum()
)
#%%


# Assemble and write the tract table

# Left-join every tract-level metric to the 31-row base. Counts are filled with
# zero for tracts with no matching records.
metric_frames = [
    ej_area_metrics,
    rodent_311_metrics,
    rodent_insp_metrics,
    indoor_metrics,
]

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
for metric_frame in metric_frames:
    clean = clean.merge(metric_frame, on="GEOID", how="left", validate="one_to_one")

count_columns = [
    "rodent_311_calls",
    "rodent_active_inspections",
    "indoor_environmental_complaints",
    "indoor_air_quality_complaints",
    "mold_complaints",
    "asbestos_complaints",
    "indoor_sewage_complaints",
]
clean[count_columns] = clean[count_columns].fillna(0)

# Compute East Village share of rodent calls for the flag note below.
ev_calls = int(clean.loc[clean["nta_name"].eq("East Village"), "rodent_311_calls"].sum())
total_calls = int(clean["rodent_311_calls"].sum())
ev_share = f"{ev_calls / total_calls:.1%}" if total_calls > 0 else "n/a"

# Write geography and data-availability notes to a sidecar log instead of
# repeating identical text across all 31 rows.
log_lines = [
    "CB3 Environmental Conditions — Build Log",
    f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"Output:    {OUTPUT_PATH}",
    "",
    "=== Tract Universe ===",
    "Official DCP 2020 tract-NTA-CDTA crosswalk; CDTACode=MN03",
    "",
    "=== Geography ===",
    "EJ Areas:              2010 GEOID10 crosswalked to 2020 tracts via "
    "nyc_2010to2020_split_census_tract_pop_proportion.xlsx; each 2020 tract "
    "uses its primary (largest population-proportion) 2010 source tract.",
    f"Rodent 311 calls:      {rodent_311_unallocated_count} CB3-labelled records not allocated to a tract",
    f"Rodent inspections:    {rodent_insp_unallocated_count} active-result records not allocated to a tract",
    f"Indoor env complaints: {indoor_unallocated_count} CB3-labelled records not allocated to a tract",
    "",
    "=== Flag for Review ===",
    f"Rodent 311 NTA share:  Tract-level aggregation gives East Village {ev_share} of "
    "mapped CB3 rodent calls, vs. the concept doc's cited ~48%. Re-check before "
    "using the NTA-level disparity narrative with this build.",
    "",
    "=== Data Availability ===",
    "Sanitation cleanliness: Scorecard ratings are reported by cleaning section "
    "(MN031-MN034), not census tract, and no cleaning-section boundary file is "
    "available locally to spatially join to tracts. Not yet included.",
    "Heat Vulnerability Index: reported by ZIP/ZCTA, not census tract. Deferred "
    "per request; not yet included.",
]
LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
print(f"Wrote build log to {LOG_PATH}")

# Sort columns and rows deterministically, then write the requested CSV.
clean = clean.sort_values("GEOID").reset_index(drop=True)
assert len(clean) == 31
assert clean["GEOID"].is_unique
assert clean["GEOID"].str.fullmatch(r"\d{11}").all()

clean.to_csv(OUTPUT_PATH, index=False)
print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")


# Validation summary

validation = pd.Series(
    {
        "tract_rows": len(clean),
        "unique_geoids": clean["GEOID"].nunique(),
        "ej_area_tracts": clean["ej_area_flag"].sum(),
        "mapped_rodent_311_calls": clean["rodent_311_calls"].sum(),
        "mapped_rodent_active_inspections": clean["rodent_active_inspections"].sum(),
        "mapped_indoor_env_complaints": clean["indoor_environmental_complaints"].sum(),
    }
)
print(validation.to_frame("value").to_string())
