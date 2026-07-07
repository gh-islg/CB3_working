from __future__ import annotations

import argparse
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_C16002 = (
    REPO_ROOT / "data" / "raw" / "Language" / "acs_5yr_2024_C16002.csv"
)
DEFAULT_B16004 = (
    REPO_ROOT / "data" / "raw" / "Language" / "acs_5yr_2024_B16004.csv"
)
DEFAULT_BOUNDARY = (
    REPO_ROOT
    / "data"
    / "raw"
    / "Geography"
    / "cb3_2020_census_tracts.geojson"
)
DEFAULT_OUTPUT = REPO_ROOT / "data" / "clean" / "language.csv"


C16002_REQUIRED = [
    "C16002_001E",
    "C16002_004E",
    "C16002_007E",
    "C16002_010E",
    "C16002_013E",
]

B16004_REQUIRED = [
    "B16004_062E",
    "B16004_063E",
]


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Apply light column-name standardization."""
    result = df.copy()
    result.columns = (
        result.columns.astype(str)
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("(", "", regex=False)
        .str.replace(")", "", regex=False)
        .str.replace(".", "", regex=False)
    )
    return result


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find an exact or case-insensitive column match."""
    for candidate in candidates:
        if candidate in df.columns:
            return candidate

    lookup = {str(col).lower(): str(col) for col in df.columns}

    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]

    return None


def _extract_tract_geoid(value: object) -> str | None:
    """Extract an 11-digit census-tract GEOID from a scalar value."""
    if pd.isna(value):
        return None

    text = str(value).strip()
    matches = re.findall(r"\d{11}", text)

    if matches:
        return matches[-1]

    digits = re.sub(r"\D", "", text)

    if 1 <= len(digits) <= 11:
        return digits.zfill(11)

    return None


