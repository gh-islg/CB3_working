"""Build the CB3 Environmental Conditions tract-level metrics table.
It uses only local project data and writes:
    data/clean/environment_tract.csv
    data/clean/environment_rodent_inspection_points.csv
    data/clean/environment_tree_points.csv
    data/clean/environment_nycha_building_points.csv
    data/clean/environment_hurricane_evacuation_zones.geojson
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
    add_polygon_centroids,
    assign_points_to_cb3_tract,
    load_cb3_tract_universe,
)

# Define project paths and create the clean-data directory.
PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Environment"
GEOGRAPHY_DIR = PROJECT_DIR / "data" / "raw" / "Geography"
RELATIONSHIP_DIR = GEOGRAPHY_DIR / "GeographicRelationshipFiles"
HOUSING_RAW_DIR = PROJECT_DIR / "data" / "raw" / "Housing and Affordability"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "environment_tract.csv"
LOG_PATH = CLEAN_DIR / "environment_log.txt"
RODENT_INSPECTION_POINTS_PATH = CLEAN_DIR / "environment_rodent_inspection_points.csv"
TREE_POINTS_PATH = CLEAN_DIR / "environment_tree_points.csv"
NYCHA_BUILDING_POINTS_PATH = CLEAN_DIR / "environment_nycha_building_points.csv"
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


def build_building_points(frame, bbl_col, address_col, lon_col, lat_col, value_cols):
    """Aggregate point-event records (e.g. inspections) to one row per
    building (BBL) with summed metric counts and native coordinates.

    Records missing a BBL are dropped from the building-level export (they
    still contribute to the tract-level counts computed separately), since
    there is no reliable building key to group them on.
    """
    with_bbl = frame[
        frame[bbl_col].notna()
        & frame[lat_col].notna()
        & frame[lon_col].notna()
        & frame["GEOID"].isin(CB3_GEOIDS)
    ].copy()
    with_bbl[bbl_col] = with_bbl[bbl_col].astype("int64")
    points = (
        with_bbl.groupby(bbl_col, as_index=False)
        .agg(
            **{
                "GEOID": ("GEOID", "first"),
                "address": (address_col, "first"),
                "longitude": (lon_col, "first"),
                "latitude": (lat_col, "first"),
                **{col: (col, "sum") for col in value_cols},
            }
        )
        .rename(columns={bbl_col: "bbl"})
    )
    return points
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
rodent_insp["INSPECTION_DATE"] = pd.to_datetime(
    rodent_insp["INSPECTION_DATE"], format="%m/%d/%Y %I:%M:%S %p"
)
rodent_insp_active = rodent_insp[rodent_insp["RESULT"].isin(ACTIVE_RODENT_RESULTS)].copy()

# Restrict to the latest 5 years of data (relative to the most recent
# inspection in the file) instead of the full 2002-2026 history, so the
# metric reflects recent conditions rather than two decades of accumulation.
rodent_insp_latest_date = rodent_insp_active["INSPECTION_DATE"].max()
rodent_insp_cutoff_date = rodent_insp_latest_date - pd.DateOffset(years=5)
rodent_insp_active = rodent_insp_active[
    rodent_insp_active["INSPECTION_DATE"] >= rodent_insp_cutoff_date
].copy()

rodent_insp_active["GEOID"] = _assign(
    rodent_insp_active, "LONGITUDE", "LATITUDE", "CENSUS TRACT"
)
rodent_insp_unallocated_count = int(rodent_insp_active["GEOID"].isna().sum())
rodent_insp_mapped = rodent_insp_active[rodent_insp_active["GEOID"].isin(CB3_GEOIDS)].copy()
rodent_insp_mapped["rodent_active_inspections"] = 1

rodent_insp_metrics = (
    rodent_insp_mapped.groupby("GEOID", as_index=False)["rodent_active_inspections"]
    .sum()
)

# Building-level export for point/bubble maps: one row per building with a
# confirmed-activity inspection, keeping its own coordinates rather than
# collapsing to the tract centroid.
rodent_insp_points = build_building_points(
    rodent_insp_mapped, "BBL", "LOCATION", "LONGITUDE", "LATITUDE",
    ["rodent_active_inspections"],
)
rodent_insp_points.to_csv(RODENT_INSPECTION_POINTS_PATH, index=False)
print(f"Wrote {len(rodent_insp_points)} rodent inspection building points to {RODENT_INSPECTION_POINTS_PATH}")
#%%

# Street trees (NYC Parks 2015 TreesCount census)

# File is citywide; filter to CB3 Manhattan (community board 103) and keep
# only Alive trees, since Dead/Stump records have no health rating to color
# by. Coordinates are complete for the CB3 subset, so no source-tract
# fallback is needed for the spatial join.
trees = pd.read_csv(
    RAW_DIR / "2015_Street_Tree_Census_-_Tree_Data_20260630.csv",
    low_memory=False,
)
trees_cb3 = trees[
    trees["community board"].eq(103)
    & trees["borough"].eq("Manhattan")
    & trees["status"].eq("Alive")
].copy()
trees_cb3["GEOID"] = _assign(trees_cb3, "longitude", "latitude")
tree_unallocated_count = int(trees_cb3["GEOID"].isna().sum())
trees_cb3_mapped = trees_cb3[trees_cb3["GEOID"].isin(CB3_GEOIDS)].copy()

tree_points = trees_cb3_mapped[
    ["tree_id", "address", "GEOID", "latitude", "longitude", "health", "spc_common"]
].copy()
tree_points.to_csv(TREE_POINTS_PATH, index=False)
print(f"Wrote {len(tree_points)} alive street tree points to {TREE_POINTS_PATH}")
#%%

# NYCHA residential buildings (for the hurricane evacuation zone map)

# Sourced from the Housing & Affordability domain's NYCHA code-violations file
# (the only local file with building-level NYCHA coordinates), since there is
# no dedicated NYCHA development roster in the raw data. Each violation is
# tied to a specific residential building, so deduplicating on BBL gives one
# point per actual NYCHA residential building rather than a development's
# non-residential amenity sites (community centers, senior centers, etc. —
# see NYCHA_Facilities_and_Service_Centers, which is not building-level).
nycha = pd.read_csv(
    HOUSING_RAW_DIR / "Housing_Maintenance_Code_Violations_NYCHA_properties_20260420.csv",
    low_memory=False,
)
nycha = nycha[
    nycha["Primary Borough Name"].eq("MANHATTAN")
    & nycha["Community Board"].eq(103)
].copy()
nycha["GEOID"] = _assign(nycha, "Longitude", "Latitude", "Census Tract (2020)")
nycha_unallocated_count = int(nycha["GEOID"].isna().sum())
nycha_mapped = nycha[nycha["GEOID"].isin(CB3_GEOIDS)].copy()
nycha_mapped["address"] = (
    nycha_mapped["Primary House Number"].astype(str).str.strip()
    + " " + nycha_mapped["Primary Street Name"].astype(str).str.strip()
)

nycha_building_points = (
    nycha_mapped.groupby("BBL", as_index=False)
    .agg(
        development_name=("Development Name", "first"),
        address=("address", "first"),
        GEOID=("GEOID", "first"),
        latitude=("Latitude", "first"),
        longitude=("Longitude", "first"),
    )
)
nycha_building_points.to_csv(NYCHA_BUILDING_POINTS_PATH, index=False)
print(f"Wrote {len(nycha_building_points)} NYCHA residential building points to {NYCHA_BUILDING_POINTS_PATH}")
#%%

# Hurricane Evacuation Zones (NYC OEM)

# Citywide file covering the whole coastline (7MB, hundreds of polygon parts
# per zone), so clip to the CB3 tract boundary here rather than embedding the
# full citywide geometry in every map that uses it.
evac_zones = gpd.read_file(RAW_DIR / "Hurricane_Evacuation_Zones_20260708.geojson")
evac_zones = evac_zones.rename(columns={"hurricane_": "evacuation_zone"})[
    ["evacuation_zone", "geometry"]
]
evac_zones["evacuation_zone"] = evac_zones["evacuation_zone"].astype(str)

cb3_boundary = cb3_tract_geometry.to_crs("EPSG:2263").geometry.union_all()
evac_zones_proj = evac_zones.to_crs("EPSG:2263")
evac_zones_proj["geometry"] = evac_zones_proj.geometry.intersection(cb3_boundary)
evac_zones_clipped = evac_zones_proj[~evac_zones_proj.geometry.is_empty].to_crs("EPSG:4326")

EVAC_ZONES_OUTPUT_PATH = CLEAN_DIR / "environment_hurricane_evacuation_zones.geojson"
evac_zones_clipped.to_file(EVAC_ZONES_OUTPUT_PATH, driver="GeoJSON")
print(
    f"Wrote {len(evac_zones_clipped)} CB3-clipped evacuation zones to {EVAC_ZONES_OUTPUT_PATH}"
)
#%%

# Sanitation cleanliness (DSNY scorecard — section level)

# Scorecard ratings are published at the DSNY cleaning section level (MN031-MN034),
# not census tract. Average the latest 12 calendar months of available data,
# skipping null rows (September 2023 is present but fully null).
SECTIONS_OUTPUT_PATH = CLEAN_DIR / "environment_sections.csv"

scorecard = pd.read_csv(RAW_DIR / "Scorecard_Ratings_20260420.csv", low_memory=False)
scorecard_cb3 = scorecard[
    scorecard["Borough"].eq("Manhattan") & scorecard["Community Board"].eq(3)
].copy()
scorecard_cb3["date"] = pd.to_datetime(scorecard_cb3["Month"], format="%Y / %m")

latest_scorecard_date = scorecard_cb3["date"].max()
scorecard_cutoff = latest_scorecard_date - pd.DateOffset(months=11)
scorecard_recent = scorecard_cb3[scorecard_cb3["date"] >= scorecard_cutoff].copy()

scorecard_n_months = int(
    scorecard_recent.groupby("Cleaning Section")["Acceptable Streets %"]
    .apply(lambda s: s.notna().sum())
    .min()
)

scorecard_section_avgs = (
    scorecard_recent.groupby("Cleaning Section", as_index=False)[
        ["Acceptable Streets %", "Acceptable Sidewalks %"]
    ]
    .mean(numeric_only=True)
    .rename(columns={
        "Cleaning Section": "section",
        "Acceptable Streets %": "avg_acceptable_streets_pct",
        "Acceptable Sidewalks %": "avg_acceptable_sidewalks_pct",
    })
)
scorecard_section_avgs = scorecard_section_avgs.round(1)

scorecard_section_avgs.to_csv(SECTIONS_OUTPUT_PATH, index=False)
print(
    f"Wrote {len(scorecard_section_avgs)} cleaning sections "
    f"({scorecard_n_months} non-null months averaged) to {SECTIONS_OUTPUT_PATH}"
)
#%%


# Assemble and write the tract table

# Left-join every tract-level metric to the 31-row base. Counts are filled with
# zero for tracts with no matching records.
metric_frames = [
    ej_area_metrics,
    rodent_insp_metrics
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

count_columns = ["rodent_active_inspections"]
clean[count_columns] = clean[count_columns].fillna(0)

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
    f"Rodent inspections:    {rodent_insp_unallocated_count} active-result records not allocated to a tract; "
    f"restricted to latest 5 years ({rodent_insp_cutoff_date.strftime('%Y-%m-%d')} to "
    f"{rodent_insp_latest_date.strftime('%Y-%m-%d')})",
    f"Street trees:          {tree_unallocated_count} Alive CB3-board records not allocated to a tract",
    f"NYCHA buildings:       {nycha_unallocated_count} MN03 NYCHA violation records not allocated to a tract; "
    f"{len(nycha_building_points)} unique residential buildings (by BBL) from violation records",
    "",
    "=== Data Availability ===",
    f"Sanitation cleanliness: Reported at cleaning section level (MN031-MN034). "
    f"Averaged over {scorecard_n_months} non-null months "
    f"({scorecard_cutoff.strftime('%Y-%m')} to {latest_scorecard_date.strftime('%Y-%m')}). "
    f"Written to environment_sections.csv; not included in tract-level {OUTPUT_PATH.name}.",
]
LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
print(f"Wrote build log to {LOG_PATH}")

# Sort columns and rows deterministically, then write the requested CSV.
clean = clean.sort_values("GEOID").reset_index(drop=True)
assert len(clean) == 31
assert clean["GEOID"].is_unique
assert clean["GEOID"].str.fullmatch(r"\d{11}").all()

# Attach each tract's polygon centroid so tract-level metrics (e.g. EJ Area
# status) can still be placed as a single point on point/bubble map layers,
# in the absence of building-level coordinates.
clean = add_polygon_centroids(
    clean, cb3_tract_geometry, "GEOID",
    lat_col="tract_centroid_latitude", lon_col="tract_centroid_longitude",
)

clean.to_csv(OUTPUT_PATH, index=False)
print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {OUTPUT_PATH}")


# Validation summary

validation = pd.Series(
    {
        "tract_rows": len(clean),
        "unique_geoids": clean["GEOID"].nunique(),
        "ej_area_tracts": clean["ej_area_flag"].sum(),
        "mapped_rodent_active_inspections": clean["rodent_active_inspections"].sum(),
        "mapped_alive_trees": len(tree_points),
        "nycha_residential_buildings": len(nycha_building_points),
        "cb3_evacuation_zone_parts": len(evac_zones_clipped),
    }
)
print(validation.to_frame("value").to_string())
