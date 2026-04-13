"""Connect to alternative_data.duckdb and run a smoke-test query.

Demonstrates: insert → select → ASOF JOIN pattern.
Uses tiny fake data so you can see the schema work end-to-end
before you have real loaders.
"""
import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent / "alternative_data.duckdb"


def main() -> None:
    con = duckdb.connect(str(DB_PATH))

    # ---- Seed a tiny BDI indicator + one Korean shipping stock ----
    con.execute("""
        INSERT OR REPLACE INTO dim_indicator
            (indicator_id, name, category, country, frequency, unit, source,
             release_lag_days, collection_method)
        VALUES
            ('BDI', 'Baltic Dry Index', 'freight', 'GLOBAL', 'daily',
             'index', 'Baltic Exchange', 0, 'manual_excel');
    """)

    con.execute("""
        INSERT OR REPLACE INTO dim_security
            (security_id, ticker, name, asset_class, exchange, currency, country)
        VALUES
            ('011200.KS', '011200', 'HMM', 'stock', 'KRX', 'KRW', 'KR');
    """)

    # BDI values (weekly-ish, just for illustration)
    con.execute("""
        INSERT OR REPLACE INTO fact_indicator_value
            (indicator_id, observation_date, release_date, value, revision)
        VALUES
            ('BDI', '2024-01-02', '2024-01-02', 2094, 0),
            ('BDI', '2024-01-09', '2024-01-09', 1496, 0),
            ('BDI', '2024-01-16', '2024-01-16', 1368, 0),
            ('BDI', '2024-01-23', '2024-01-23', 1466, 0);
    """)

    # HMM prices (daily)
    con.execute("""
        INSERT OR REPLACE INTO fact_price
            (security_id, date, open, high, low, close, adj_close, volume, source)
        VALUES
            ('011200.KS', '2024-01-02', 19500, 19900, 19300, 19800, 19800, 1200000, 'seed'),
            ('011200.KS', '2024-01-03', 19800, 20100, 19600, 19950, 19950, 1100000, 'seed'),
            ('011200.KS', '2024-01-04', 19950, 20200, 19800, 20050, 20050, 1050000, 'seed'),
            ('011200.KS', '2024-01-05', 20050, 20300, 19900, 20150, 20150, 1400000, 'seed'),
            ('011200.KS', '2024-01-08', 20150, 20400, 20000, 20250, 20250, 1300000, 'seed'),
            ('011200.KS', '2024-01-09', 20250, 20500, 20100, 20300, 20300, 1250000, 'seed'),
            ('011200.KS', '2024-01-10', 20300, 20600, 20200, 20400, 20400, 1500000, 'seed'),
            ('011200.KS', '2024-01-11', 20400, 20550, 20150, 20200, 20200, 1350000, 'seed'),
            ('011200.KS', '2024-01-12', 20200, 20300, 19900, 20000, 20000, 1200000, 'seed'),
            ('011200.KS', '2024-01-15', 20000, 20100, 19700, 19800, 19800, 1450000, 'seed'),
            ('011200.KS', '2024-01-16', 19800, 19900, 19500, 19600, 19600, 1600000, 'seed'),
            ('011200.KS', '2024-01-17', 19600, 19700, 19300, 19400, 19400, 1550000, 'seed'),
            ('011200.KS', '2024-01-18', 19400, 19600, 19200, 19500, 19500, 1400000, 'seed');
    """)

    # ---- Cross-layer query: HMM daily returns with as-of-joined BDI ----
    print("=" * 70)
    print("HMM (011200.KS) daily returns with latest BDI level (ASOF JOIN)")
    print("=" * 70)
    rows = con.execute("""
        WITH bdi AS (
            SELECT observation_date, value AS bdi
            FROM v_indicator_latest
            WHERE indicator_id = 'BDI'
        ),
        hmm AS (
            SELECT date, close, ret_1d
            FROM v_price_returns
            WHERE security_id = '011200.KS'
        )
        SELECT
            hmm.date,
            hmm.close,
            ROUND(hmm.ret_1d * 100, 2) AS ret_1d_pct,
            bdi.bdi AS bdi_asof
        FROM hmm
        ASOF LEFT JOIN bdi
             ON bdi.observation_date <= hmm.date
        ORDER BY hmm.date;
    """).fetchall()

    print(f"{'date':<12} {'close':>8} {'ret_1d_pct':>12} {'bdi_asof':>10}")
    print("-" * 46)
    for date, close, ret, bdi in rows:
        ret_str = f"{ret:>10}%" if ret is not None else f"{'n/a':>11}"
        print(f"{str(date):<12} {close:>8.0f} {ret_str:>12} {bdi:>10.0f}")

    # ---- Counts ----
    print("\n=== Row counts ===")
    for table in ("dim_indicator", "dim_security", "fact_indicator_value", "fact_price"):
        (n,) = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        print(f"  {table}: {n}")

    con.close()


if __name__ == "__main__":
    main()
