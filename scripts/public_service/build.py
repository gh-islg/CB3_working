"""Build the CB3 Public Services domain clean files from the newly-added raw
clean exports (park facility inventory, subway entrances, bus stops, public
restrooms).

Outputs
-------
data/clean/public_service_tract.csv               (1 row per CB3 tract)
data/clean/public_service_park_footprints.csv      (1 row per park, polygon as WKT)
data/clean/public_service_park_facility_points.csv (1 row per individual facility, at its own coordinates)
data/clean/public_service_transit_points.csv       (1 row per subway entrance or bus stop)
data/clean/public_service_restroom_points.csv      (1 row per restroom)
data/clean/public_service_build_log.txt

Data-quality note
-----------------
Two of the source files (PUBLIC_SERVICE_DOMAIN_bus_stops_file.csv and
..._subway_entrances_file.csv) are not valid CSV: they were exported from R
with an unquoted trailing projected-coordinate ``c(x, y)`` pair whose internal
comma collides with the CSV field separator. This module parses around that
directly from the raw text rather than via ``pandas.read_csv``, dropping the
unusable projected-coordinate column in favor of the valid WKT lon/lat column.
The park files (PUBLIC_SERVICE_DOMAIN_parks.csv, a park-level footprint WKT
file, and ..._park_facilities.csv, one row per facility with its own lon/lat)
are valid CSV and are read with ``pandas.read_csv``.
"""

from pathlib import Path
import csv
import datetime
import sys

import geopandas as gpd
import numpy as np
import pandas as pd

_project_dir = Path(__file__).resolve().parents[2]
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))

from src.cb3_utils import add_polygon_centroids, assign_points_to_cb3_tract, load_cb3_tract_universe

PROJECT_DIR = _project_dir
RAW_DIR = PROJECT_DIR / "data" / "clean"  # the four source files were delivered pre-cleaned into data/clean
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

PARKS_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_parks.csv"
PARK_FACILITIES_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_park_facilities.csv"
SUBWAY_ENTRANCES_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_subway_entrances_file.csv"
BUS_STOPS_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_bus_stops_file.csv"
RESTROOMS_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_public_restrooms.csv"

TRACT_OUTPUT = CLEAN_DIR / "public_service_tract.csv"
PARK_FOOTPRINTS_OUTPUT = CLEAN_DIR / "public_service_park_footprints.csv"
PARK_FACILITY_POINTS_OUTPUT = CLEAN_DIR / "public_service_park_facility_points.csv"
TRANSIT_POINTS_OUTPUT = CLEAN_DIR / "public_service_transit_points.csv"
RESTROOM_POINTS_OUTPUT = CLEAN_DIR / "public_service_restroom_points.csv"
LOG_OUTPUT = CLEAN_DIR / "public_service_build_log.txt"

# Facility types present in the park facility file (its ``facility_type``
# values) and their display labels. NYC Parks capital projects (pickleball) and
# chess/game tables or skate parks are not in this file and are therefore not
# represented as metrics here. Skating rinks are mapped in the R script but
# none fall in CB3, so they have no label; the assert in main() flags any that appear.
FACILITY_LABELS = {
    "basketball_court": "Basketball court",
    "other_court": "Other court",
    "athletic_field_baseball_softball": "Baseball/softball field",
    "pool": "Pool",
    "athletic_field_football": "Football field",
    "running_track": "Running track",
    "athletic_field_soccer": "Soccer field",
    "tennis_court": "Tennis court",
    "playground": "Playground",
    "dog_run": "Dog run",
}

QUARTER_MILE_FEET = 1320  # 0.25 mi, buffered in EPSG:2263 (NY State Plane, feet)


def _log(lines, message):
    print(message)
    lines.append(message)


def read_broken_trailing_geometry_csv(path):
    """Read a CSV whose last column is unquoted R ``c(x, y)`` projected-coordinate
    text (see module docstring). Returns only the well-formed leading columns;
    the broken trailing ``geometry`` column is dropped.
    """
    with open(path, encoding="utf-8") as file:
        rows = list(csv.reader(file))
    header = rows[0][:-1]
    data = [row[:len(header)] for row in rows[1:]]
    return pd.DataFrame(data, columns=header)


def extract_lon_lat(wkt_point_series):
    """Extract longitude/latitude floats from a Series of ``POINT (lon lat)`` text."""
    extracted = wkt_point_series.str.extract(r"POINT \(([-\d.]+) ([-\d.]+)\)")
    return extracted[0].astype(float), extracted[1].astype(float)


