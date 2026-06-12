from pathlib import Path

import duckdb
import pandas as pd

from src.utils.log import setup_logger


# ============================================================
# Paths
# ============================================================

PROJECT_ROOT = Path.cwd()

DATABASE_PATH = (
    PROJECT_ROOT
    / "database"
    / "alternative_data.duckdb"
)

METADATA_PATH = (
    PROJECT_ROOT
    / "data"
    / "metadata.xlsx"
)

INDEX_DATA_PATH = (
    PROJECT_ROOT
    / "data_lake"
    / "raw"
    / "index"
)

MACRO_DATA_PATH = (
    PROJECT_ROOT
    / "data_lake"
    / "raw"
    / "macro"
)

LOGGER = setup_logger(
    name=__name__,
    log_path="logs/jobs/build_duckdb.log",
)


# ============================================================
# Table names
# ============================================================

METADATA_TABLE = "metadata.instrument_master"
INDEX_TABLE = "market.index_data"
MACRO_TABLE = "macro.macro_data"


# ============================================================
# Expected columns
# ============================================================

INDEX_COLUMNS = [
    "base_date",
    "release_date",
    "time",
    "time_zone",
    "symbol",
    "exchange",
    "country",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

MACRO_COLUMNS = [
    "base_date",
    "release_date",
    "time",
    "time_zone",
    "symbol",
    "exchange",
    "country",
    "actual",
    "forecast",
    "previous",
]


# ============================================================
# Utility functions
# ============================================================

def get_parquet_files(
    directory: Path,
) -> list[Path]:
    """
    Recursively returns all Parquet files under the target directory.
    """

    if not directory.exists():
        raise FileNotFoundError(
            f"Data directory not found: {directory}"
        )

    parquet_files = sorted(
        directory.rglob("*.parquet")
    )

    if not parquet_files:
        raise FileNotFoundError(
            f"No Parquet files found in: {directory}"
        )

    return parquet_files


def validate_parquet_columns(
    parquet_files: list[Path],
    required_columns: list[str],
    data_type_name: str,
) -> None:
    """
    Validates that every Parquet file contains all required columns.
    """

    required_column_set = set(required_columns)

    for file_path in parquet_files:
        columns = (
            pd.read_parquet(file_path)
            .columns
            .tolist()
        )

        normalized_columns = {
            str(column).strip().lower()
            for column in columns
        }

        missing_columns = (
            required_column_set
            - normalized_columns
        )

        if missing_columns:
            raise ValueError(
                f"Required columns are missing from "
                f"{data_type_name} data.\n"
                f"File: {file_path}\n"
                f"Missing columns: {sorted(missing_columns)}"
            )

    LOGGER.info(
        "Parquet schema validation completed | "
        "data_type=%s | files=%d",
        data_type_name,
        len(parquet_files),
    )


def normalize_metadata_columns(
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    """
    Normalizes metadata column names to lowercase snake_case
    and trims string values.
    """

    metadata = metadata.copy()

    metadata.columns = (
        metadata.columns
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
    )

    string_columns = metadata.select_dtypes(
        include=["object", "string"]
    ).columns

    for column in string_columns:
        metadata[column] = (
            metadata[column]
            .astype("string")
            .str.strip()
        )

        metadata[column] = metadata[column].replace(
            {
                "": pd.NA,
                "nan": pd.NA,
                "None": pd.NA,
                "<NA>": pd.NA,
            }
        )

    return metadata


def build_parquet_path_list(
    parquet_files: list[Path],
) -> str:
    """
    Builds a DuckDB-compatible SQL list of Parquet file paths.
    """

    escaped_paths = [
        file_path
        .resolve()
        .as_posix()
        .replace("'", "''")
        for file_path in parquet_files
    ]

    return "[" + ", ".join(
        f"'{path}'"
        for path in escaped_paths
    ) + "]"


# ============================================================
# Schema creation
# ============================================================

def create_schemas(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """
    Creates the required DuckDB schemas.
    """

    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS metadata
        """
    )

    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS market
        """
    )

    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS macro
        """
    )

    LOGGER.info(
        "DuckDB schemas created or verified | "
        "schemas=metadata,market,macro"
    )


# ============================================================
# Table builders
# ============================================================

def create_metadata_table(
    connection: duckdb.DuckDBPyConnection,
) -> int:
    """
    Creates metadata.instrument_master from data/metadata.xlsx.
    """

    if not METADATA_PATH.exists():
        raise FileNotFoundError(
            f"Metadata file not found: {METADATA_PATH}"
        )

    LOGGER.info(
        "Loading metadata Excel file | path=%s",
        METADATA_PATH,
    )

    metadata = pd.read_excel(
        METADATA_PATH,
        sheet_name="Master",
    )

    metadata = normalize_metadata_columns(
        metadata
    )

    if metadata.empty:
        raise ValueError(
            "The Master sheet in metadata.xlsx is empty."
        )

    connection.register(
        "metadata_dataframe",
        metadata,
    )

    try:
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE {METADATA_TABLE} AS
            SELECT *
            FROM metadata_dataframe
            """
        )

    finally:
        connection.unregister(
            "metadata_dataframe"
        )

    row_count = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {METADATA_TABLE}
        """
    ).fetchone()[0]

    LOGGER.info(
        "Metadata table created | "
        "table=%s | rows=%d",
        METADATA_TABLE,
        row_count,
    )

    return row_count


def create_index_table(
    connection: duckdb.DuckDBPyConnection,
    parquet_files: list[Path],
) -> int:
    """
    Combines all Index Parquet files into market.index_data.
    """

    parquet_paths = build_parquet_path_list(
        parquet_files
    )

    query = f"""
        CREATE OR REPLACE TABLE {INDEX_TABLE} AS
        SELECT
            CAST(base_date AS DATE) AS base_date,
            CAST(release_date AS DATE) AS release_date,
            CAST(time AS TIME) AS time,
            CAST(time_zone AS VARCHAR) AS time_zone,
            CAST(symbol AS VARCHAR) AS symbol,
            CAST(exchange AS VARCHAR) AS exchange,
            CAST(country AS VARCHAR) AS country,
            CAST(open AS DOUBLE) AS open,
            CAST(high AS DOUBLE) AS high,
            CAST(low AS DOUBLE) AS low,
            CAST(close AS DOUBLE) AS close,
            CAST(volume AS DOUBLE) AS volume
        FROM read_parquet(
            {parquet_paths},
            union_by_name = TRUE
        )
    """

    connection.execute(query)

    row_count = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {INDEX_TABLE}
        """
    ).fetchone()[0]

    LOGGER.info(
        "Index table created | "
        "table=%s | files=%d | rows=%d",
        INDEX_TABLE,
        len(parquet_files),
        row_count,
    )

    return row_count


