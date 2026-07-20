"""Build the CB3 Public Services domain clean files from the newly-added raw
clean exports (park facility inventory, subway entrances, bus stops, public
restrooms).

Outputs
-------
data/clean/public_service_tract.csv               (1 row per CB3 tract)
data/clean/public_service_park_points.csv          (1 row per park)
data/clean/public_service_park_facility_points.csv (1 row per park x facility type present)
data/clean/public_service_transit_points.csv       (1 row per subway entrance or bus stop)
data/clean/public_service_restroom_points.csv      (1 row per restroom)
data/clean/public_service_build_log.txt

Data-quality note
-----------------
Three of the four source files
(PUBLIC_SERVICE_DOMAIN_bus_stops_file.csv, ..._subway_entrances_file.csv,
..._park_facilities.csv) are not valid CSV: they were exported from R with an
unquoted trailing/embedded list-type column (a projected-coordinate
``c(x, y)`` pair for the point files, and an R-deparsed ``list(list(c(...)))``
polygon column for the park file) whose internal commas collide with the CSV
field separator, and — for the park file specifically — whose long numeric
vectors were hard-wrapped onto multiple physical lines. This module parses
around both problems directly from the raw text rather than via
``pandas.read_csv``. The point files' unusable projected-coordinate column is
dropped in favor of their valid WKT lon/lat column.
"""

from pathlib import Path
import csv
import datetime
import math
import re
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

_project_dir = Path(__file__).resolve().parents[2]
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))

from src.cb3_utils import add_polygon_centroids, assign_points_to_cb3_tract, load_cb3_tract_universe

PROJECT_DIR = _project_dir
RAW_DIR = PROJECT_DIR / "data" / "clean"  # the four source files were delivered pre-cleaned into data/clean
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

PARK_FACILITIES_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_park_facilities.csv"
SUBWAY_ENTRANCES_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_subway_entrances_file.csv"
BUS_STOPS_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_bus_stops_file.csv"
RESTROOMS_PATH = RAW_DIR / "PUBLIC_SERVICE_DOMAIN_public_restrooms.csv"

TRACT_OUTPUT = CLEAN_DIR / "public_service_tract.csv"
PARK_POINTS_OUTPUT = CLEAN_DIR / "public_service_park_points.csv"
PARK_FACILITY_POINTS_OUTPUT = CLEAN_DIR / "public_service_park_facility_points.csv"
TRANSIT_POINTS_OUTPUT = CLEAN_DIR / "public_service_transit_points.csv"
RESTROOM_POINTS_OUTPUT = CLEAN_DIR / "public_service_restroom_points.csv"
LOG_OUTPUT = CLEAN_DIR / "public_service_build_log.txt"

# Facility columns present in the park facility inventory. NYC Parks capital
# projects (pickleball) and chess/game tables or skate parks are not columns
# in this file and are therefore not represented as metrics here.
FACILITY_COLUMNS = [
    "basketball_court",
    "other_court",
    "athletic_field_baseball_softball",
    "pool",
    "athletic_field_football",
    "running_track",
    "athletic_field_soccer",
    "tennis_court",
    "has_playground",
    "dog_runs",
]
FACILITY_LABELS = {
    "basketball_court": "Basketball court",
    "other_court": "Other court",
    "athletic_field_baseball_softball": "Baseball/softball field",
    "pool": "Pool",
    "athletic_field_football": "Football field",
    "running_track": "Running track",
    "athletic_field_soccer": "Soccer field",
    "tennis_court": "Tennis court",
    "has_playground": "Playground",
    "dog_runs": "Dog run",
}

QUARTER_MILE_FEET = 1320  # 0.25 mi, buffered in EPSG:2263 (NY State Plane, feet)


def _log(lines, message):
    print(message)
    lines.append(message)