def _add_geoid(df: pd.DataFrame) -> pd.DataFrame:
    """Create a normalized 11-digit census-tract GEOID."""
    result = df.copy()

    geoid_col = _find_column(
        result,
        [
            "GEOID",
            "geoid",
            "GeoID",
            "GEO_ID",
            "Geography_ID",
            "Census_Tract",
            "tract_geoid",
            "tract_fips",
        ],
    )

    if geoid_col is not None:
        result["GEOID"] = result[geoid_col].map(_extract_tract_geoid)
        return result

    state_col = _find_column(result, ["state", "STATE"])
    county_col = _find_column(result, ["county", "COUNTY"])
    tract_col = _find_column(result, ["tract", "TRACT"])

    if state_col and county_col and tract_col:
        state = (
            result[state_col]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

        county = (
            result[county_col]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

        tract = (
            result[tract_col]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

        result["GEOID"] = (
            state.str.zfill(2)
            + county.str.zfill(3)
            + tract.str.zfill(6)
        )

        return result

    raise ValueError(
        "Could not identify or construct GEOID. "
        f"Available columns: {result.columns.tolist()}"
    )


def _load_acs_csv(path: Path) -> pd.DataFrame:
    """Load and normalize an ACS CSV."""
    if not path.exists():
        raise FileNotFoundError(
            f"Missing input file: {path}"
        )

    df = pd.read_csv(
        path,
        dtype=str,
    )

    df = _standardize_columns(df)
    df = _add_geoid(df)

    if df["GEOID"].isna().any():
        bad_rows = int(
            df["GEOID"].isna().sum()
        )

        raise ValueError(
            f"{path.name}: {bad_rows} rows have invalid GEOIDs."
        )

    return df


def _require_columns(
    df: pd.DataFrame,
    columns: list[str],
    dataset_name: str,
) -> None:
    """Raise a clear error when required source columns are absent."""
    missing = [
        col
        for col in columns
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"{dataset_name} is missing required columns: "
            f"{missing}. "
            f"Available columns include: "
            f"{df.columns.tolist()[:100]}"
        )


def _to_numeric(
    df: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """Convert selected ACS estimate fields to numeric."""
    result = df.copy()

    for col in columns:
        result[col] = pd.to_numeric(
            result[col]
            .astype(str)
            .str.replace(",", "", regex=False),
            errors="coerce",
        )

    return result


def _clean_census_values(
    df: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """Replace negative ACS sentinel values with missing values."""
    result = df.copy()

    for col in columns:
        result[col] = result[col].mask(
            result[col] < 0
        )

    return result


def _load_cb3_geoids(
    path: Path,
) -> pd.DataFrame:
    """Read the official CB3 tract universe from the boundary file."""
    if not path.exists():
        raise FileNotFoundError(
            f"Missing CB3 boundary file: {path}"
        )

    boundaries = gpd.read_file(path)
    boundaries = _add_geoid(boundaries)

    geoids = (
        boundaries[["GEOID"]]
        .dropna()
        .drop_duplicates()
        .sort_values("GEOID")
        .reset_index(drop=True)
    )

    if geoids["GEOID"].duplicated().any():
        raise ValueError(
            "CB3 boundary file contains duplicate GEOIDs."
        )

    return pd.DataFrame(geoids)


def _build_c16002_metrics(
    c16002: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate tract-level LEP household metrics."""
    _require_columns(
        c16002,
        C16002_REQUIRED,
        "ACS C16002",
    )

    c16002 = _to_numeric(
        c16002,
        C16002_REQUIRED,
    )

    c16002 = _clean_census_values(
        c16002,
        C16002_REQUIRED,
    )

    output_cols = ["GEOID"]

    if "NAME" in c16002.columns:
        output_cols.append("NAME")

    result = c16002[
        output_cols
    ].copy()

    result[
        "total_households_language_universe"
    ] = c16002[
        "C16002_001E"
    ]

    result[
        "lep_households"
    ] = c16002[
        [
            "C16002_004E",
            "C16002_007E",
            "C16002_010E",
            "C16002_013E",
        ]
    ].sum(
        axis=1,
        min_count=1,
    )

    denominator = result[
        "total_households_language_universe"
    ]

    result[
        "lep_household_share"
    ] = np.where(
        denominator > 0,
        (
            result["lep_households"]
            / denominator
            * 100
        ),
        np.nan,
    )

    return (
        result
        .dropna(subset=["GEOID"])
        .drop_duplicates(subset=["GEOID"])
        .reset_index(drop=True)
    )


def _build_b16004_metrics(
    b16004: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate the senior Asian/Pacific-language LEP count."""
    _require_columns(
        b16004,
        B16004_REQUIRED,
        "ACS B16004",
    )

    b16004 = _to_numeric(
        b16004,
        B16004_REQUIRED,
    )

    b16004 = _clean_census_values(
        b16004,
        B16004_REQUIRED,
    )

    result = b16004[
        ["GEOID"]
    ].copy()

    result[
        "senior_asian_pacific_lep_count"
    ] = b16004[
        B16004_REQUIRED
    ].sum(
        axis=1,
        min_count=1,
    )

    return (
        result
        .dropna(subset=["GEOID"])
        .drop_duplicates(subset=["GEOID"])
        .reset_index(drop=True)
    )


def build_language_metrics(
    c16002_path: Path,
    b16004_path: Path,
    boundary_path: Path,
) -> pd.DataFrame:
    """Build and validate the final CB3 Language Access table."""
    c16002 = _load_acs_csv(
        c16002_path
    )

    b16004 = _load_acs_csv(
        b16004_path
    )

    cb3_geoids = _load_cb3_geoids(
        boundary_path
    )

    c16002_metrics = _build_c16002_metrics(
        c16002
    )

    b16004_metrics = _build_b16004_metrics(
        b16004
    )

    metrics = c16002_metrics.merge(
        b16004_metrics,
        on="GEOID",
        how="outer",
        validate="one_to_one",
    )

    result = cb3_geoids.merge(
        metrics,
        on="GEOID",
        how="left",
        validate="one_to_one",
    )

    metric_columns = [
        "total_households_language_universe",
        "lep_households",
        "lep_household_share",
        "senior_asian_pacific_lep_count",
    ]

    missing_counts = result[
        metric_columns
    ].isna().sum()

    # Missing shares are acceptable only when
    # the denominator is zero.
    invalid_share = result[
        result["lep_household_share"].isna()
        & result[
            "total_households_language_universe"
        ].gt(0)
    ]

    if not invalid_share.empty:
        raise ValueError(
            "LEP household share is missing despite a "
            "positive denominator for GEOIDs: "
            f"{invalid_share['GEOID'].tolist()}"
        )

    if result["GEOID"].duplicated().any():
        raise ValueError(
            "Final language output contains duplicate GEOIDs."
        )

    result[
        "language_access_methodology_note"
    ] = (
        "lep_household_share = "
        "(C16002_004E + C16002_007E + "
        "C16002_010E + C16002_013E) "
        "/ C16002_001E * 100. "
        "senior_asian_pacific_lep_count = "
        "B16004_062E + B16004_063E."
    )

    result = result[
        [
            "GEOID",
            "NAME",
            "total_households_language_universe",
            "lep_households",
            "lep_household_share",
            "senior_asian_pacific_lep_count",
            "language_access_methodology_note",
        ]
    ].sort_values(
        "GEOID"
    ).reset_index(
        drop=True
    )

    print(
        f"CB3 tract rows: {len(result)}"
    )

    print("Missing values:")
    print(
        missing_counts.to_string()
    )

    print(
        "LEP household share range:",
        result[
            "lep_household_share"
        ].min(),
        "to",
        result[
            "lep_household_share"
        ].max(),
    )

    print(
        "Senior Asian/Pacific-language LEP total:",
        result[
            "senior_asian_pacific_lep_count"
        ].sum(),
    )

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build CB3 Language Access metrics."
        )
    )

    parser.add_argument(
        "--c16002",
        type=Path,
        default=DEFAULT_C16002,
    )

    parser.add_argument(
        "--b16004",
        type=Path,
        default=DEFAULT_B16004,
    )

    parser.add_argument(
        "--boundary",
        type=Path,
        default=DEFAULT_BOUNDARY,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output = build_language_metrics(
        c16002_path=args.c16002,
        b16004_path=args.b16004,
        boundary_path=args.boundary,
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.to_csv(
        args.output,
        index=False,
    )

    print(
        f"Saved: {args.output}"
    )


if __name__ == "__main__":
    main()