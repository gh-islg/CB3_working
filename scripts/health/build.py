"""Update CB3 Health outputs with OMH mental-health sites and life expectancy."""
from __future__ import annotations
import re, time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import geopandas as gpd
import pandas as pd
import requests

from src.cb3_utils import add_polygon_centroids, load_cb3_tract_universe

CLEAN = ROOT/"data"/"clean"
RAW = ROOT/"data"/"raw"/"Health"
GEO = ROOT/"data"/"raw"/"Geography"
TRACTS = GEO/"cb3_2020_census_tracts.geojson"
OMH_URL = "https://data.ny.gov/api/views/6nvr-tbv8/rows.csv?accessType=DOWNLOAD"
GEOCODER = "https://geosearch.planninglabs.nyc/v2/search"

PROGRAM_OUT = CLEAN/"mental_health_programs_geocoded.csv"
SITE_OUT = CLEAN/"mental_health_sites_geocoded.csv"
LIFE_WORKBOOK = RAW/"2022-chp-pud.xlsx"
LIFE_OUT = CLEAN/"health_cd.csv"
HEALTH_TRACT = CLEAN/"health_tract.csv"
HEALTH_NTA = CLEAN/"health_nta.csv"
HEALTH_OUT = CLEAN/"health.csv"
LOG_OUT = CLEAN/"health_log.txt"

INCLUDE = ("outpatient","clinic","crisis","assertive community treatment","act team",
           "care coordination","continuing day treatment","pros","partial hospitalization")
EXCLUDE = ("administrative","inpatient","state psychiatric center","family care",
           "supportive housing","school-based","school based")

def col(df, names):
    lookup={re.sub(r"[^a-z0-9]","",str(c).lower()):c for c in df.columns}
    for n in names:
        if n in df.columns: return n
        k=re.sub(r"[^a-z0-9]","",n.lower())
        if k in lookup: return lookup[k]
    return None

def norm_geoid(v):
    if pd.isna(v): return None
    d=re.sub(r"\D","",str(v))
    return d[-11:].zfill(11) if d else None

def norm(v):
    return "" if pd.isna(v) else re.sub(r"\s+"," ",str(v).strip().lower())

def parse_point(v):
    if pd.isna(v): return (None,None)
    m=re.search(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)",str(v),re.I)
    return (float(m.group(2)),float(m.group(1))) if m else (None,None)

def cb3_tracts():
    g=gpd.read_file(TRACTS)
    gc=col(g,["GEOID","GEOID20","geoid","TRACTFIPS"])
    if gc is None: raise ValueError(f"No GEOID column: {g.columns.tolist()}")
    g["GEOID"]=g[gc].map(norm_geoid)
    return g[["GEOID","geometry"]].dropna().drop_duplicates("GEOID")


def _normalized_text(value):
    """Normalize geography labels for a fallback name-based match."""
    if pd.isna(value):
        return None
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _attach_tract_centroids():
    """Add shared tract-centroid columns to health_tract.csv."""
    tracts, cb3_tract_geometry, _, _ = load_cb3_tract_universe(ROOT)

    health = pd.read_csv(HEALTH_TRACT, dtype={"GEOID": str})
    health["GEOID"] = health["GEOID"].map(norm_geoid)

    # Remove stale centroid fields before reattaching them.
    health = health.drop(
        columns=[
            "tract_centroid_latitude",
            "tract_centroid_longitude",
        ],
        errors="ignore",
    )

    health = add_polygon_centroids(
        health,
        cb3_tract_geometry,
        "GEOID",
        lat_col="tract_centroid_latitude",
        lon_col="tract_centroid_longitude",
        validate="one_to_one",
    )

    if len(health) != 31:
        raise ValueError(
            f"health_tract.csv should contain 31 CB3 tracts; found {len(health)}."
        )

    if health["GEOID"].duplicated().any():
        raise ValueError("health_tract.csv contains duplicate GEOIDs.")

    health.to_csv(HEALTH_TRACT, index=False)
    print(f"Attached tract centroids to {HEALTH_TRACT}")