def parse_park_facilities(path, log_lines):
    """Parse the R-deparsed park facility inventory into a park-level GeoDataFrame.

    The file is not valid CSV (see module docstring): each park record's
    ``multipolygon`` field is unquoted R ``list(list(c(lon..., lat...)))``
    text whose numeric vector was hard line-wrapped, so pandas/csv cannot
    read it row-by-row. This function re-joins the wrapped physical lines,
    then splits records using the fixed-shape tail of 10 facility flags +
    inspection date + condition flag that follows every ``multipolygon``
    field (the only reliably delimited part of each record).
    """
    raw_text = path.read_text(encoding="utf-8")
    header_line, body = raw_text.split("\n", 1)
    joined = body.replace("\n", " ")

    scalar_field = r"(?:NA|TRUE|FALSE|\d+)"
    date_field = r'(?:NA|"[^"]*")'
    tail_pattern = re.compile(r"\)\)\),(" + scalar_field + r",){10}" + date_field + r",(?:NA|\d+)")

    records = []
    previous_end = 0
    for match in tail_pattern.finditer(joined):
        records.append(joined[previous_end:match.end()].strip())
        previous_end = match.end()
    leftover = joined[previous_end:].strip()
    if leftover:
        _log(log_lines, f"Warning: {len(leftover)} unparsed trailing characters after last park record.")

    record_pattern = re.compile(r'^"([^"]*)","([^"]*)","([^"]*)",([\d.]+),(list.*)$')
    multipolygon_pattern = re.compile(r"^(list\(.*?\)\)\)),(.*)$")

    rows = []
    unusable_geometry_count = 0
    for record in records:
        match = record_pattern.match(record)
        if not match:
            _log(log_lines, f"Warning: could not parse park record head: {record[:80]!r}")
            continue
        gisprop, park_name, category, acres, rest = match.groups()

        mp_match = multipolygon_pattern.match(rest)
        multipolygon_text, tail_text = mp_match.groups()
        tail_fields = next(csv.reader([tail_text]))

        # Each c(...) block is one polygon ring: first half of its numbers are
        # longitudes, second half are latitudes, in matching point order.
        # Some parks have more than one c(...) block (multi-part properties);
        # their rings are combined with unary_union into one park geometry,
        # which treats a second ring as added area rather than a hole — a
        # reasonable approximation since only the buffered accessibility
        # footprint (not the ring's interior/exterior distinction) is used.
        rings = []
        for ring_text in re.findall(r"c\(([^)]*)\)", multipolygon_text):
            numbers = [float(value) for value in ring_text.split(",")]
            half = len(numbers) // 2
            longitudes, latitudes = numbers[:half], numbers[half:]
            if len(longitudes) != len(latitudes) or len(longitudes) < 3:
                unusable_geometry_count += 1
                continue
            rings.append(Polygon(zip(longitudes, latitudes)))
        geometry = unary_union(rings) if rings else None

        row = {
            "GISPROPNUM": gisprop,
            "park_name": park_name,
            "category": category,
            "acres": float(acres),
            "geometry": geometry,
            "inspection_date": tail_fields[10],
            "condition_acceptable": tail_fields[11],
        }
        for column, value in zip(FACILITY_COLUMNS, tail_fields[:10]):
            row[column] = value in ("1", "TRUE")
        rows.append(row)

    _log(log_lines, f"Parsed {len(rows)} park records from {path.name} ({unusable_geometry_count} unusable rings skipped).")

    parks = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    parks = parks[parks.geometry.notna()].copy()
    return parks


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