def create_macro_table(
    connection: duckdb.DuckDBPyConnection,
    parquet_files: list[Path],
) -> int:
    """
    Combines all Macro Parquet files into macro.macro_data.
    """

    parquet_paths = build_parquet_path_list(
        parquet_files
    )

    query = f"""
        CREATE OR REPLACE TABLE {MACRO_TABLE} AS
        SELECT
            CAST(base_date AS DATE) AS base_date,
            CAST(release_date AS DATE) AS release_date,
            CAST(time AS TIME) AS time,
            CAST(time_zone AS VARCHAR) AS time_zone,
            CAST(symbol AS VARCHAR) AS symbol,
            CAST(exchange AS VARCHAR) AS exchange,
            CAST(country AS VARCHAR) AS country,
            CAST(actual AS DOUBLE) AS actual,
            CAST(forecast AS DOUBLE) AS forecast,
            CAST(previous AS DOUBLE) AS previous
        FROM read_parquet(
            {parquet_paths},
            union_by_name = TRUE
        )
    """

    connection.execute(query)

    row_count = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {MACRO_TABLE}
        """
    ).fetchone()[0]

    LOGGER.info(
        "Macro table created | "
        "table=%s | files=%d | rows=%d",
        MACRO_TABLE,
        len(parquet_files),
        row_count,
    )

    return row_count


# ============================================================
# Database validation
# ============================================================

def validate_database(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    """
    Performs basic data-quality validation after table creation.
    """

    index_null_keys = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {INDEX_TABLE}
        WHERE base_date IS NULL
           OR symbol IS NULL
        """
    ).fetchone()[0]

    macro_null_keys = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {MACRO_TABLE}
        WHERE base_date IS NULL
           OR release_date IS NULL
           OR symbol IS NULL
        """
    ).fetchone()[0]

    index_duplicates = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT
                base_date,
                symbol,
                exchange,
                COUNT(*) AS row_count
            FROM {INDEX_TABLE}
            GROUP BY
                base_date,
                symbol,
                exchange
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]

    macro_duplicates = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT
                base_date,
                release_date,
                symbol,
                exchange,
                COUNT(*) AS row_count
            FROM {MACRO_TABLE}
            GROUP BY
                base_date,
                release_date,
                symbol,
                exchange
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]

    if index_null_keys > 0:
        raise ValueError(
            "The index table contains "
            f"{index_null_keys:,} rows with a null "
            "base_date or symbol."
        )

    if macro_null_keys > 0:
        raise ValueError(
            "The macro table contains "
            f"{macro_null_keys:,} rows with a null "
            "base_date, release_date, or symbol."
        )

    if index_duplicates > 0:
        LOGGER.warning(
            "Duplicate index keys detected | "
            "duplicate_groups=%d",
            index_duplicates,
        )

    if macro_duplicates > 0:
        LOGGER.warning(
            "Duplicate macro keys detected | "
            "duplicate_groups=%d",
            macro_duplicates,
        )

    LOGGER.info(
        "Database validation completed | "
        "index_null_keys=%d | "
        "macro_null_keys=%d | "
        "index_duplicate_groups=%d | "
        "macro_duplicate_groups=%d",
        index_null_keys,
        macro_null_keys,
        index_duplicates,
        macro_duplicates,
    )


# ============================================================
# Main job
# ============================================================

def build_duckdb() -> None:
    """
    Builds the metadata, market, and macro DuckDB tables.
    """

    LOGGER.info(
        "DuckDB build job started | database=%s",
        DATABASE_PATH,
    )

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = None

    try:
        index_files = get_parquet_files(
            INDEX_DATA_PATH
        )

        macro_files = get_parquet_files(
            MACRO_DATA_PATH
        )

        LOGGER.info(
            "Parquet file discovery completed | "
            "index_files=%d | macro_files=%d",
            len(index_files),
            len(macro_files),
        )

        validate_parquet_columns(
            parquet_files=index_files,
            required_columns=INDEX_COLUMNS,
            data_type_name="index",
        )

        validate_parquet_columns(
            parquet_files=macro_files,
            required_columns=MACRO_COLUMNS,
            data_type_name="macro",
        )

        connection = duckdb.connect(
            str(DATABASE_PATH)
        )

        LOGGER.info(
            "DuckDB connection opened | database=%s",
            DATABASE_PATH,
        )

        connection.execute(
            "BEGIN TRANSACTION"
        )

        create_schemas(
            connection
        )

        metadata_rows = create_metadata_table(
            connection
        )

        index_rows = create_index_table(
            connection,
            parquet_files=index_files,
        )

        macro_rows = create_macro_table(
            connection,
            parquet_files=macro_files,
        )

        validate_database(
            connection
        )

        connection.execute(
            "COMMIT"
        )

        LOGGER.info(
            "DuckDB build job completed successfully | "
            "metadata_rows=%d | "
            "index_rows=%d | "
            "macro_rows=%d",
            metadata_rows,
            index_rows,
            macro_rows,
        )

    except Exception:
        if connection is not None:
            try:
                connection.execute(
                    "ROLLBACK"
                )

                LOGGER.warning(
                    "DuckDB transaction rolled back."
                )

            except Exception:
                LOGGER.exception(
                    "DuckDB rollback failed."
                )

        LOGGER.exception(
            "DuckDB build job failed."
        )

        raise

    finally:
        if connection is not None:
            connection.close()

            LOGGER.info(
                "DuckDB connection closed."
            )


if __name__ == "__main__":
    build_duckdb()