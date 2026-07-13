from pathlib import Path
import sys

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.cb3_utils import load_cb3_tract_universe


RAW_DIR = PROJECT_DIR / "data" / "raw" / "Baseline"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

C16002_PATH = RAW_DIR / "acs_5yr_2024_C16002.csv"
B16001_PATH = RAW_DIR / "acs_5yr_2024_B16001.csv"

LANGUAGE_OUT = CLEAN_DIR / "language.csv"
LOG_OUT = CLEAN_DIR / "language_log.txt"

# Look for demographic columns in the same practical places used/created by the CB3 workflow.
# Health maps often work because one of these files is already available in the health branch/stash.
DEMOGRAPHIC_SOURCE_CANDIDATES = [
    CLEAN_DIR / "tract_baseline_profile_clean.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk.csv",
    CLEAN_DIR / "tract_baseline_profile_partial_before_crosswalk_clean.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile.csv",
    CLEAN_DIR / "cb3_tract_baseline_profile_clean.csv",
    CLEAN_DIR / "health_tract.csv",
    CLEAN_DIR / "health.csv",
    CLEAN_DIR / "baseline" / "tract_baseline_profile_clean.csv",
    CLEAN_DIR / "baseline" / "tract_baseline_profile_partial_before_crosswalk.csv",
]

DEMOGRAPHIC_COLUMNS = [
    "median_household_income",
    "age_0_to_19_share",
    "age_20_to_64_share",
    "age_65_plus_share",
    "white_non_hispanic_share",
    "black_non_hispanic_share",
    "asian_non_hispanic_share",
    "hispanic_share",
    "poverty_rate",
]

B16001_LANGUAGE_GROUPS = {
    "spanish": {
        "speaker_col": "B16001_003E",
        "very_well_col": "B16001_004E",
        "lep_col": "B16001_005E",
    },
    "french_haitian_cajun": {
        "speaker_col": "B16001_006E",
        "very_well_col": "B16001_007E",
        "lep_col": "B16001_008E",
    },
    "russian_slavic": {
        "speaker_col": "B16001_012E",
        "very_well_col": "B16001_013E",
        "lep_col": "B16001_014E",
    },
    "korean": {
        "speaker_col": "B16001_018E",
        "very_well_col": "B16001_019E",
        "lep_col": "B16001_020E",
    },
    "chinese": {
        "speaker_col": "B16001_021E",
        "very_well_col": "B16001_022E",
        "lep_col": "B16001_023E",
    },
    "vietnamese": {
        "speaker_col": "B16001_024E",
        "very_well_col": "B16001_025E",
        "lep_col": "B16001_026E",
    },
    "tagalog": {
        "speaker_col": "B16001_027E",
        "very_well_col": "B16001_028E",
        "lep_col": "B16001_029E",
    },
    "arabic": {
        "speaker_col": "B16001_033E",
        "very_well_col": "B16001_034E",
        "lep_col": "B16001_035E",
    },
}