def offset_icon_points_ft(centroid, equiv_radius_ft, count):
    """Positions for ``count`` facility-type icons inside one park, spread
    evenly around ``centroid`` on a circle sized to the park (in feet, same
    projected CRS as ``centroid``).

    A single facility sits at the centroid. Multiple facilities are spread
    around a ring at 60% of the park's equivalent-circle radius (the radius
    of a circle with the same area as the park), clamped to 15-120 ft so
    icons stay visibly separated in small parks and don't drift outside
    medium ones; very large parks (e.g. East River Park) will still spread
    icons past their edges since two courts on opposite sides of the park
    aren't literally centroid-adjacent, which is an acceptable simplification
    for an icon layer rather than exact facility placement.
    """
    if count <= 1:
        return [(centroid.x, centroid.y)]
    icon_radius_ft = max(15, min(equiv_radius_ft * 0.6, 120))
    return [
        (
            centroid.x + icon_radius_ft * math.cos(2 * math.pi * i / count),
            centroid.y + icon_radius_ft * math.sin(2 * math.pi * i / count),
        )
        for i in range(count)
    ]


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

    # Parks

    parks = parse_park_facilities(PARK_FACILITIES_PATH, log_lines)
    park_centroids = parks.to_crs("EPSG:2263").geometry.centroid.to_crs("EPSG:4326")
    parks["latitude"] = park_centroids.y.values
    parks["longitude"] = park_centroids.x.values
    parks["GEOID"] = assign_points_to_cb3_tract(
        parks, longitude_column="longitude", latitude_column="latitude",
        cb3_tract_geometry=cb3_tract_geometry, CB3_TRACT_CODES=CB3_TRACT_CODES,
    )
    parks_unallocated_count = int(parks["GEOID"].isna().sum())
    _log(log_lines, f"Parks: {parks_unallocated_count} of {len(parks)} not allocated to a CB3 tract (centroid outside CB3).")

    parks["facility_list"] = parks[FACILITY_COLUMNS].apply(
        lambda row: ", ".join(FACILITY_LABELS[column] for column in FACILITY_COLUMNS if row[column]) or "None recorded",
        axis=1,
    )
    parks["geometry_wkt"] = parks.geometry.to_wkt()

    # Precompute each park's projected centroid and equivalent-circle radius
    # (both in feet) as plain columns, so they survive the merge/column
    # selection below and stay row-aligned with park_points by GISPROPNUM
    # rather than relying on positional order.
    parks_geometry_ft = parks.to_crs("EPSG:2263").geometry
    parks["_centroid_ft"] = parks_geometry_ft.centroid
    parks["_equiv_radius_ft"] = (parks_geometry_ft.area / math.pi) ** 0.5

    park_points = parks.merge(tract_base[["GEOID", "tract_label", "nta_name"]], on="GEOID", how="left")
    icon_geometry_lookup = park_points.set_index("GISPROPNUM")[["_centroid_ft", "_equiv_radius_ft"]]
    park_points = park_points[
        ["GISPROPNUM", "park_name", "category", "acres", "latitude", "longitude", "GEOID",
         "tract_label", "nta_name", "condition_acceptable", "inspection_date", "facility_list",
         "geometry_wkt"] + FACILITY_COLUMNS
    ]
    park_points.to_csv(PARK_POINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(park_points)} park points to {PARK_POINTS_OUTPUT}")

    # Long-format park x facility-type points, for icons layered inside each
    # park's real footprint. A park with multiple facility types gets its
    # icons spread around a small ring rather than all stacked at the
    # centroid (see offset_icon_points_ft).
    facility_point_rows = []
    for park_row in park_points.itertuples():
        present_columns = [column for column in FACILITY_COLUMNS if getattr(park_row, column)]
        if not present_columns:
            continue
        centroid_ft = icon_geometry_lookup.loc[park_row.GISPROPNUM, "_centroid_ft"]
        equiv_radius_ft = icon_geometry_lookup.loc[park_row.GISPROPNUM, "_equiv_radius_ft"]
        offsets_ft = offset_icon_points_ft(centroid_ft, equiv_radius_ft, len(present_columns))
        offsets_wgs84 = gpd.GeoSeries(
            [Point(x, y) for x, y in offsets_ft], crs="EPSG:2263",
        ).to_crs("EPSG:4326")
        for column, point in zip(present_columns, offsets_wgs84):
            facility_point_rows.append({
                "park_name": park_row.park_name,
                "facility_type": FACILITY_LABELS[column],
                "latitude": point.y,
                "longitude": point.x,
                "GEOID": park_row.GEOID,
                "tract_label": park_row.tract_label,
                "nta_name": park_row.nta_name,
            })
    park_facility_points = pd.DataFrame(facility_point_rows)
    park_facility_points.to_csv(PARK_FACILITY_POINTS_OUTPUT, index=False)
    _log(log_lines, f"Wrote {len(park_facility_points)} park x facility-type points to {PARK_FACILITY_POINTS_OUTPUT}")

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
        "Pickleball courts, chess/game tables, skate parks: not columns in "
        "PUBLIC_SERVICE_DOMAIN_park_facilities.csv; no metric produced. The "
        "NYC Parks Capital Projects Tracker (pickleball) referenced in the "
        "domain reference sheet was not among the uploaded raw files.",
        "",
        "=== Parsing Notes ===",
        *log_lines,
    ]
    LOG_OUTPUT.write_text("\n".join(log_lines) + "\n")

    print("\nWrote Public Services clean outputs:")
    for path in [TRACT_OUTPUT, PARK_POINTS_OUTPUT, PARK_FACILITY_POINTS_OUTPUT, TRANSIT_POINTS_OUTPUT, RESTROOM_POINTS_OUTPUT, LOG_OUTPUT]:
        print(f"  {path.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
