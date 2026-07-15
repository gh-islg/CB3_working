from pathlib import Path
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "Baseline"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

OUT = CLEAN_DIR / "tract_baseline_profile_clean.csv"

def make_geoid(df):
    if "GEOID" in df.columns:
        return df["GEOID"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(11)
    if "geoid" in df.columns:
        return df["geoid"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(11)

    state = df["state"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
    county = df["county"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
    tract = df["tract"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return state + county + tract

def read_acs(filename):
    df = pd.read_csv(RAW_DIR / filename, dtype=str)
    df["GEOID"] = make_geoid(df)
    for col in df.columns:
        if col not in {"GEOID", "NAME", "state", "county", "tract", "geoid"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def safe_div(num, den):
    return (num / den).where(den.notna() & (den != 0))

def require_cols(df, cols, source):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{source} is missing columns: {missing}")

# Use health_tract.csv if available to restrict to CB3 tracts.
health_tract_path = CLEAN_DIR / "health_tract.csv"
cb3_geoids = None
if health_tract_path.exists():
    cb3_geoids = pd.read_csv(health_tract_path, dtype={"GEOID": str})["GEOID"].astype(str).str.zfill(11)

# -----------------------
# Age from B01001
# -----------------------
age = read_acs("acs_5yr_2024_B01001.csv")

age_0_19_cols = [
    "B01001_003E", "B01001_004E", "B01001_005E", "B01001_006E", "B01001_007E",
    "B01001_027E", "B01001_028E", "B01001_029E", "B01001_030E", "B01001_031E",
]
age_65_plus_cols = [
    "B01001_020E", "B01001_021E", "B01001_022E", "B01001_023E", "B01001_024E", "B01001_025E",
    "B01001_044E", "B01001_045E", "B01001_046E", "B01001_047E", "B01001_048E", "B01001_049E",
]

require_cols(age, ["B01003_001E"] + age_0_19_cols + age_65_plus_cols, "B01001")

age["total_population"] = age["B01003_001E"]
age["age_0_to_19_population"] = age[age_0_19_cols].sum(axis=1, min_count=1)
age["age_65_plus_population"] = age[age_65_plus_cols].sum(axis=1, min_count=1)
age["age_20_to_64_population"] = (
    age["total_population"] - age["age_0_to_19_population"] - age["age_65_plus_population"]
)

age["age_0_to_19_share"] = safe_div(age["age_0_to_19_population"], age["total_population"]) * 100
age["age_20_to_64_share"] = safe_div(age["age_20_to_64_population"], age["total_population"]) * 100
age["age_65_plus_share"] = safe_div(age["age_65_plus_population"], age["total_population"]) * 100

age_keep = age[
    [
        "GEOID",
        "total_population",
        "age_0_to_19_share",
        "age_20_to_64_share",
        "age_65_plus_share",
    ]
].copy()

# -----------------------
# Race / ethnicity from B03002
# -----------------------
race = read_acs("acs_5yr_2024_B03002.csv")
require_cols(
    race,
    ["B03002_001E", "B03002_003E", "B03002_004E", "B03002_006E", "B03002_012E"],
    "B03002",
)

race["race_ethnicity_total"] = race["B03002_001E"]
race["white_non_hispanic_share"] = safe_div(race["B03002_003E"], race["race_ethnicity_total"]) * 100
race["black_non_hispanic_share"] = safe_div(race["B03002_004E"], race["race_ethnicity_total"]) * 100
race["asian_non_hispanic_share"] = safe_div(race["B03002_006E"], race["race_ethnicity_total"]) * 100
race["hispanic_share"] = safe_div(race["B03002_012E"], race["race_ethnicity_total"]) * 100

race_keep = race[
    [
        "GEOID",
        "white_non_hispanic_share",
        "black_non_hispanic_share",
        "asian_non_hispanic_share",
        "hispanic_share",
    ]
].copy()

# -----------------------
# Median household income from B19013
# -----------------------
income = read_acs("acs_5yr_2024_B19013.csv")
require_cols(income, ["B19013_001E"], "B19013")

income_keep = income[["GEOID", "B19013_001E"]].rename(
    columns={"B19013_001E": "median_household_income"}
)

# -----------------------
# LEP household share from C16002
# -----------------------
lep = read_acs("acs_5yr_2024_C16002.csv")
require_cols(lep, ["C16002_001E", "C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"], "C16002")

lep["total_households_language_universe"] = lep["C16002_001E"]
lep["lep_households"] = lep[["C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"]].sum(axis=1, min_count=1)
lep["lep_household_share"] = safe_div(
    lep["lep_households"],
    lep["total_households_language_universe"],
) * 100

lep_keep = lep[
    [
        "GEOID",
        "total_households_language_universe",
        "lep_households",
        "lep_household_share",
    ]
].copy()

# -----------------------
# Poverty: prefer existing clean economic-food.csv if available
# -----------------------
poverty_keep = None
economic_path = CLEAN_DIR / "economic-food.csv"
if economic_path.exists():
    econ = pd.read_csv(economic_path, dtype={"GEOID": str, "geoid": str})
    econ["GEOID"] = make_geoid(econ)

    rename = {}
    if "poverty_rate_pct" in econ.columns and "poverty_rate" not in econ.columns:
        rename["poverty_rate_pct"] = "poverty_rate"
    if "senior_poverty_rate_pct" in econ.columns and "senior_poverty_rate" not in econ.columns:
        rename["senior_poverty_rate_pct"] = "senior_poverty_rate"
    econ = econ.rename(columns=rename)

    keep_cols = ["GEOID"] + [
        col for col in ["poverty_rate", "senior_poverty_rate"]
        if col in econ.columns
    ]
    if len(keep_cols) > 1:
        poverty_keep = econ[keep_cols].copy()

# If economic-food.csv is not usable, try standard B17020 columns.
if poverty_keep is None:
    poverty = read_acs("acs_5yr_2024_B17020.csv")

    if {"B17020_001E", "B17020_002E"}.issubset(poverty.columns):
        poverty["poverty_rate"] = safe_div(poverty["B17020_002E"], poverty["B17020_001E"]) * 100

        senior_cols = ["B17020_007E", "B17020_008E", "B17020_014E", "B17020_015E"]
        if set(senior_cols).issubset(poverty.columns):
            senior_poverty_num = poverty["B17020_007E"] + poverty["B17020_008E"]
            senior_poverty_den = (
                poverty["B17020_007E"]
                + poverty["B17020_008E"]
                + poverty["B17020_014E"]
                + poverty["B17020_015E"]
            )
            poverty["senior_poverty_rate"] = safe_div(senior_poverty_num, senior_poverty_den) * 100

        keep_cols = ["GEOID"] + [
            col for col in ["poverty_rate", "senior_poverty_rate"]
            if col in poverty.columns
        ]
        poverty_keep = poverty[keep_cols].copy()
    else:
        poverty_keep = pd.DataFrame({"GEOID": age_keep["GEOID"]})

# -----------------------
# Merge
# -----------------------
baseline = age_keep.merge(race_keep, on="GEOID", how="outer", validate="one_to_one")
baseline = baseline.merge(income_keep, on="GEOID", how="outer", validate="one_to_one")
baseline = baseline.merge(lep_keep, on="GEOID", how="outer", validate="one_to_one")
baseline = baseline.merge(poverty_keep, on="GEOID", how="outer", validate="one_to_one")

if cb3_geoids is not None:
    baseline = baseline[baseline["GEOID"].isin(cb3_geoids)].copy()

baseline = baseline.sort_values("GEOID").reset_index(drop=True)

wanted = [
    "GEOID",
    "total_population",
    "median_household_income",
    "age_0_to_19_share",
    "age_20_to_64_share",
    "age_65_plus_share",
    "white_non_hispanic_share",
    "black_non_hispanic_share",
    "asian_non_hispanic_share",
    "hispanic_share",
    "lep_household_share",
    "poverty_rate",
    "senior_poverty_rate",
]

baseline = baseline[[col for col in wanted if col in baseline.columns]]
baseline.to_csv(OUT, index=False)

print(f"Wrote {OUT}")
print(f"Rows: {len(baseline)}")
print("Columns:")
for col in baseline.columns:
    nonnull = baseline[col].notna().sum()
    print(f"  {col}: {nonnull} non-null")