def compute_buffer_access_pct(cb3_tract_geometry_ft, geometries_wgs84, log_lines, label):
    """Percent of each tract's area within a quarter mile of any geometry in
    ``geometries_wgs84`` (park polygons or subway entrance points).

    Buffering and area math are done in EPSG:2263 (NY State Plane, feet) for
    accuracy; a geographic (lon/lat) CRS would distort distances.
    """
    if not geometries_wgs84:
        _log(log_lines, f"Warning: no geometries supplied for {label}; access pct set to 0 for all tracts.")
        return {geoid: 0.0 for geoid in cb3_tract_geometry_ft["GEOID"]}

    projected = gpd.GeoSeries(geometries_wgs84, crs="EPSG:4326").to_crs("EPSG:2263")
    buffered_union = projected.buffer(QUARTER_MILE_FEET).union_all()

    pct_by_geoid = {}
    for _, tract_row in cb3_tract_geometry_ft.iterrows():
        tract_geometry = tract_row.geometry
        intersection_area = tract_geometry.intersection(buffered_union).area
        pct_by_geoid[tract_row["GEOID"]] = 100 * intersection_area / tract_geometry.area
    return pct_by_geoid


def main():
    log_lines = []
    _log(log_lines, f"Project directory: {PROJECT_DIR}")

    tracts, cb3_tract_geometry, CB3_TRACT_CODES, CB3_GEOIDS = load_cb3_tract_universe(PROJECT_DIR)
    cb3_tract_geometry_ft = cb3_tract_geometry.to_crs("EPSG:2263")

    tract_base = tracts[["GEOID", "tract_label", "nta_name"]].copy()
    tract_base = add_polygon_centroids(
        tract_base, cb3_tract_geometry, id_column="GEOID",
        lat_col="tract_centroid_latitude", lon_col="tract_centroid_longitude",
    )

    # Parks: facilities keep their own coordinates (no park centroids)

    facilities = pd.read_csv(PARK_FACILITIES_PATH)
    unlabeled_types = set(facilities["facility_type"]) - set(FACILITY_LABELS)
    assert not unlabeled_types, f"facility types without a label: {unlabeled_types}"
    _log(log_lines, f"Read {len(facilities)} park facilities from {PARK_FACILITIES_PATH.name}.")

    # Assign each facility to the CB3 tract containing its own point.
    facilities["GEOID"] = assign_points_to_cb3_tract(
        facilities, longitude_column="longitude", latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry, CB3_TRACT_CODES=CB3_TRACT_CODES,
    )
    facilities_unallocated_count = int(facilities["GEOID"].isna().sum())
    _log(log_lines, f"Park facilities: {facilities_unallocated_count} of {len(facilities)} not allocated to a CB3 tract.")

    facilities["facility_type"] = facilities["facility_type"].map(FACILITY_LABELS)
    facility_points = facilities.merge(tract_base[["GEOID", "tract_label", "nta_name"]], on="GEOID", how="left")
    facility_points = facility_points[
        ["GISPROPNUM", "park_name", "facility_type", "latitude", "longitude", "GEOID", "tract_label", "nta_name"]
    ]
    facility_points.to_csv(PARK_FACILITY_POINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(facility_points)} park facility points to {PARK_FACILITY_POINTS_OUTPUT}")

    # Park footprints (polygon WKT) with a tooltip-ready list of the facility
    # types each park contains, derived from the facility file.
    park_footprints = pd.read_csv(PARKS_PATH).rename(columns={"TYPECATEGORY": "category", "ACRES": "acres"})
    facility_lists = facilities.groupby("GISPROPNUM")["facility_type"].agg(
        lambda types: ", ".join(label for label in FACILITY_LABELS.values() if label in set(types))
    )
    park_footprints["facility_list"] = park_footprints["GISPROPNUM"].map(facility_lists).fillna("None recorded")
    park_footprints = park_footprints[
        ["GISPROPNUM", "park_name", "category", "acres", "condition_acceptable", "inspection_date",
         "facility_list", "geometry_wkt"]
    ]
    park_footprints.to_csv(PARK_FOOTPRINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(park_footprints)} park footprints to {PARK_FOOTPRINTS_OUTPUT}")

    # Subway entrances

    subway_raw = read_broken_trailing_geometry_csv(SUBWAY_ENTRANCES_PATH)
    subway_raw["longitude"], subway_raw["latitude"] = extract_lon_lat(subway_raw["entrance_georeference"])
    subway_geometries = gpd.points_from_xy(subway_raw["longitude"], subway_raw["latitude"]).tolist()
    subway_access_pct = compute_buffer_access_pct(
        cb3_tract_geometry_ft, subway_geometries, log_lines, "subway entrance accessibility",
    )
    _log(log_lines, f"Parsed {len(subway_raw)} subway entrances from {SUBWAY_ENTRANCES_PATH.name}.")

    subway_raw["GEOID"] = assign_points_to_cb3_tract(
        subway_raw, longitude_column="longitude", latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry, CB3_TRACT_CODES=CB3_TRACT_CODES,
    )
    subway_unallocated_count = int(subway_raw["GEOID"].isna().sum())
    _log(log_lines, f"Subway entrances: {subway_unallocated_count} of {len(subway_raw)} not allocated to a CB3 tract.")
    subway_entrance_count = subway_raw[subway_raw["GEOID"].isin(CB3_GEOIDS)].groupby("GEOID").size()
    subway_entrance_count.name = "subway_entrance_count"

    # Bus stops

    bus_raw = read_broken_trailing_geometry_csv(BUS_STOPS_PATH)
    bus_raw["longitude"], bus_raw["latitude"] = extract_lon_lat(bus_raw["Georeference"])
    bus_raw["GEOID"] = assign_points_to_cb3_tract(
        bus_raw, longitude_column="longitude", latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry, CB3_TRACT_CODES=CB3_TRACT_CODES,
    )
    bus_unallocated_count = int(bus_raw["GEOID"].isna().sum())
    _log(log_lines, f"Bus stops: {bus_unallocated_count} of {len(bus_raw)} not allocated to a CB3 tract "
                     "(source file was filtered to a bounding box, not the tract boundary).")
    bus_stop_count = bus_raw[bus_raw["GEOID"].isin(CB3_GEOIDS)].groupby("GEOID").size()
    bus_stop_count.name = "bus_stop_count"

    # Individual subway entrance and bus stop points, for a point-level map
    # (as opposed to the per-tract pie bubbles).
    transit_points = pd.concat([
        pd.DataFrame({
            "stop_type": "Subway entrance",
            "stop_name": subway_raw["Stop.Name"],
            "entrance_type": subway_raw["Entrance.Type"],
            "latitude": subway_raw["latitude"],
            "longitude": subway_raw["longitude"],
            "GEOID": subway_raw["GEOID"],
        }),
        pd.DataFrame({
            "stop_type": "Bus stop",
            "stop_name": bus_raw["Stop.Name"],
            "entrance_type": pd.NA,
            "latitude": bus_raw["latitude"],
            "longitude": bus_raw["longitude"],
            "GEOID": bus_raw["GEOID"],
        }),
    ], ignore_index=True)
    transit_points = transit_points.merge(tract_base[["GEOID", "tract_label", "nta_name"]], on="GEOID", how="left")
    transit_points.to_csv(TRANSIT_POINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(transit_points)} transit points to {TRANSIT_POINTS_OUTPUT}")

    # Assemble tract table

    tract_table = tract_base.copy()
    tract_table["subway_access_pct"] = tract_table["GEOID"].map(subway_access_pct)
    tract_table = tract_table.merge(subway_entrance_count, on="GEOID", how="left", validate="one_to_one")
    tract_table["subway_entrance_count"] = tract_table["subway_entrance_count"].fillna(0).astype(int)
    tract_table = tract_table.merge(bus_stop_count, on="GEOID", how="left", validate="one_to_one")
    tract_table["bus_stop_count"] = tract_table["bus_stop_count"].fillna(0).astype(int)

    tract_table = tract_table.sort_values("GEOID").reset_index(drop=True)
    assert len(tract_table) == 31
    assert tract_table["GEOID"].is_unique

    tract_table.to_csv(TRACT_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(tract_table)} tract rows and {len(tract_table.columns)} columns to {TRACT_OUTPUT}")

    # Public restrooms

    restrooms = pd.read_csv(RESTROOMS_PATH)
    restrooms["status_category"] = np.where(
        restrooms["status"].ne("Operational"),
        "Not operational or closed for construction",
        restrooms["accessibility"].fillna("Accessibility not reported"),
    )
    restrooms["GEOID"] = assign_points_to_cb3_tract(
        restrooms, longitude_column="longitude", latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry, CB3_TRACT_CODES=CB3_TRACT_CODES,
    )
    restrooms_unallocated_count = int(restrooms["GEOID"].isna().sum())
    _log(log_lines, f"Restrooms: {restrooms_unallocated_count} of {len(restrooms)} not allocated to a CB3 tract.")
    restrooms = restrooms.merge(tract_base[["GEOID", "tract_label", "nta_name"]], on="GEOID", how="left")
    restrooms.to_csv(RESTROOM_POINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(restrooms)} restroom points to {RESTROOM_POINTS_OUTPUT}")

    # Write build log

    log_lines = [
        "CB3 Public Services — Build Log",
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "=== Data Availability ===",
        "Pickleball courts, chess/game tables, skate parks: not types in "
        "PUBLIC_SERVICE_DOMAIN_park_facilities.csv; no metric produced. The "
        "NYC Parks Capital Projects Tracker (pickleball) referenced in the "
        "domain reference sheet was not among the uploaded raw files.",
        "",
        "=== Parsing Notes ===",
        *log_lines,
    ]
    LOG_OUTPUT.write_text("\n".join(log_lines) + "\n")

    print("\nWrote Public Services clean outputs:")
    for path in [TRACT_OUTPUT, PARK_FOOTPRINTS_OUTPUT, PARK_FACILITY_POINTS_OUTPUT, TRANSIT_POINTS_OUTPUT, RESTROOM_POINTS_OUTPUT, LOG_OUTPUT]:
        print(f"  {path.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
