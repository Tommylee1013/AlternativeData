"""Run all 6 example queries from schema-design.md with real data."""
import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # ── Query 1: BDI vs HMM shipping stock ─────────────────────────
    print('=' * 70)
    print('QUERY 1: BDI weekly change vs HMM (011200.KS) daily returns')
    print('=' * 70)
    r = con.execute('''
        WITH bdi AS (
            SELECT observation_date, value AS bdi, pct_1p AS bdi_wow
            FROM v_indicator_changes
            WHERE indicator_id = 'BDI'
        ),
        hmm AS (
            SELECT date, ret_1d, ret_5d
            FROM v_price_returns
            WHERE security_id = '011200.KS'
        )
        SELECT
            hmm.date,
            ROUND(hmm.ret_1d, 4) AS ret_1d,
            ROUND(hmm.ret_5d, 4) AS ret_5d,
            bdi.bdi,
            ROUND(bdi.bdi_wow, 4) AS bdi_wow
        FROM hmm
        ASOF LEFT JOIN bdi
             ON bdi.observation_date <= hmm.date
        WHERE hmm.date >= DATE '2024-01-01'
        ORDER BY hmm.date DESC
        LIMIT 10
    ''').fetchall()
    print(f'{"date":>12s}  {"ret_1d":>8s}  {"ret_5d":>8s}  {"bdi":>8s}  {"bdi_wow":>8s}')
    print('-' * 52)
    for row in r:
        print(f'{str(row[0]):>12s}  {str(row[1]):>8s}  {str(row[2]):>8s}  {str(row[3]):>8s}  {str(row[4]):>8s}')

    # Correlation
    corr = con.execute('''
        WITH bdi AS (
            SELECT observation_date, pct_1p AS bdi_wow
            FROM v_indicator_changes WHERE indicator_id = 'BDI'
        ),
        hmm AS (
            SELECT date, ret_5d FROM v_price_returns WHERE security_id = '011200.KS'
        )
        SELECT ROUND(CORR(bdi.bdi_wow, hmm.ret_5d), 4), COUNT(*)
        FROM hmm ASOF LEFT JOIN bdi ON bdi.observation_date <= hmm.date
        WHERE hmm.date >= DATE '2015-01-01' AND bdi.bdi_wow IS NOT NULL
    ''').fetchone()
    print(f'\nCorrelation (BDI wow vs HMM ret_5d, since 2015): r={corr[0]}, n={corr[1]:,}')

    # ── Query 2: DRAM spot vs SK Hynix ─────────────────────────────
    print('\n' + '=' * 70)
    print('QUERY 2: CFM DRAM Index vs SK Hynix (000660.KS), rebased')
    print('=' * 70)
    r = con.execute('''
        SELECT
            p.date,
            ROUND(p.adj_close, 0) AS hynix_close,
            ROUND(p.adj_close / FIRST_VALUE(p.adj_close) OVER (ORDER BY p.date), 3) AS hynix_rebase,
            d.value AS dram_idx,
            ROUND(d.value / FIRST_VALUE(d.value) OVER (ORDER BY p.date), 3) AS dram_rebase
        FROM fact_price p
        ASOF LEFT JOIN (
            SELECT observation_date, value
            FROM v_indicator_latest
            WHERE indicator_id = 'CFM_DRAM_INDEX'
        ) d ON d.observation_date <= p.date
        WHERE p.security_id = '000660.KS'
          AND d.value IS NOT NULL
        ORDER BY p.date DESC
        LIMIT 10
    ''').fetchall()
    print(f'{"date":>12s}  {"hynix":>10s}  {"hx_rebase":>10s}  {"dram_idx":>10s}  {"dr_rebase":>10s}')
    print('-' * 58)
    for row in r:
        print(f'{str(row[0]):>12s}  {row[1]:>10.0f}  {row[2]:>10.3f}  {row[3]:>10.2f}  {row[4]:>10.3f}')

    # ── Query 3: Point-in-time CPI vs SPY ──────────────────────────
    print('\n' + '=' * 70)
    print('QUERY 3: Point-in-time CPI YoY vs SPY 20d returns')
    print('=' * 70)
    r = con.execute('''
        WITH cpi_pit AS (
            SELECT effective_release_date AS known_on, value AS cpi_yoy
            FROM v_indicator_pit
            WHERE indicator_id = 'US_CPI_YOY'
        ),
        spy AS (
            SELECT date, ret_20d
            FROM v_price_returns
            WHERE security_id = 'SPY'
        )
        SELECT
            spy.date,
            ROUND(spy.ret_20d, 4) AS ret_20d,
            ROUND(cpi.cpi_yoy, 4) AS cpi_yoy_known
        FROM spy
        ASOF LEFT JOIN cpi_pit cpi
             ON cpi.known_on <= spy.date
        WHERE spy.date BETWEEN DATE '2022-01-01' AND DATE '2024-12-31'
        ORDER BY spy.date DESC
        LIMIT 10
    ''').fetchall()
    print(f'{"date":>12s}  {"spy_ret20d":>12s}  {"cpi_yoy_pit":>12s}')
    print('-' * 40)
    for row in r:
        print(f'{str(row[0]):>12s}  {str(row[1]):>12s}  {str(row[2]):>12s}')

    pit_count = con.execute('''
        SELECT COUNT(DISTINCT observation_date) FROM v_indicator_pit
        WHERE indicator_id = 'US_CPI_YOY' AND effective_release_date != observation_date
    ''').fetchone()[0]
    print(f'\nCPI observations with release lag: {pit_count}')

    # ── Query 4: OFR stress regime → KOSPI returns ──────────────────
    print('\n' + '=' * 70)
    print('QUERY 4: OFR stress regimes vs ^GSPC (S&P 500) forward returns')
    print('=' * 70)
    # Using ^GSPC since we don't have KOSPI directly
    r = con.execute('''
        WITH stress AS (
            SELECT
                observation_date,
                value,
                AVG(value)    OVER w AS mu,
                STDDEV(value) OVER w AS sigma
            FROM v_indicator_latest
            WHERE indicator_id = 'OFR_FSI'
            WINDOW w AS (ORDER BY observation_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
        ),
        stress_flag AS (
            SELECT
                observation_date,
                CASE
                    WHEN (value - mu) / NULLIF(sigma, 0) > 2 THEN 'stress_high'
                    WHEN (value - mu) / NULLIF(sigma, 0) < -1 THEN 'stress_low'
                    ELSE 'normal'
                END AS regime
            FROM stress
        ),
        sp AS (
            SELECT date, ret_20d
            FROM v_price_returns
            WHERE security_id = '^GSPC'
        )
        SELECT
            s.regime,
            COUNT(*)             AS n_days,
            ROUND(AVG(k.ret_20d), 5)       AS mean_fwd_20d,
            ROUND(STDDEV(k.ret_20d), 5)    AS vol_fwd_20d,
            ROUND(AVG(k.ret_20d) / NULLIF(STDDEV(k.ret_20d), 0), 3) AS sharpe_ish
        FROM sp k
        ASOF LEFT JOIN stress_flag s
             ON s.observation_date <= k.date
        WHERE s.regime IS NOT NULL
        GROUP BY s.regime
        ORDER BY mean_fwd_20d DESC
    ''').fetchall()
    print(f'{"regime":>12s}  {"n_days":>8s}  {"mean_20d":>10s}  {"vol_20d":>10s}  {"sharpe":>8s}')
    print('-' * 54)
    for row in r:
        print(f'{row[0]:>12s}  {row[1]:>8,}  {row[2]:>10.5f}  {row[3]:>10.5f}  {row[4]:>8.3f}')

    # ── Query 5: CNN Fear & Greed → next-week SPY returns ──────────
    print('\n' + '=' * 70)
    print('QUERY 5: CNN Fear & Greed buckets vs SPY forward 5d returns')
    print('=' * 70)
    r = con.execute('''
        WITH fg AS (
            SELECT observation_date, value AS fear_greed
            FROM v_indicator_latest
            WHERE indicator_id = 'CNN_FEAR_GREED'
        ),
        spy_fwd AS (
            SELECT
                date,
                LEAD(adj_close, 5) OVER (ORDER BY date) / adj_close - 1 AS fwd_5d
            FROM fact_price
            WHERE security_id = 'SPY'
        )
        SELECT
            CASE
                WHEN fg.fear_greed < 20 THEN 1
                WHEN fg.fear_greed < 40 THEN 2
                WHEN fg.fear_greed < 60 THEN 3
                WHEN fg.fear_greed < 80 THEN 4
                ELSE 5
            END AS fg_bucket,
            COUNT(*)          AS n,
            ROUND(AVG(s.fwd_5d), 5)     AS mean_fwd_5d,
            ROUND(STDDEV(s.fwd_5d), 5)  AS vol_fwd_5d
        FROM spy_fwd s
        ASOF JOIN fg ON fg.observation_date <= s.date
        WHERE s.fwd_5d IS NOT NULL
        GROUP BY fg_bucket
        ORDER BY fg_bucket
    ''').fetchall()
    print(f'{"bucket":>8s}  {"F&G range":>12s}  {"n":>6s}  {"mean_5d":>10s}  {"vol_5d":>10s}')
    print('-' * 52)
    labels = {1: '0-20', 2: '20-40', 3: '40-60', 4: '60-80', 5: '80-100'}
    for row in r:
        label = labels.get(row[0], f'{row[0]}')
        print(f'{row[0]:>8d}  {label:>12s}  {row[1]:>6,}  {row[2]:>10.5f}  {row[3]:>10.5f}')

    # ── Query 6: Multi-indicator panel for Samsung ──────────────────
    print('\n' + '=' * 70)
    print('QUERY 6: Multi-indicator panel - Samsung (005930.KS)')
    print('=' * 70)
    r = con.execute('''
        WITH base AS (
            SELECT date, adj_close, ret_5d
            FROM v_price_returns
            WHERE security_id = '005930.KS'
        )
        SELECT
            b.date,
            ROUND(b.adj_close, 0) AS samsung,
            ROUND(b.ret_5d, 4) AS ret_5d,
            dram.value  AS dram_idx,
            pmi.value   AS us_pmi,
            cn_pmi.value AS cn_pmi,
            ROUND(ofr.value, 3) AS ofr_fsi
        FROM base b
        ASOF LEFT JOIN (
            SELECT observation_date, value FROM v_indicator_latest
            WHERE indicator_id = 'CFM_DRAM_INDEX'
        ) dram ON dram.observation_date <= b.date
        ASOF LEFT JOIN (
            SELECT observation_date, value FROM v_indicator_latest
            WHERE indicator_id = 'US_PMI'
        ) pmi ON pmi.observation_date <= b.date
        ASOF LEFT JOIN (
            SELECT observation_date, value FROM v_indicator_latest
            WHERE indicator_id = 'CN_PMI'
        ) cn_pmi ON cn_pmi.observation_date <= b.date
        ASOF LEFT JOIN (
            SELECT observation_date, value FROM v_indicator_latest
            WHERE indicator_id = 'OFR_FSI'
        ) ofr ON ofr.observation_date <= b.date
        WHERE b.date >= DATE '2025-10-01'
        ORDER BY b.date DESC
        LIMIT 10
    ''').fetchall()
    print(f'{"date":>12s}  {"samsung":>8s}  {"ret_5d":>7s}  {"dram_idx":>9s}  {"us_pmi":>7s}  {"cn_pmi":>7s}  {"ofr":>7s}')
    print('-' * 66)
    for row in r:
        dram = f'{row[3]:.0f}' if row[3] else 'N/A'
        pmi = f'{row[4]:.1f}' if row[4] else 'N/A'
        cn = f'{row[5]:.1f}' if row[5] else 'N/A'
        ofr = f'{row[6]:.3f}' if row[6] else 'N/A'
        print(f'{str(row[0]):>12s}  {row[1]:>8.0f}  {str(row[2]):>7s}  {dram:>9s}  {pmi:>7s}  {cn:>7s}  {ofr:>7s}')

    # ── Final summary ──────────────────────────────────────────────
    print('\n' + '=' * 70)
    print('DATABASE SUMMARY')
    print('=' * 70)
    total_ind = con.execute('SELECT COUNT(*) FROM dim_indicator').fetchone()[0]
    total_sec = con.execute('SELECT COUNT(*) FROM dim_security').fetchone()[0]
    total_facts = con.execute('SELECT COUNT(*) FROM fact_indicator_value').fetchone()[0]
    total_prices = con.execute('SELECT COUNT(*) FROM fact_price').fetchone()[0]
    print(f'Indicators:  {total_ind}')
    print(f'Securities:  {total_sec}')
    print(f'Fact rows:   {total_facts:,} (indicators) + {total_prices:,} (prices) = {total_facts + total_prices:,}')

    secs = con.execute('''
        SELECT s.security_id, s.name, COUNT(*) AS n,
               MIN(p.date) AS from_dt, MAX(p.date) AS to_dt
        FROM fact_price p JOIN dim_security s USING (security_id)
        GROUP BY s.security_id, s.name ORDER BY s.security_id
    ''').fetchall()
    print(f'\n{"security":>15s}  {"name":>30s}  {"rows":>6s}  {"from":>12s}  {"to":>12s}')
    for s in secs:
        print(f'{s[0]:>15s}  {s[1]:>30s}  {s[2]:>6,}  {str(s[3]):>12s}  {str(s[4]):>12s}')

    con.close()
    print('\nAll 6 queries passed with real data.')


if __name__ == '__main__':
    main()
