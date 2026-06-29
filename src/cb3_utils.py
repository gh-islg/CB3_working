"""Shared data-wrangling utilities for CB3 domain metric scripts."""

import re

import geopandas as gpd
import numpy as np
import pandas as pd


def clean_census_values(frame):
    """Convert Census API suppression/sentinel values to missing values."""
    cleaned = frame.copy()
    numeric_columns = cleaned.select_dtypes(include="number").columns
    cleaned[numeric_columns] = cleaned[numeric_columns].mask(
        cleaned[numeric_columns] < 0
    )
    return cleaned


def percent(numerator, denominator):
    """Calculate a percentage, returning NaN for zero denominators."""
    denominator = denominator.replace(0, np.nan)
    return numerator.div(denominator).mul(100)


def add_acs_geoid(frame):
    """Build an 11-character tract GEOID from ACS geography columns."""
    result = frame.copy()
    result["GEOID"] = (
        result["state"].astype(int).astype(str).str.zfill(2)
        + result["county"].astype(int).astype(str).str.zfill(3)
        + result["tract"].astype(int).astype(str).str.zfill(6)
    )
    return result


def extract_year(value):
    """Extract a four-digit year from numeric, YYYYMMDD, or ISO date values."""
    if pd.isna(value):
        return np.nan
    match = re.search(r"(19|20)\d{2}", str(value))
    return float(match.group(0)) if match else np.nan


def load_cb3_tract_universe(project_dir):
    """Load the official 31-tract CB3 universe and return core lookup objects.

    Returns
    -------
    tracts : pd.DataFrame
        One row per CB3 census tract with NTA/CDTA labels.
    cb3_tract_geometry : gpd.GeoDataFrame
        Tract polygons in EPSG:4326, keyed by GEOID.
    CB3_TRACT_CODES : set of int
        CT2020 integer tract codes for the 31 CB3 tracts.
    CB3_GEOIDS : set of str
        11-character GEOIDs for the 31 CB3 tracts.
    """
    geography_dir = project_dir / "data" / "raw" / "Geography"
    relationship_dir = geography_dir / "GeographicRelationshipFiles"

    tract_crosswalk = pd.read_excel(
        relationship_dir / "nyc_2020_census_tract_nta_cdta_relationships.xlsx",
        sheet_name="NYC_CT2020_Relate",
        dtype={
            "GEOID": "string",
            "CT2020": "string",
            "NTACode": "string",
            "CDTACode": "string",
        },
    )
    tract_crosswalk["GEOID"] = tract_crosswalk["GEOID"].str.zfill(11)
    cb3_crosswalk = tract_crosswalk[
        tract_crosswalk["BoroName"].eq("Manhattan")
        & tract_crosswalk["CDTACode"].eq("MN03")
    ].copy()

    tract_geometry_all = gpd.read_file(
        geography_dir / "2020_Census_Tracts_20260505.geojson"
    )
    tract_geometry_all["GEOID"] = (
        tract_geometry_all["geoid"].astype("string").str.zfill(11)
    )
    cb3_tract_geometry = tract_geometry_all[
        tract_geometry_all["GEOID"].isin(cb3_crosswalk["GEOID"])
    ][["GEOID", "geometry"]].copy()
    cb3_tract_geometry = cb3_tract_geometry.to_crs("EPSG:4326")

    tracts = (
        cb3_crosswalk[
            ["GEOID", "CT2020", "CTLabel", "NTACode", "NTAName", "CDTACode", "CDTAName", "BoroName"]
        ]
        .rename(
            columns={
                "CT2020": "tract",
                "CTLabel": "tract_label",
                "NTACode": "nta_code",
                "NTAName": "nta_name",
                "CDTACode": "cdta_code",
                "CDTAName": "cdta_name",
                "BoroName": "borough",
            }
        )
    )
    tracts["tract_name"] = (
        "Census Tract " + tracts["tract_label"].astype(str) + ", " + tracts["borough"].astype(str) + " County, New York"
    )
    tracts = tracts.drop(columns=["borough"])
    tracts["tract"] = tracts["tract"].astype(int)
    tracts = tracts.sort_values("GEOID").reset_index(drop=True)

    assert len(tracts) == 31, f"Expected 31 CB3 tracts, found {len(tracts)}"
    assert tracts["GEOID"].is_unique
    assert tracts["tract_name"].notna().all()
    assert len(cb3_tract_geometry) == 31
    assert cb3_tract_geometry["GEOID"].is_unique

    CB3_TRACT_CODES = set(tracts["tract"])
    CB3_GEOIDS = set(tracts["GEOID"])

    return tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS


def assign_points_to_cb3_tract(
    frame,
    longitude_column,
    latitude_column,
    cb3_tract_geometry,
    CB3_TRACT_CODES,
    source_tract_column=None,
):
    """Assign point records to official 2020 CB3 tract polygons.

    Coordinates are the primary method; ``source_tract_column`` is used only
    as a fallback when coordinates are missing or fall outside CB3 bounds.
    """
    CB3_GEOIDS = set(cb3_tract_geometry["GEOID"])
    assigned = pd.Series(pd.NA, index=frame.index, dtype="string")
    longitude = pd.to_numeric(frame[longitude_column], errors="coerce")
    latitude = pd.to_numeric(frame[latitude_column], errors="coerce")
    valid_coordinates = longitude.between(-75, -73) & latitude.between(40, 41.5)

    points = gpd.GeoDataFrame(
        frame.loc[valid_coordinates].copy(),
        geometry=gpd.points_from_xy(
            longitude.loc[valid_coordinates], latitude.loc[valid_coordinates]
        ),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, cb3_tract_geometry, how="left", predicate="within")
    assigned.loc[joined.index] = joined["GEOID"].astype("string")

    if source_tract_column is not None:
        fallback = frame[source_tract_column].map(
            lambda v: _source_tract_to_geoid(v, CB3_TRACT_CODES)
        )
        assigned = assigned.fillna(fallback)

    return assigned


def load_cb3_acs(filename, raw_dir, CB3_TRACT_CODES):
    """Load and filter an ACS table to the 31-tract CB3 universe."""
    frame = clean_census_values(pd.read_csv(raw_dir / filename, low_memory=False))
    frame = frame.query("state == 36 and county == 61").copy()
    frame["tract"] = frame["tract"].astype(int)
    frame = frame[frame["tract"].isin(CB3_TRACT_CODES)]
    return add_acs_geoid(frame)


def _resolve_source_tract(value, allowed_codes):
    '''Map raw source tract label to CB3 integer tract codes since different datasets store tract numbers inconsistently. 
    For example, tract 2 might appear as 2, or as 200.
    Used in _source_tract_to_geoid.'''
    if pd.isna(value):
        return np.nan
    source_code = int(float(value))
    candidates = [source_code, source_code * 100]
    matches = [c for c in candidates if c in allowed_codes]
    return float(matches[0]) if len(matches) == 1 else np.nan


def _source_tract_to_geoid(value, CB3_TRACT_CODES):
    '''Used as fallback in assign_points_to_cb3_tract when coordinates are missing or do not land in a CB3 tract.'''
    tract_code = _resolve_source_tract(value, CB3_TRACT_CODES)
    if pd.isna(tract_code):
        return pd.NA
    return f"36061{int(tract_code):06d}"
