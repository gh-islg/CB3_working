"""Build the CB3 Housing & Affordability tract-level metrics table.
It uses only local project data and writes:
    data/clean/housing_and_affordability.csv
"""

# Import the packages used for tabular, spatial, PDF, and file processing.
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
from pypdf import PdfReader

from src.cb3_utils import (
    assign_points_to_cb3_tract,
    extract_year,
    load_cb3_acs,
    load_cb3_tract_universe,
    percent,
)

# Define project paths and create the clean-data directory.
PROJECT_DIR = Path(__file__).resolve().parents[2]
DOCS_DIR = PROJECT_DIR / "docs"
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Housing and Affordability"
GEOGRAPHY_DIR = PROJECT_DIR / "data" / "raw" / "Geography"
RELATIONSHIP_DIR = GEOGRAPHY_DIR / "GeographicRelationshipFiles"
FURMAN_DIR = RAW_DIR / "FC_Subsidized_Housing_Database_2025-05-13"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = CLEAN_DIR / "housing_and_affordability.csv"
LOG_PATH = CLEAN_DIR / "housing_and_affordability_log.txt"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)


# Project context

# Read the reference sheet with its Windows-1252 encoding and display Domain 1.
reference = pd.read_csv(
    DOCS_DIR / "CB3_Data_Reference_by_Domain_v4.csv",
    encoding="cp1252",
    header=None,
)
domain_1_reference = reference.iloc[32:49].copy()
print(f"Loaded Housing & Affordability reference rows: {len(domain_1_reference)}")

# Extract the Housing & Affordability section from the concept PDF for auditability.
concept_reader = PdfReader(
    DOCS_DIR / "proposed_concept_for_CB3_equity_report_v2.pdf"
)
concept_text = "\n".join(page.extract_text() or "" for page in concept_reader.pages)
start = concept_text.find("Domain 1 — Housing & Affordability")
end = concept_text.find("Domain 2 — Economic Security & Food")
housing_concept = concept_text[start:end].strip()
print(f"Extracted Housing & Affordability concept text: {len(housing_concept)} characters")


# Establish the 31-tract universe
tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS = load_cb3_tract_universe(
    PROJECT_DIR
)
print(f"Validated CB3 tract universe: {len(tracts)} tracts")


# Wrap domain-aware helpers so call sites below stay unchanged.
def _assign(frame, lon_col, lat_col, source_tract_col=None):
    return assign_points_to_cb3_tract(
        frame, lon_col, lat_col, cb3_tract_geometry, CB3_TRACT_CODES, source_tract_col
    )


def _load_acs(filename):
    return load_cb3_acs(filename, RAW_DIR, CB3_TRACT_CODES)


# Severe rent burden

# Severe rent burden: renters spending at least 50% of income on gross rent.
rent_burden = _load_acs("acs_5yr_2024_B25070.csv")
rent_burden["severe_rent_burden_households"] = rent_burden["B25070_010E"]
rent_burden["rent_burden_denominator"] = (
    rent_burden["B25070_001E"] - rent_burden["B25070_011E"]
)
rent_burden["severe_rent_burden_pct"] = percent(
    rent_burden["severe_rent_burden_households"],
    rent_burden["rent_burden_denominator"],
)
rent_burden_metrics = rent_burden[
    [
        "GEOID",
        "severe_rent_burden_households",
        "rent_burden_denominator",
        "severe_rent_burden_pct",
    ]
]


# Crowding

# Crowding: owner- or renter-occupied households with more than one occupant
# per room. Cells 5-7 are owners and 11-13 are renters.
crowding = _load_acs("acs_5yr_2024_B25014.csv")
crowded_cells = [
    "B25014_005E",
    "B25014_006E",
    "B25014_007E",
    "B25014_011E",
    "B25014_012E",
    "B25014_013E",
]
crowding["crowded_households"] = crowding[crowded_cells].sum(axis=1)
crowding["occupied_households"] = crowding["B25014_001E"]
crowding["crowded_households_pct"] = percent(
    crowding["crowded_households"], crowding["occupied_households"]
)
crowding_metrics = crowding[
    ["GEOID", "crowded_households", "occupied_households", "crowded_households_pct"]
]