def _build_nta_polygons():
    """Dissolve the shared CB3 tract universe into NTA polygons."""
    tracts, cb3_tract_geometry, _, _ = load_cb3_tract_universe(ROOT)

    nta_id = col(tracts, ["nta_code", "NTA2020", "NTACode"])
    nta_name = col(tracts, ["nta_name", "NTA2020Name", "NTAName"])

    if nta_id is None:
        raise ValueError(
            "The shared tract universe does not contain an NTA code column. "
            f"Available columns: {tracts.columns.tolist()}"
        )

    attributes = tracts.drop(columns="geometry", errors="ignore").copy()
    keep = ["GEOID", nta_id]

    if nta_name:
        keep.append(nta_name)

    polygons = cb3_tract_geometry.merge(
        attributes[keep],
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    polygons = gpd.GeoDataFrame(
        polygons,
        geometry="geometry",
        crs=cb3_tract_geometry.crs,
    )

    aggregation = {}
    if nta_name:
        aggregation[nta_name] = "first"

    dissolved = polygons.dropna(subset=[nta_id]).dissolve(
        by=nta_id,
        as_index=False,
        aggfunc=aggregation or "first",
    )

    dissolved = dissolved.rename(columns={nta_id: "nta_code"})

    if nta_name and nta_name != "nta_name":
        dissolved = dissolved.rename(columns={nta_name: "nta_name"})

    return dissolved

def _attach_nta_centroids():
    """Add shared polygon-centroid columns to the pediatric-asthma NTA table.

    The DOHMH pediatric-asthma source uses older/simple NTA labels/codes
    such as 6127 / Chinatown. The shared CB3 geography may use newer NTA
    labels such as Chinatown-Two Bridges. Because of that cross-vintage
    mismatch, do not rely on nta_code here. Match by normalized NTA name
    with a small explicit crosswalk for known label differences.
    """
    if not HEALTH_NTA.exists():
        return

    health_nta = pd.read_csv(HEALTH_NTA, dtype=str)
    nta_polygons = _build_nta_polygons()

    name_column = col(health_nta, ["nta_name", "NTA Name", "NTAName"])

    if name_column is None or "nta_name" not in nta_polygons.columns:
        raise ValueError(
            "health_nta.csv and the shared NTA polygons both need NTA names "
            "to attach pediatric-asthma centroids."
        )

    if name_column != "nta_name":
        health_nta = health_nta.rename(columns={name_column: "nta_name"})

    # Crosswalk source names from the DOHMH asthma table to the shared
    # geography names after _normalized_text() is applied.
    nta_name_crosswalk = {
        "chinatown": "chinatown two bridges",
        "chinatown two bridges": "chinatown two bridges",
        "lower east side": "lower east side",
        "east village": "east village",
    }

    health_nta["_nta_key"] = health_nta["nta_name"].map(_normalized_text)
    health_nta["_nta_key"] = health_nta["_nta_key"].replace(nta_name_crosswalk)

    nta_polygons["_nta_key"] = nta_polygons["nta_name"].map(_normalized_text)

    health_nta = health_nta.drop(
        columns=[
            "nta_centroid_latitude",
            "nta_centroid_longitude",
        ],
        errors="ignore",
    )

    health_nta = add_polygon_centroids(
        health_nta,
        nta_polygons[["_nta_key", "geometry"]].drop_duplicates("_nta_key"),
        "_nta_key",
        lat_col="nta_centroid_latitude",
        lon_col="nta_centroid_longitude",
        validate="many_to_one",
    ).drop(columns="_nta_key")

    missing_centroids = health_nta[
        health_nta["nta_centroid_latitude"].isna()
        | health_nta["nta_centroid_longitude"].isna()
    ]

    if not missing_centroids.empty:
        print(
            "Warning: NTA centroids are still missing for these asthma rows:"
        )
        print(
            missing_centroids[["nta_code", "nta_name"]]
            .drop_duplicates()
            .to_string(index=False)
        )
        print("Available shared NTA names:")
        print(
            nta_polygons[["nta_name"]]
            .drop_duplicates()
            .to_string(index=False)
        )

    health_nta.to_csv(HEALTH_NTA, index=False)
    print(f"Attached NTA centroids to {HEALTH_NTA}")

def attach_metric_centroids():
    """Apply the shared centroid helper to tract- and NTA-level health metrics."""
    _attach_tract_centroids()
    _attach_nta_centroids()



def assemble_health_table():
    """Assemble the 31-row Health tract table, following the shared domain pattern.

    Supervisor's Environmental build writes one domain-level clean CSV keyed by
    the official CB3 tract universe. This function does the same for Health by
    combining the shared tract attributes with the tract-level PLACES metrics
    already staged in health_tract.csv.
    """
    tracts, _, _, _ = load_cb3_tract_universe(ROOT)
    health_tract = pd.read_csv(HEALTH_TRACT, dtype={"GEOID": str})
    health_tract["GEOID"] = health_tract["GEOID"].map(norm_geoid)

    base_columns = [
        "GEOID",
        "tract_label",
        "tract_name",
        "nta_code",
        "nta_name",
        "cdta_code",
        "cdta_name",
    ]
    metric_columns = [
        column
        for column in [
            "total_population",
            "adult_population",
            "fair_or_poor_general_health_pct",
            "fair_or_poor_general_health_95ci",
            "diagnosed_diabetes_pct",
            "diagnosed_diabetes_95ci",
            "uninsured_adults_pct",
            "uninsured_adults_95ci",
            "source",
            "source_release",
            "estimate_type",
        ]
        if column in health_tract.columns
    ]

    clean = tracts[base_columns].copy()
    clean = clean.merge(
        health_tract[["GEOID", *metric_columns]],
        on="GEOID",
        how="left",
        validate="one_to_one",
    )
    clean = clean.sort_values("GEOID").reset_index(drop=True)

    assert len(clean) == 31
    assert clean["GEOID"].is_unique
    assert clean["GEOID"].str.fullmatch(r"\d{11}").all()

    clean.to_csv(HEALTH_OUT, index=False)

    log_lines = [
        "CB3 Health — Build Log",
        f"Output: {HEALTH_OUT}",
        "",
        "=== Tract-level metrics ===",
        "CDC PLACES tract estimates retained at census tract geography:",
        "- fair_or_poor_general_health_pct",
        "- diagnosed_diabetes_pct",
        "- uninsured_adults_pct",
        "",
        "=== Non-tract metrics ===",
        "Pediatric asthma ED rate remains at NTA geography in health_nta.csv.",
        "FQHC and mental-health providers remain point overlays using native coordinates.",
        "Life expectancy remains a community-district KPI in health_cd.csv.",
        "",
        "=== Mapping context ===",
        "Health maps join cb3_tract_baseline_profile_clean.csv in maps.qmd to add demographic context.",
    ]
    LOG_OUT.write_text("\n".join(log_lines), encoding="utf-8")

    print(f"Wrote {len(clean)} rows and {len(clean.columns)} columns to {HEALTH_OUT}")
    print(f"Wrote build log to {LOG_OUT}")
    return clean

def validate_existing():
    req={
      CLEAN/"health_tract.csv":["GEOID","fair_or_poor_general_health_pct","diagnosed_diabetes_pct","uninsured_adults_pct"],
      CLEAN/"health_nta.csv":["nta_code","nta_name","pediatric_asthma_ed_rate"],
      CLEAN/"health_providers_geocoded.csv":["latitude","longitude","inside_cb3"],
    }
    for p, fields in req.items():
        if not p.exists(): raise FileNotFoundError(p)
        missing=[x for x in fields if x not in pd.read_csv(p,nrows=3).columns]
        if missing: raise ValueError(f"{p.name} missing {missing}")

def download_omh():
    RAW.mkdir(parents=True,exist_ok=True)
    r=requests.get(OMH_URL,timeout=60,headers={"User-Agent":"CB3-health-project/1.0"})
    r.raise_for_status()
    p=RAW/"nys_omh_local_mental_health_programs.csv"
    p.write_bytes(r.content)
    return pd.read_csv(p,dtype=str,low_memory=False)

def standardize(raw):
    specs={
      "facility_name":["Facility Name","Agency Name","Provider Name","Operator Name"],
      "program_name":["Program Name","Program"],
      "site_name":["Site Name","Program Site Name","Main Site Name"],
      "program_category":["Program Category","Category","Service Category"],
      "program_subcategory":["Program Subcategory","Subcategory","Service Type","Program Type"],
      "street_address":["Address","Street Address","Program Address"],
      "address_2":["Address 2","Address Line 2"],
      "city":["City"],"state":["State"],"zip_code":["Zip","ZIP Code","Postal Code"],
      "county":["County","Program County"],"phone":["Phone","Telephone","Program Phone"],
      "program_id":["Program ID","Facility Unit Site ID","FUS ID"],
      "latitude":["Latitude"],"longitude":["Longitude"],
      "location":["Location","Georeference","Geocoded Column"],
    }
    out=pd.DataFrame(index=raw.index)
    for target,names in specs.items():
        c=col(raw,names); out[target]=raw[c] if c else pd.NA
    pts=out["location"].map(parse_point)
    out["latitude"]=pd.to_numeric(out["latitude"],errors="coerce").fillna(pts.map(lambda x:x[0]))
    out["longitude"]=pd.to_numeric(out["longitude"],errors="coerce").fillna(pts.map(lambda x:x[1]))
    out["state"]=out["state"].fillna("NY")
    out["full_address"]=out[["street_address","address_2","city","state","zip_code"]].fillna("").astype(str).apply(
        lambda r:", ".join(x.strip() for x in r if x.strip()),axis=1)
    return out

def filter_programs(df):
    county=df["county"].map(norm); city=df["city"].map(norm)
    df=df.loc[county.isin({"new york","new york county","manhattan"})|city.eq("new york")].copy()
    text=df[["program_category","program_subcategory","program_name"]].fillna("").agg(" ".join,axis=1).map(norm)
    keep=text.map(lambda x:any(t in x for t in INCLUDE)) & ~text.map(lambda x:any(t in x for t in EXCLUDE))
    return df.loc[keep].copy() if keep.any() else df

def geocode(df):
    missing=df["latitude"].isna()|df["longitude"].isna()
    df["geocode_status"]="source_coordinates"
    with requests.Session() as s:
        s.headers.update({"User-Agent":"CB3-health-project/1.0"})
        ids=df.index[missing].tolist()
        for i,idx in enumerate(ids,1):
            address=str(df.at[idx,"full_address"])
            print(f"Geocoding {i}/{len(ids)}: {address}")
            try:
                r=s.get(GEOCODER,params={"text":address,"size":1},timeout=30); r.raise_for_status()
                f=r.json().get("features",[])
                if f:
                    lon,lat=f[0]["geometry"]["coordinates"]
                    df.at[idx,"latitude"]=float(lat); df.at[idx,"longitude"]=float(lon)
                    df.at[idx,"geocode_label"]=f[0].get("properties",{}).get("label")
                    df.at[idx,"geocode_status"]="matched"
                else: df.at[idx,"geocode_status"]="no_match"
            except Exception as e:
                df.at[idx,"geocode_status"]=f"error: {e}"
            time.sleep(.2)
    return df

def assign_cb3(df):
    valid=df.dropna(subset=["latitude","longitude"]).copy()
    invalid=df.loc[~df.index.isin(valid.index)].copy()
    pts=gpd.GeoDataFrame(valid,geometry=gpd.points_from_xy(valid.longitude,valid.latitude),crs="EPSG:4326")
    tr=cb3_tracts()
    joined=gpd.sjoin(pts.to_crs(tr.crs),tr,how="left",predicate="within")
    joined["inside_cb3"]=joined["GEOID"].notna()
    joined=pd.DataFrame(joined.drop(columns=["geometry","index_right"],errors="ignore"))
    invalid["GEOID"]=pd.NA; invalid["inside_cb3"]=False
    return pd.concat([joined,invalid],ignore_index=True,sort=False)

def collapse_sites(programs):
    inside=programs.loc[programs["inside_cb3"].fillna(False)].copy()
    inside["site_key"]=inside["full_address"].map(norm)
    def uniq(s): return " | ".join(sorted({str(x).strip() for x in s.dropna() if str(x).strip()}))
    if inside.empty: return pd.DataFrame()
    sites=inside.groupby("site_key",as_index=False).agg(
      site_name=("site_name",uniq),facility_name=("facility_name",uniq),
      full_address=("full_address","first"),latitude=("latitude","first"),
      longitude=("longitude","first"),GEOID=("GEOID","first"),
      mental_health_program_count=("program_name","size"),
      program_names=("program_name",uniq),program_categories=("program_category",uniq),
      phone=("phone",uniq))
    sites.insert(0,"site_id",[f"MH_SITE_{i:03d}" for i in range(1,len(sites)+1)])
    sites["source"]="NYS OMH Local Mental Health Programs"
    sites["snapshot_date"]=pd.Timestamp.today().date().isoformat()
    return sites

def find_life_workbook():
    """Find the CHP public-use workbook regardless of case, spacing, or subfolder."""
    if LIFE_WORKBOOK.exists():
        print(f"Using life-expectancy workbook: {LIFE_WORKBOOK}")
        return LIFE_WORKBOOK

    candidates = []

    for path in RAW.rglob("*"):
        if not path.is_file():
            continue

        name = path.name.lower()

        if (
            path.suffix.lower() in {".xlsx", ".xls"}
            and "chp" in name
            and "pud" in name
        ):
            candidates.append(path)

    if not candidates:
        excel_files = sorted(
            path
            for path in RAW.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".xlsx", ".xls"}
        )

        available = (
            "\n".join(str(path) for path in excel_files)
            if excel_files
            else "(no Excel files found)"
        )

        raise FileNotFoundError(
            "Could not find a Community Health Profiles workbook. "
            "The filename may use spaces, underscores, or uppercase letters.\n"
            f"Searched recursively under: {RAW}\n"
            "Expected a filename containing both 'chp' and 'pud'.\n"
            "Excel files found:\n"
            f"{available}"
        )

    # Prefer a 2022 workbook when present; otherwise use the newest match.
    candidates = sorted(
        candidates,
        key=lambda path: (
            "2022" not in path.name.lower(),
            path.name.lower(),
        ),
    )

    workbook = candidates[0]

    print(f"Using life-expectancy workbook: {workbook}")

    return workbook