def normalize_geoid(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(11)


def make_geoid(df: pd.DataFrame) -> pd.Series:
    if "GEOID" in df.columns:
        return normalize_geoid(df["GEOID"])
    if "geoid" in df.columns:
        return normalize_geoid(df["geoid"])
    if "GEOID20" in df.columns:
        return normalize_geoid(df["GEOID20"])
    if {"state", "county", "tract"}.issubset(df.columns):
        return (
            df["state"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
            + df["county"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
            + df["tract"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        )
    raise KeyError(f"Could not create GEOID from columns: {list(df.columns)}")


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return (num / den).where(den.notna() & (den != 0))


def read_acs(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required ACS file: {path}")
    df = pd.read_csv(path, dtype=str)
    df["GEOID"] = make_geoid(df)

    id_cols = {"GEOID", "NAME", "state", "county", "tract", "geoid", "GEOID20"}
    for col in df.columns:
        if col not in id_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_c16002_metrics(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["GEOID", "NAME", "C16002_001E"]].copy()
    out = out.rename(columns={"C16002_001E": "households_language_universe"})

    # Supports both common column layouts we have seen in local extracts.
    if {"C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"}.issubset(df.columns):
        out["spanish_limited_english_households"] = df["C16002_004E"]
        out["other_indo_european_limited_english_households"] = df["C16002_007E"]
        out["asian_pacific_limited_english_households"] = df["C16002_010E"]
        out["other_languages_limited_english_households"] = df["C16002_013E"]
        layout = "C16002 broad-language layout A: 004/007/010/013"
    elif {"C16002_004E", "C16002_006E", "C16002_008E", "C16002_010E"}.issubset(df.columns):
        out["spanish_limited_english_households"] = df["C16002_004E"]
        out["other_indo_european_limited_english_households"] = df["C16002_006E"]
        out["asian_pacific_limited_english_households"] = df["C16002_008E"]
        out["other_languages_limited_english_households"] = df["C16002_010E"]
        layout = "C16002 broad-language layout B: 004/006/008/010"
    else:
        raise KeyError("Could not find expected C16002 LEP household columns.")

    group_cols = [
        "spanish_limited_english_households",
        "other_indo_european_limited_english_households",
        "asian_pacific_limited_english_households",
        "other_languages_limited_english_households",
    ]

    out["lep_households"] = out[group_cols].sum(axis=1, min_count=1)
    out["lep_household_share"] = safe_div(out["lep_households"], out["households_language_universe"]) * 100

    for col in group_cols:
        share_col = col.replace("_households", "_household_share")
        out[share_col] = safe_div(out[col], out["households_language_universe"]) * 100

    return out, layout


def build_b16001_metrics(df: pd.DataFrame, cb3_geoids: set[str]) -> tuple[pd.DataFrame, list[str]]:
    out = pd.DataFrame({"GEOID": df["GEOID"]})
    log_lines = []

    if "B16001_001E" not in df.columns:
        return out, ["B16001_001E missing; detailed language metrics skipped."]

    out["b16001_population_age_5_plus"] = pd.to_numeric(df["B16001_001E"], errors="coerce")
    cb3_df = df[df["GEOID"].isin(cb3_geoids)].copy()

    usable_count = 0
    for language_key, spec in B16001_LANGUAGE_GROUPS.items():
        needed = [spec["speaker_col"], spec["very_well_col"], spec["lep_col"]]
        if not all(col in df.columns for col in needed):
            log_lines.append(f"{language_key}: missing one or more required B16001 columns; skipped.")
            continue

        cb3_lep = pd.to_numeric(cb3_df[spec["lep_col"]], errors="coerce")
        cb3_speaker = pd.to_numeric(cb3_df[spec["speaker_col"]], errors="coerce")

        # Do not create fake zero columns when the source extract is blank/unusable.
        if cb3_lep.notna().sum() == 0 and cb3_speaker.notna().sum() == 0:
            log_lines.append(f"{language_key}: B16001 values are blank for CB3; skipped.")
            continue

        if cb3_lep.sum(skipna=True) <= 0 and cb3_speaker.sum(skipna=True) <= 0:
            log_lines.append(f"{language_key}: B16001 values are all zero for CB3; skipped to avoid misleading empty map.")
            continue

        out[f"{language_key}_speaker_population_age_5_plus"] = pd.to_numeric(df[spec["speaker_col"]], errors="coerce")
        out[f"{language_key}_speaks_english_very_well_population_age_5_plus"] = pd.to_numeric(df[spec["very_well_col"]], errors="coerce")
        out[f"{language_key}_limited_english_population_age_5_plus"] = pd.to_numeric(df[spec["lep_col"]], errors="coerce")

        out[f"{language_key}_limited_english_share"] = (
            safe_div(out[f"{language_key}_limited_english_population_age_5_plus"], out["b16001_population_age_5_plus"]) * 100
        )
        out[f"{language_key}_speaker_share"] = (
            safe_div(out[f"{language_key}_speaker_population_age_5_plus"], out["b16001_population_age_5_plus"]) * 100
        )
        out[f"{language_key}_limited_english_within_speakers_share"] = (
            safe_div(
                out[f"{language_key}_limited_english_population_age_5_plus"],
                out[f"{language_key}_speaker_population_age_5_plus"],
            )
            * 100
        )

        usable_count += 1
        log_lines.append(
            f"{language_key}: added; CB3 LEP total={cb3_lep.sum(skipna=True):,.0f}; "
            f"CB3 speaker total={cb3_speaker.sum(skipna=True):,.0f}"
        )

    if usable_count == 0:
        log_lines.append("No detailed B16001 language metrics were usable for CB3.")

    return out, log_lines


def find_demographic_source(cb3_geoids: set[str]) -> tuple[pd.DataFrame, list[str]]:
    log_lines = []
    checked = []

    for path in DEMOGRAPHIC_SOURCE_CANDIDATES:
        checked.append(str(path))
        if not path.exists():
            continue

        try:
            df = pd.read_csv(path, dtype={"GEOID": "string", "geoid": "string", "GEOID20": "string"})
            df["GEOID"] = make_geoid(df)
        except Exception as exc:
            log_lines.append(f"Could not read demographic candidate {path}: {exc}")
            continue

        available = [col for col in DEMOGRAPHIC_COLUMNS if col in df.columns]
        if not available:
            log_lines.append(f"Candidate found but no expected demographic columns: {path}")
            continue

        out = df[["GEOID"] + available].copy()
        out = out[out["GEOID"].isin(cb3_geoids)].copy()
        for col in available:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        if out.empty or not any(out[col].notna().any() for col in available):
            log_lines.append(f"Candidate has expected columns but no populated CB3 values: {path}")
            continue

        log_lines.append(f"Using demographic source: {path}")
        log_lines.append(f"Added demographic columns: {', '.join(available)}")
        return out, log_lines

    log_lines.append("No usable demographic source found.")
    log_lines.append("Checked paths:")
    for path in checked:
        log_lines.append(f"  {path}")
    return pd.DataFrame({"GEOID": sorted(cb3_geoids)}), log_lines


def main() -> None:
    tract_universe, _, _, _ = load_cb3_tract_universe(PROJECT_DIR)
    cb3_lookup = tract_universe.drop(columns="geometry", errors="ignore").copy()
    cb3_lookup["GEOID"] = normalize_geoid(cb3_lookup["GEOID"])
    cb3_geoids = set(cb3_lookup["GEOID"])

    log_lines = []

    c16002 = read_acs(C16002_PATH)
    c16002_metrics, c16002_layout = build_c16002_metrics(c16002)

    language = cb3_lookup.merge(
        c16002_metrics,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    log_lines.append(f"Wrote {LANGUAGE_OUT}")
    log_lines.append(f"Rows: {len(language)}")
    log_lines.append("")
    log_lines.append(f"C16002 source: {C16002_PATH}")
    log_lines.append(f"C16002 layout used: {c16002_layout}")
    log_lines.append("C16002 household-level language metrics:")

    for metric in [
        "lep_household_share",
        "spanish_limited_english_household_share",
        "other_indo_european_limited_english_household_share",
        "asian_pacific_limited_english_household_share",
        "other_languages_limited_english_household_share",
    ]:
        log_lines.append(
            f"  {metric}: {language[metric].notna().sum()} non-null; "
            f"max={language[metric].max(skipna=True):.2f}%"
        )

    log_lines.append("")
    log_lines.append("B16001 person-level detailed language metrics:")
    if B16001_PATH.exists():
        b16001 = read_acs(B16001_PATH)
        b16001_metrics, b16001_log = build_b16001_metrics(b16001, cb3_geoids)
        b16001_cols = [col for col in b16001_metrics.columns if col == "GEOID" or col not in language.columns]
        language = language.merge(
            b16001_metrics[b16001_cols],
            on="GEOID",
            how="left",
            validate="one_to_one",
        )
        log_lines.extend([f"  {line}" for line in b16001_log])
    else:
        log_lines.append(f"  Missing {B16001_PATH}; detailed language metrics skipped.")

    log_lines.append("")
    log_lines.append("Demographic context columns:")
    demographics, demo_log = find_demographic_source(cb3_geoids)
    demo_cols = [col for col in demographics.columns if col == "GEOID" or col not in language.columns]
    language = language.merge(
        demographics[demo_cols],
        on="GEOID",
        how="left",
        validate="one_to_one",
    )
    log_lines.extend([f"  {line}" for line in demo_log])

    language.to_csv(LANGUAGE_OUT, index=False)

    log_lines.append("")
    log_lines.append("Final language.csv columns:")
    for col in language.columns:
        log_lines.append(f"  {col}")

    log_lines.append("")
    log_lines.append("Note: C16002 metrics are household-level. B16001 metrics, if present, are person-level age 5+.")

    LOG_OUT.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print("\n".join(log_lines))


if __name__ == "__main__":
    main()
