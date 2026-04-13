"""Initialize alternative_data.duckdb with schema from schema-design.md."""
import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent / "alternative_data.duckdb"

DDL = r"""
-- ============================================================
-- DIMENSIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_indicator (
    indicator_id        VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    category            VARCHAR NOT NULL,
    subcategory         VARCHAR,
    country             VARCHAR,
    frequency           VARCHAR NOT NULL,
    unit                VARCHAR,
    source              VARCHAR NOT NULL,
    source_url          VARCHAR,
    release_lag_days    INTEGER,
    collection_method   VARCHAR NOT NULL,
    description         VARCHAR,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_security (
    security_id         VARCHAR PRIMARY KEY,
    ticker              VARCHAR NOT NULL,
    name                VARCHAR,
    asset_class         VARCHAR NOT NULL,
    exchange            VARCHAR,
    mic                 VARCHAR,               -- ISO MIC: 'XKRX' | 'XNYS' | 'XNAS' | 'XLON' | 'XTKS' | 'XHKG' (NULL for crypto)
    currency            VARCHAR NOT NULL,
    country             VARCHAR,
    sector              VARCHAR,
    industry            VARCHAR,
    index_membership    VARCHAR[],
    listed_date         DATE,
    delisted_date       DATE,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- FACTS
-- ============================================================

CREATE TABLE IF NOT EXISTS fact_indicator_value (
    indicator_id        VARCHAR NOT NULL REFERENCES dim_indicator(indicator_id),
    observation_date    DATE    NOT NULL,
    release_date        DATE,
    value               DOUBLE,
    value_text          VARCHAR,
    revision            INTEGER NOT NULL DEFAULT 0,
    source_file         VARCHAR,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (indicator_id, observation_date, revision)
);

CREATE TABLE IF NOT EXISTS fact_price (
    security_id         VARCHAR NOT NULL REFERENCES dim_security(security_id),
    date                DATE    NOT NULL,
    open                DOUBLE,
    high                DOUBLE,
    low                 DOUBLE,
    close               DOUBLE  NOT NULL,
    adj_close           DOUBLE,
    volume              BIGINT,
    source              VARCHAR,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (security_id, date)
);

CREATE TABLE IF NOT EXISTS fact_fx (
    pair                VARCHAR NOT NULL,
    date                DATE    NOT NULL,
    rate                DOUBLE  NOT NULL,
    source              VARCHAR,
    PRIMARY KEY (pair, date)
);

-- ============================================================
-- CALENDAR
-- Only open (trading) days are stored. Missing row = closed.
-- Populate with populate_calendar.py (uses exchange_calendars).
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_calendar (
    mic             VARCHAR NOT NULL,          -- ISO MIC: 'XKRX', 'XNYS', 'XNAS', 'XLON', 'XTKS', 'XHKG'
    session_date    DATE    NOT NULL,
    PRIMARY KEY (mic, session_date)
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_calendar_date    ON dim_calendar(session_date);
CREATE INDEX IF NOT EXISTS idx_sec_mic          ON dim_security(mic);

CREATE INDEX IF NOT EXISTS idx_ind_val_obs      ON fact_indicator_value(observation_date);
CREATE INDEX IF NOT EXISTS idx_ind_val_release  ON fact_indicator_value(release_date);
CREATE INDEX IF NOT EXISTS idx_price_date       ON fact_price(date);
CREATE INDEX IF NOT EXISTS idx_sec_asset_class  ON dim_security(asset_class);
CREATE INDEX IF NOT EXISTS idx_ind_category     ON dim_indicator(category);

-- ============================================================
-- VIEWS
-- ============================================================

CREATE OR REPLACE VIEW v_indicator_latest AS
SELECT indicator_id, observation_date, release_date, value, value_text, revision
FROM fact_indicator_value
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY indicator_id, observation_date
    ORDER BY revision DESC
) = 1;

CREATE OR REPLACE VIEW v_indicator_pit AS
SELECT
    iv.indicator_id,
    iv.observation_date,
    COALESCE(
        iv.release_date,
        (iv.observation_date + (COALESCE(di.release_lag_days, 0) * INTERVAL 1 DAY))::DATE
    ) AS effective_release_date,
    iv.value,
    iv.revision
FROM fact_indicator_value iv
JOIN dim_indicator di USING (indicator_id);

CREATE OR REPLACE VIEW v_price_returns AS
SELECT
    security_id,
    date,
    close,
    adj_close,
    adj_close / LAG(adj_close) OVER (PARTITION BY security_id ORDER BY date) - 1
        AS ret_1d,
    LN(adj_close / LAG(adj_close) OVER (PARTITION BY security_id ORDER BY date))
        AS log_ret_1d,
    adj_close / LAG(adj_close, 5) OVER (PARTITION BY security_id ORDER BY date) - 1
        AS ret_5d,
    adj_close / LAG(adj_close, 20) OVER (PARTITION BY security_id ORDER BY date) - 1
        AS ret_20d
FROM fact_price
WHERE adj_close IS NOT NULL;

CREATE OR REPLACE VIEW v_indicator_changes AS
SELECT
    indicator_id,
    observation_date,
    value,
    value - LAG(value) OVER w            AS diff_1p,
    value / LAG(value) OVER w - 1        AS pct_1p,
    value / LAG(value, 4) OVER w - 1     AS pct_4p,
    value / LAG(value, 12) OVER w - 1    AS pct_12p
FROM v_indicator_latest
WINDOW w AS (PARTITION BY indicator_id ORDER BY observation_date);
"""


def main() -> None:
    print(f"DuckDB version: {duckdb.__version__}")
    print(f"Creating database at: {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)

    print("\n=== SHOW TABLES ===")
    tables = con.execute("SHOW TABLES").fetchall()
    for (name,) in tables:
        print(f"  {name}")

    print("\n=== Views (from duckdb_views) ===")
    views = con.execute(
        "SELECT view_name FROM duckdb_views() WHERE internal = false ORDER BY view_name"
    ).fetchall()
    for (name,) in views:
        print(f"  {name}")

    print("\n=== Column counts per table ===")
    for (t,) in tables:
        cols = con.execute(f"PRAGMA table_info('{t}')").fetchall()
        print(f"  {t}: {len(cols)} columns")

    con.close()
    print(f"\nDatabase ready: {DB_PATH}")


if __name__ == "__main__":
    main()