def build_life():
    """Extract MN03 life expectancy directly from the CHP workbook."""
    workbook = find_life_workbook()

    excel_file = pd.ExcelFile(workbook)

    sheet_lookup = {
        str(sheet).strip().lower(): sheet
        for sheet in excel_file.sheet_names
    }

    all_data_sheet = sheet_lookup.get(
        "chp_all_data"
    )

    if all_data_sheet is None:
        raise ValueError(
            "The workbook does not contain a CHP_all_data sheet. "
            f"Available sheets: {excel_file.sheet_names}"
        )

    all_data = pd.read_excel(
        workbook,
        sheet_name=all_data_sheet,
        header=1,
    )

    id_column = col(
        all_data,
        [
            "ID",
            "Community District",
            "Community_District",
        ],
    )

    value_column = col(
        all_data,
        [
            "Life_Expectancy",
            "Life Expectancy",
        ],
    )

    if id_column is None or value_column is None:
        raise ValueError(
            "Could not find ID and Life_Expectancy columns "
            f"in {workbook.name}. Columns include: "
            f"{all_data.columns.tolist()}"
        )

    all_data[id_column] = pd.to_numeric(
        all_data[id_column],
        errors="coerce",
    )

    row = all_data.loc[
        all_data[id_column].eq(103)
    ].copy()

    if row.empty:
        raise ValueError(
            "Community District 103 was not found in "
            f"{workbook.name}."
        )

    row = row.iloc[0]

    lower_column = None
    upper_column = None

    value_position = all_data.columns.get_loc(
        value_column
    )

    # The workbook repeats lower_95CL / upper_95CL for each metric.
    # For life expectancy, use the two columns immediately following
    # Life_Expectancy.
    if value_position + 2 < len(all_data.columns):
        lower_column = all_data.columns[
            value_position + 1
        ]
        upper_column = all_data.columns[
            value_position + 2
        ]

    period = ""
    source = (
        "NYC DOHMH, Bureau of Vital Statistics"
    )

    try:
        metadata_sheet = sheet_lookup.get(
            "metadata"
        )

        if metadata_sheet is None:
            raise ValueError(
                "Metadata sheet not found."
            )

        metadata = pd.read_excel(
            workbook,
            sheet_name=metadata_sheet,
            header=None,
        )

        for _, metadata_row in metadata.iterrows():
            values = [
                str(value).strip()
                for value in metadata_row.tolist()
                if pd.notna(value)
            ]

            if any(
                value == "Life_Expectancy"
                for value in values
            ):
                if len(values) >= 6:
                    source = values[4]
                    period = values[5]
                break

    except Exception as error:
        print(
            "Warning: could not read life-expectancy metadata:",
            error,
        )

    output = pd.DataFrame(
        [
            {
                "community_district": "MN03",
                "community_district_id": 103,
                "community_name": (
                    row.get(
                        "Community District Name",
                        row.get(
                            "Name",
                            "Lower East Side and Chinatown",
                        ),
                    )
                ),
                "metric": "life_expectancy_at_birth",
                "value": pd.to_numeric(
                    row[value_column],
                    errors="coerce",
                ),
                "lower_95cl": (
                    pd.to_numeric(
                        row[lower_column],
                        errors="coerce",
                    )
                    if lower_column is not None
                    else pd.NA
                ),
                "upper_95cl": (
                    pd.to_numeric(
                        row[upper_column],
                        errors="coerce",
                    )
                    if upper_column is not None
                    else pd.NA
                ),
                "unit": "years",
                "time_period": period,
                "source": source,
                "source_file": workbook.name,
            }
        ]
    )

    output = output.dropna(
        subset=["value"]
    )

    return output

def main():
    CLEAN.mkdir(parents=True,exist_ok=True); RAW.mkdir(parents=True,exist_ok=True)
    validate_existing()
    assemble_health_table()
    attach_metric_centroids()
    programs=assign_cb3(geocode(filter_programs(standardize(download_omh()))))
    programs["source"]="NYS OMH Local Mental Health Programs"
    programs["snapshot_date"]=pd.Timestamp.today().date().isoformat()
    programs.to_csv(PROGRAM_OUT,index=False)
    sites=collapse_sites(programs); sites.to_csv(SITE_OUT,index=False)
    life=build_life(); life.to_csv(LIFE_OUT,index=False)
    print(f"Programs inside CB3: {programs.inside_cb3.fillna(False).sum()}")
    print(f"Unique mental-health sites: {len(sites)}")
    print(f"Life-expectancy rows: {len(life)}")
    print(f"Saved {PROGRAM_OUT}\nSaved {SITE_OUT}\nSaved {LIFE_OUT}")

if __name__=="__main__":
    main()
