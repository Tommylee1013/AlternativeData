"""Weekly update pipeline: refresh prices + indicators, print summary.

Usage:
    python update_all.py              # full update
    python update_all.py --dry-run    # preview without writing
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'


def print_header(title: str) -> None:
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print(f'{"=" * 60}')


def run_price_update(db_path: str, dry_run: bool) -> dict:
    from update_prices import update_prices
    return update_prices(db_path, dry_run)


def run_indicator_update(db_path: str, dry_run: bool) -> dict:
    from update_indicators import update_all_indicators
    return update_all_indicators(db_path, dry_run)


def get_staleness_report(db_path: str) -> list[tuple]:
    """Find indicators whose latest data is more than 7 days old."""
    con = duckdb.connect(db_path, read_only=True)
    today = date.today()
    stale_threshold = today - timedelta(days=7)

    stale = con.execute('''
        SELECT
            di.indicator_id,
            di.name,
            di.category,
            di.frequency,
            MAX(fv.observation_date) AS last_obs,
            ? - MAX(fv.observation_date) AS days_stale
        FROM dim_indicator di
        JOIN fact_indicator_value fv USING (indicator_id)
        GROUP BY di.indicator_id, di.name, di.category, di.frequency
        HAVING MAX(fv.observation_date) < ?
          AND di.category != 'industry'
        ORDER BY MAX(fv.observation_date) ASC
    ''', [today, stale_threshold]).fetchall()

    stale_prices = con.execute('''
        SELECT
            s.security_id,
            s.name,
            MAX(p.date) AS last_date,
            ? - MAX(p.date) AS days_stale
        FROM dim_security s
        JOIN fact_price p USING (security_id)
        GROUP BY s.security_id, s.name
        HAVING MAX(p.date) < ?
        ORDER BY MAX(p.date) ASC
    ''', [today, stale_threshold]).fetchall()

    con.close()
    return stale, stale_prices


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Weekly update: refresh prices + indicators from yfinance + Excel files'
    )
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be updated without writing to DB')
    parser.add_argument('--prices-only', action='store_true')
    parser.add_argument('--indicators-only', action='store_true')
    args = parser.parse_args()

    mode = '[DRY RUN] ' if args.dry_run else ''
    print(f'{mode}Alternative Data Weekly Update')
    print(f'Database: {args.db}')
    print(f'Date: {date.today()}')

    errors = []
    price_results = {}
    indicator_results = {}

    # ── Step 1: Update Prices ──────────────────────────────────────
    if not args.indicators_only:
        print_header('Step 1: Updating Prices (yfinance)')
        t0 = time.time()
        try:
            price_results = run_price_update(args.db, args.dry_run)
            elapsed = time.time() - t0

            total_new_prices = 0
            for sec_id, r in price_results.items():
                n = r['new_rows']
                total_new_prices += n
                if r['error']:
                    errors.append(f'Price {sec_id}: {r["error"]}')
                    print(f'  {sec_id:15s}  ERROR: {r["error"]}')
                elif n > 0:
                    print(f'  {sec_id:15s}  +{n} rows')
                else:
                    print(f'  {sec_id:15s}  up to date')

            print(f'\n  Prices: {total_new_prices} new rows ({elapsed:.1f}s)')

        except Exception as e:
            errors.append(f'Price update failed: {e}')
            print(f'  ERROR: {e}')

    # ── Step 2: Update Indicators ──────────────────────────────────
    if not args.prices_only:
        print_header('Step 2: Updating Indicators (Excel catalogs)')
        t0 = time.time()
        try:
            indicator_results = run_indicator_update(args.db, args.dry_run)
            elapsed = time.time() - t0

            total_new_indicators = 0
            for category, r in indicator_results.items():
                n = r['new_rows']
                total_new_indicators += n
                if n > 0:
                    print(f'  {category:20s}  +{n} rows ({r["indicators_updated"]} indicators)')
                else:
                    print(f'  {category:20s}  up to date')

                for err in r.get('errors', []):
                    errors.append(f'Indicator {category}: {err}')
                    print(f'    ERROR: {err}')

            print(f'\n  Indicators: {total_new_indicators} new rows ({elapsed:.1f}s)')

        except Exception as e:
            errors.append(f'Indicator update failed: {e}')
            print(f'  ERROR: {e}')

    # ── Step 3: Staleness Report ───────────────────────────────────
    if not args.dry_run:
        print_header('Step 3: Staleness Check (>7 days old)')
        stale_indicators, stale_prices = get_staleness_report(args.db)

        if stale_indicators:
            print(f'\n  Stale indicators ({len(stale_indicators)}):')
            for iid, name, cat, freq, last_obs, days in stale_indicators[:15]:
                print(f'    {iid:30s}  {cat:10s}  {freq:8s}  last: {last_obs}  ({days}d ago)')
            if len(stale_indicators) > 15:
                print(f'    ... and {len(stale_indicators) - 15} more')
        else:
            print('  All indicators are fresh (within 7 days).')

        if stale_prices:
            print(f'\n  Stale prices ({len(stale_prices)}):')
            for sec_id, name, last_date, days in stale_prices:
                print(f'    {sec_id:15s}  {name:30s}  last: {last_date}  ({days}d ago)')
        else:
            print('  All price data is fresh.')

    # ── Summary ────────────────────────────────────────────────────
    print_header(f'{mode}Summary')

    total_p = sum(r['new_rows'] for r in price_results.values())
    total_i = sum(r['new_rows'] for r in indicator_results.values())

    print(f'  New price rows:     {total_p:>6,}')
    print(f'  New indicator rows: {total_i:>6,}')
    print(f'  Errors:             {len(errors):>6}')

    if errors:
        print(f'\n  Error details:')
        for err in errors:
            print(f'    - {err}')

    # Final DB stats
    if not args.dry_run:
        con = duckdb.connect(args.db, read_only=True)
        n_ind = con.execute('SELECT COUNT(*) FROM dim_indicator').fetchone()[0]
        n_sec = con.execute('SELECT COUNT(*) FROM dim_security').fetchone()[0]
        n_facts = con.execute('SELECT COUNT(*) FROM fact_indicator_value').fetchone()[0]
        n_prices = con.execute('SELECT COUNT(*) FROM fact_price').fetchone()[0]
        con.close()
        print(f'\n  DB totals: {n_ind} indicators, {n_sec} securities')
        print(f'             {n_facts:,} indicator rows + {n_prices:,} price rows = {n_facts + n_prices:,} total')

    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