# Tenure overall and by race/ethnicity

# Tenure overall and by the race/ethnicity variants available in the local file.
tenure = _load_acs("acs_5yr_2024_B25003.csv")
tenure["occupied_housing_units"] = tenure["B25003_001E"]
tenure["owner_occupied_units"] = tenure["B25003_002E"]
tenure["renter_occupied_units"] = tenure["B25003_003E"]
tenure["owner_occupied_pct"] = percent(
    tenure["owner_occupied_units"], tenure["occupied_housing_units"]
)
tenure["renter_occupied_pct"] = percent(
    tenure["renter_occupied_units"], tenure["occupied_housing_units"]
)

race_variants = {
    "A": "white_alone",
    "B": "black_alone",
    "D": "asian_alone",
    "H": "white_non_hispanic",
    "I": "hispanic",
}
tenure_columns = [
    "GEOID",
    "occupied_housing_units",
    "owner_occupied_units",
    "renter_occupied_units",
    "owner_occupied_pct",
    "renter_occupied_pct",
]
for suffix, label in race_variants.items():
    total = f"B25003{suffix}_001E"
    owner = f"B25003{suffix}_002E"
    renter = f"B25003{suffix}_003E"
    tenure[f"owner_occupied_pct_{label}"] = percent(tenure[owner], tenure[total])
    tenure[f"renter_occupied_pct_{label}"] = percent(tenure[renter], tenure[total])
    tenure_columns.extend(
        [f"owner_occupied_pct_{label}", f"renter_occupied_pct_{label}"]
    )
tenure_metrics = tenure[tenure_columns]


# Rent burden

# Rent burden of at least 30% by household-income band. Each B25074 income block contains a total, three categories below 30%,
# four categories at or above 30%, and a "not computed" category.
rent_income = _load_acs("acs_5yr_2024_B25074.csv")
income_blocks = {
    "lt_10k": 2,
    "10k_19k": 11,
    "20k_34k": 20,
    "35k_49k": 29,
    "50k_74k": 38,
    "75k_99k": 47,
    "100k_plus": 56,
}
rent_income_columns = ["GEOID"]
for label, start_cell in income_blocks.items():
    total_column = f"B25074_{start_cell:03d}E"
    burden_columns = [
        f"B25074_{cell:03d}E" for cell in range(start_cell + 4, start_cell + 8)
    ]
    #ACS category for households whose rent burden could not be calculated.
    not_computed_column = f"B25074_{start_cell + 8:03d}E"
    denominator_name = f"rent_burden_denominator_income_{label}"
    count_name = f"rent_burden_30plus_households_income_{label}"
    pct_name = f"rent_burden_30plus_pct_income_{label}"
    rent_income[denominator_name] = (
        # remove the NAs from the denominator
        rent_income[total_column] - rent_income[not_computed_column]
    )
    rent_income[count_name] = rent_income[burden_columns].sum(axis=1)
    rent_income[pct_name] = percent(
        rent_income[count_name], rent_income[denominator_name]
    )
    rent_income_columns.extend([count_name, denominator_name, pct_name])
rent_income_metrics = rent_income[rent_income_columns]


# Housing maintenance violations (Housing quality)

# Keep valid Manhattan CB3 records and aggregate open HPD violations by class.
hpd = pd.read_csv(
    RAW_DIR / "Housing_Maintenance_Code_Violations_20260504.csv",
    low_memory=False,
)
hpd = hpd[
    (hpd["BoroID"] == 1)
    & (hpd["CommunityBoard"] == 3)
    & hpd["BBL"].astype(str).str.startswith("1")
].copy()
hpd["GEOID"] = _assign(hpd, "Longitude", "Latitude", "CensusTract")
hpd_unallocated_count = int(hpd["GEOID"].isna().sum())
hpd = hpd[hpd["GEOID"].isin(CB3_GEOIDS)]
hpd_open = hpd[hpd["ViolationStatus"].eq("Open")].copy()

