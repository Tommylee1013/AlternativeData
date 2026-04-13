"""Incremental price updater: fetch new rows from yfinance for all securities in dim_security."""
import argparse
from datetime import date, timedelta
from pathlib import Path

import duckdb
import yfinance as yf

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'


def update_prices(db_path: str, dry_run: bool = False) -> dict[str, dict]:
    """Returns {security_id: {'new_rows': int, 'last_date': date, 'error': str|None}}"""
    con = duckdb.connect(db_path, read_only=dry_run)

    # Get all securities and their latest loaded date
    securities = con.execute('''
        SELECT s.security_id, s.name,
               MAX(p.date) AS last_date
        FROM dim_security s
        LEFT JOIN fact_price p USING (security_id)
        GROUP BY s.security_id, s.name
        ORDER BY s.security_id
    ''').fetchall()

    results = {}
    for sec_id, name, last_date in securities:
        result = {'name': name, 'new_rows': 0, 'last_date': last_date, 'error': None}

        # Start from the day after last loaded date, or 2006-01-01 if empty
        if last_date:
            start = (last_date + timedelta(days=1)).isoformat()
        else:
            start = '2006-01-01'

        end = date.today().isoformat()

        if start >= end:
            results[sec_id] = result
            continue

        try:
            df = yf.download(sec_id, start=start, end=end, auto_adjust=False, progress=False)
        except Exception as e:
            result['error'] = str(e)
            results[sec_id] = result
            continue

        if df.empty:
            results[sec_id] = result
            continue

        if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
            df.columns = df.columns.droplevel('Ticker')

        rows = []
        for idx, row in df.iterrows():
            trade_date = idx.date() if hasattr(idx, 'date') else idx
            c = float(row['Close']) if row['Close'] == row['Close'] else None
            if c is None:
                continue
            o = float(row['Open']) if row['Open'] == row['Open'] else None
            h = float(row['High']) if row['High'] == row['High'] else None
            lo = float(row['Low']) if row['Low'] == row['Low'] else None
            ac = float(row['Adj Close']) if 'Adj Close' in row.index and row['Adj Close'] == row['Adj Close'] else c
            v = int(row['Volume']) if row['Volume'] == row['Volume'] and row['Volume'] else None
            rows.append((sec_id, trade_date, o, h, lo, c, ac, v, 'yahoo'))

        result['new_rows'] = len(rows)

        if rows and not dry_run:
            con.executemany('''
                INSERT INTO fact_price
                    (security_id, date, open, high, low, close, adj_close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (security_id, date) DO NOTHING
            ''', rows)
            result['last_date'] = max(r[1] for r in rows)

        results[sec_id] = result

    con.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description='Incremental price update from yfinance')
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated')
    args = parser.parse_args()

    mode = '[DRY RUN] ' if args.dry_run else ''
    print(f'{mode}Updating prices...')

    results = update_prices(args.db, args.dry_run)

    total_new = 0
    for sec_id, r in results.items():
        status = f'{r["new_rows"]} new rows' if r['new_rows'] > 0 else 'up to date'
        if r['error']:
            status = f'ERROR: {r["error"]}'
        print(f'  {sec_id:15s}  {status}')
        total_new += r['new_rows']

    print(f'\n{mode}Total: {total_new} new price rows across {len(results)} securities.')


if __name__ == '__main__':
    main()