hpd_metrics = (
    hpd_open.assign(
        hpd_open_violation=1,
        hpd_open_class_a=hpd_open["Class"].eq("A").astype(int),
        hpd_open_class_b=hpd_open["Class"].eq("B").astype(int),
        hpd_open_class_c=hpd_open["Class"].eq("C").astype(int),
        hpd_open_class_bc=hpd_open["Class"].isin(["B", "C"]).astype(int),
    )
    .groupby("GEOID", as_index=False)[
        [
            "hpd_open_violation",
            "hpd_open_class_a",
            "hpd_open_class_b",
            "hpd_open_class_c",
            "hpd_open_class_bc",
        ]
    ]
    .sum()
    .rename(columns={"hpd_open_violation": "hpd_open_violations"})
)


# Executed evictions

# Filter to residential executed evictions in Manhattan CB3 and spatially join
# their coordinates to official 2020 tract polygons.
evictions = pd.read_csv(RAW_DIR / "Evictions_20260420.csv", low_memory=False)
evictions = evictions[
    evictions["BOROUGH"].eq("MANHATTAN")
    & evictions["Community Board"].eq(3)
    & evictions["Residential/Commercial"].eq("Residential")
].copy()
evictions["executed_date"] = pd.to_datetime(
    evictions["Executed Date"], errors="coerce"
)
evictions["GEOID"] = _assign(evictions, "Longitude", "Latitude", "Census Tract")
eviction_unallocated_count = int(evictions["GEOID"].isna().sum())
evictions_mapped = evictions[evictions["GEOID"].isin(CB3_GEOIDS)].copy()

eviction_metrics = (
    evictions_mapped.assign(
        executed_evictions_total=1,
        executed_evictions_2024=evictions_mapped["executed_date"].dt.year.eq(2024),
        executed_evictions_2025=evictions_mapped["executed_date"].dt.year.eq(2025),
    )
    .groupby("GEOID", as_index=False)[
        [
            "executed_evictions_total",
            "executed_evictions_2024",
            "executed_evictions_2025",
        ]
    ]
    .sum()
)

# The filings trend is community-district level and is retained as context,
# not allocated to individual tracts.
filings = pd.read_csv(RAW_DIR / "communitydistrict-privateevictionfilings.csv")
cb3_filings = filings[
    filings["Community District"].eq("MN 03 - Lower East Side/Chinatown")
].iloc[0]


# Subsidized housing and recent construction

# Load the lot-level Furman file and spatially join property coordinates to the
# official 2020 tract polygons. The older tract_10 field is a fallback only.
furman_bbl = pd.read_csv(
    FURMAN_DIR / "FC_SHD_bbl_analysis_2025-05-13.csv",
    low_memory=False,
)
furman_bbl = furman_bbl[furman_bbl["cd_id"].eq(103)].copy()
furman_bbl["GEOID"] = _assign(furman_bbl, "longitude", "latitude")
furman_bbl_unallocated_count = int(
    (~furman_bbl["GEOID"].isin(CB3_GEOIDS)).sum()
)
furman_bbl_mapped = furman_bbl[furman_bbl["GEOID"].isin(CB3_GEOIDS)].copy()

# Find each lot's earliest recorded subsidy expiration year across program fields.
end_columns = [
    column for column in furman_bbl_mapped.columns if column.startswith("end_")
]
end_years = furman_bbl_mapped[end_columns].map(extract_year)
furman_bbl_mapped["earliest_expiration_year"] = end_years.min(axis=1)
furman_bbl_mapped["subsidized_property"] = 1
furman_bbl_mapped["subsidized_units"] = furman_bbl_mapped["res_units"].fillna(0)
furman_bbl_mapped["subsidized_units_expiring_2025_2030"] = np.where(
    furman_bbl_mapped["earliest_expiration_year"].between(2025, 2030),
    furman_bbl_mapped["subsidized_units"],
    0,
)
furman_bbl_mapped["subsidized_units_expiring_2031_2040"] = np.where(
    furman_bbl_mapped["earliest_expiration_year"].between(2031, 2040),
    furman_bbl_mapped["subsidized_units"],
    0,
)
furman_bbl_mapped["senior_subsidized_property"] = (
    furman_bbl_mapped[["prog_202_8", "prog_prac_202"]].eq(1).any(axis=1).astype(int)
)
furman_bbl_mapped["senior_subsidized_units"] = np.where(
    furman_bbl_mapped["senior_subsidized_property"].eq(1),
    furman_bbl_mapped["subsidized_units"],
    0,
)

subsidized_metrics = (
    furman_bbl_mapped.groupby("GEOID", as_index=False)[
        [
            "subsidized_property",
            "subsidized_units",
            "subsidized_units_expiring_2025_2030",
            "subsidized_units_expiring_2031_2040",
            "senior_subsidized_property",
            "senior_subsidized_units",
        ]
    ]
    .sum()
    .rename(
        columns={
            "subsidized_property": "subsidized_properties",
            "senior_subsidized_property": "senior_subsidized_properties",
        }
    )
)

# Preserve the published Furman community-district expiration totals as context.
# The property-level unit method does not reproduce the published district series,
# so the published totals are not divided across individual tracts.
def read_cb3_expiration_context(filename):
    frame = pd.read_csv(RAW_DIR / filename)
    row = frame[
        frame["Community District"].eq("MN 03 - Lower East Side/Chinatown")
    ].iloc[0]
    return int(row["2024"])


expiration_2025_2030_cd = read_cb3_expiration_context(
    "communitydistrict-eligibletoexpirefromhousingprogramsbetween2025and2030units.csv"
)
expiration_2031_2040_cd = read_cb3_expiration_context(
    "communitydistrict-eligibletoexpirefromhousingprogramsbetween2031and2040units.csv"
)
expiration_2041_later_cd = read_cb3_expiration_context(
    "communitydistrict-eligibletoexpirefromhousingprogramsin2041andlaterunits.csv"
)

# Use the subsidy-level file for recent new-construction records. The local file
# identifies subsidy/program type, not AMI band, so no AMI value is inferred.
furman_subsidy = pd.read_csv(
    FURMAN_DIR / "FC_SHD_subsidy_analysis_2025-05-13.csv",
    encoding="cp1252",
    low_memory=False,
)
furman_subsidy = furman_subsidy[furman_subsidy["cd_id"].eq(103)].copy()
furman_subsidy["start_year"] = furman_subsidy["start_date"].map(extract_year)
furman_subsidy = furman_subsidy.merge(
    furman_bbl[["bbl", "GEOID"]].rename(columns={"bbl": "ref_bbl"}),
    on="ref_bbl",
    how="left",
    validate="many_to_one",
)
recent_construction = furman_subsidy[
    furman_subsidy["preservation"].eq("New Construction")
    & furman_subsidy["start_year"].ge(2018)
    & furman_subsidy["GEOID"].isin(CB3_GEOIDS)
].copy()

# A property may have multiple subsidy rows. Count each BBL once and use its
# maximum reported unit count to avoid double counting units.
recent_construction = (
    recent_construction.groupby(["GEOID", "ref_bbl"], as_index=False)
    .agg(new_affordable_units_since_2018=("tot_units", "max"))
)
recent_construction["new_affordable_properties_since_2018"] = 1
construction_metrics = recent_construction.groupby("GEOID", as_index=False)[
    ["new_affordable_properties_since_2018", "new_affordable_units_since_2018"]
].sum()


# Housing-related health conditions

# Spatially join indoor environmental complaint coordinates to 2020 tracts.
indoor = pd.read_csv(
    RAW_DIR / "DOHMH_Indoor_Environmental_Complaints_20260428.csv",
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

indoor_points = gpd.GeoDataFrame(
    indoor_mapped,
    geometry=gpd.points_from_xy(
        indoor_mapped["Longitude"], indoor_mapped["Latitude"]
    ),
    crs="EPSG:4326",
)
assert indoor_points.geometry.x.between(-75, -73).all()
assert indoor_points.geometry.y.between(40, 41.5).all()

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

# Aggregate NYCHA code violations for properties explicitly assigned to MN 03.
nycha = pd.read_csv(
    RAW_DIR / "Housing_Maintenance_Code_Violations_NYCHA_properties_20260420.csv",
    low_memory=False,
)
nycha = nycha[
    nycha["Primary Borough Name"].eq("MANHATTAN")
    & nycha["Community Board"].eq(103)
].copy()
nycha["GEOID"] = _assign(nycha, "Longitude", "Latitude", "Census Tract (2020)")
nycha_unallocated_count = int(nycha["GEOID"].isna().sum())
nycha_metrics = (
    nycha[nycha["GEOID"].isin(CB3_GEOIDS)]
    .assign(nycha_code_violations=1)
    .groupby("GEOID", as_index=False)["nycha_code_violations"]
    .sum()
)


# Assemble and write the tract table

# Left-join every tract-level metric to the 31-row base. Event/property counts
# are filled with zero; ACS percentages remain missing when denominators are zero.
metric_frames = [
    rent_burden_metrics,
    crowding_metrics,
    tenure_metrics,
    rent_income_metrics,
    hpd_metrics,
    eviction_metrics,
    subsidized_metrics,
    construction_metrics,
    indoor_metrics,
    nycha_metrics,
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

count_prefixes = (
    "hpd_",
    "executed_evictions_",
    "subsidized_",
    "senior_subsidized_",
    "new_affordable_",
    "indoor_",
    "mold_",
    "asbestos_",
    "nycha_",
)
count_columns = [
    column
    for column in clean.columns
    if column.startswith(count_prefixes) and not column.endswith("_pct")
]
clean[count_columns] = clean[count_columns].fillna(0)

# Retain district-level context under explicitly named CD columns.
clean["private_eviction_filings_2024_cd_context"] = int(cb3_filings["2024"])
clean["subsidized_units_expiring_2025_2030_cd_context"] = expiration_2025_2030_cd
clean["subsidized_units_expiring_2031_2040_cd_context"] = expiration_2031_2040_cd
clean["subsidized_units_expiring_2041_later_cd_context"] = expiration_2041_later_cd

# Write geography and data-availability notes to a sidecar log instead of
# repeating identical text across all 31 rows.
log_lines = [
    "CB3 Housing & Affordability — Build Log",
    f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"Output:    {OUTPUT_PATH}",
    "",
    "=== Tract Universe ===",
    "Official DCP 2020 tract-NTA-CDTA crosswalk; CDTACode=MN03",
    "",
    "=== Point Assignment Method ===",
    "Coordinates spatially joined to local 2020 census tract polygons; "
    "unambiguous source tract used only as fallback",
    "",
    "=== Geography ===",
    f"HPD violations:        {hpd_unallocated_count} valid Manhattan CB3 HPD records not allocated to a tract",
    f"Subsidized housing:    {furman_bbl_unallocated_count} BBL records not allocated; "
    "published CD expiration totals retained as context columns",
    f"Executed evictions:    {eviction_unallocated_count} residential records not allocated",
    f"Indoor complaints:     {indoor_unallocated_count} CB3-labelled records not allocated",
    f"NYCHA violations:      {nycha_unallocated_count} MN03 NYCHA records not allocated",
    "Eviction filings:      Community-district trend only; 2024 value retained as CD context column",
    "",
    "=== Data Availability ===",
    "Supportive housing:    Primary DSS/OSH file unavailable; no tract metric produced",
    "New construction AMI:  Local Furman file has no defensible AMI-band field",
    "HPD Local Law 44:      Project and unit-income files unavailable",
    "Senior walkup units:   MapPLUTO elevator and BIS inspection files unavailable",
    "CHP 2022:              PUMA/sub-borough survey context only; not allocated to tracts",
    "Tenant legal services: PDF address list not converted to a tract metric",
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

# Report core checks and district totals without treating non-tract context as
# tract-level information.
validation = pd.Series(
    {
        "tract_rows": len(clean),
        "unique_geoids": clean["GEOID"].nunique(),
        "occupied_housing_units": clean["occupied_housing_units"].sum(),
        "open_hpd_class_b_or_c": clean["hpd_open_class_bc"].sum(),
        "mapped_residential_executed_evictions": clean[
            "executed_evictions_total"
        ].sum(),
        "mapped_subsidized_properties": clean["subsidized_properties"].sum(),
        "mapped_indoor_environmental_complaints": clean[
            "indoor_environmental_complaints"
        ].sum(),
        "mapped_nycha_code_violations": clean["nycha_code_violations"].sum(),
    }
)
print(validation.to_frame("value").to_string())
