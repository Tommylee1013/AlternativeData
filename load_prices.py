"""Load price data from yfinance into dim_security + fact_price."""
import argparse
from pathlib import Path
from datetime import date

import duckdb
import yfinance as yf

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'

# Security definitions: security_id → metadata
SECURITIES = {
    # Korean stocks
    '005930.KS': {
        'ticker': '005930', 'name': 'Samsung Electronics',
        'asset_class': 'stock', 'exchange': 'KRX', 'mic': 'XKRX',
        'currency': 'KRW', 'country': 'KR', 'sector': 'Technology',
        'industry': 'Semiconductors',
    },
    '000660.KS': {
        'ticker': '000660', 'name': 'SK Hynix',
        'asset_class': 'stock', 'exchange': 'KRX', 'mic': 'XKRX',
        'currency': 'KRW', 'country': 'KR', 'sector': 'Technology',
        'industry': 'Semiconductors',
    },
    '011200.KS': {
        'ticker': '011200', 'name': 'HMM Co Ltd',
        'asset_class': 'stock', 'exchange': 'KRX', 'mic': 'XKRX',
        'currency': 'KRW', 'country': 'KR', 'sector': 'Industrials',
        'industry': 'Marine Shipping',
    },
    '035420.KS': {
        'ticker': '035420', 'name': 'Naver Corp',
        'asset_class': 'stock', 'exchange': 'KRX', 'mic': 'XKRX',
        'currency': 'KRW', 'country': 'KR', 'sector': 'Technology',
        'industry': 'Internet Services',
    },
    '006400.KS': {
        'ticker': '006400', 'name': 'Samsung SDI',
        'asset_class': 'stock', 'exchange': 'KRX', 'mic': 'XKRX',
        'currency': 'KRW', 'country': 'KR', 'sector': 'Technology',
        'industry': 'Batteries',
    },
    # US indices & ETFs
    'SPY': {
        'ticker': 'SPY', 'name': 'SPDR S&P 500 ETF',
        'asset_class': 'etf', 'exchange': 'NYSE', 'mic': 'XNYS',
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    'QQQ': {
        'ticker': 'QQQ', 'name': 'Invesco QQQ Trust (Nasdaq 100)',
        'asset_class': 'etf', 'exchange': 'NASDAQ', 'mic': 'XNAS',
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    '^GSPC': {
        'ticker': '^GSPC', 'name': 'S&P 500 Index',
        'asset_class': 'index', 'exchange': None, 'mic': None,
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    '^IXIC': {
        'ticker': '^IXIC', 'name': 'NASDAQ Composite Index',
        'asset_class': 'index', 'exchange': None, 'mic': None,
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    'TLT': {
        'ticker': 'TLT', 'name': 'iShares 20+ Year Treasury Bond ETF',
        'asset_class': 'etf', 'exchange': 'NASDAQ', 'mic': 'XNAS',
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    'GLD': {
        'ticker': 'GLD', 'name': 'SPDR Gold Shares ETF',
        'asset_class': 'etf', 'exchange': 'NYSE', 'mic': 'XNYS',
        'currency': 'USD', 'country': 'US', 'sector': None,
        'industry': None,
    },
    # Crypto
    'BTC-USD': {
        'ticker': 'BTC-USD', 'name': 'Bitcoin',
        'asset_class': 'crypto', 'exchange': 'CRYPTO', 'mic': None,
        'currency': 'USD', 'country': 'GLOBAL', 'sector': None,
        'industry': None,
    },
    'ETH-USD': {
        'ticker': 'ETH-USD', 'name': 'Ethereum',
        'asset_class': 'crypto', 'exchange': 'CRYPTO', 'mic': None,
        'currency': 'USD', 'country': 'GLOBAL', 'sector': None,
        'industry': None,
    },
    'SOL-USD': {
        'ticker': 'SOL-USD', 'name': 'Solana',
        'asset_class': 'crypto', 'exchange': 'CRYPTO', 'mic': None,
        'currency': 'USD', 'country': 'GLOBAL', 'sector': None,
        'industry': None,
    },
}


def upsert_security(con: duckdb.DuckDBPyConnection, sec_id: str, meta: dict) -> None:
    con.execute('''
        INSERT INTO dim_security (
            security_id, ticker, name, asset_class, exchange, mic,
            currency, country, sector, industry
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (security_id) DO UPDATE SET
            name = EXCLUDED.name
    ''', [
        sec_id, meta['ticker'], meta['name'], meta['asset_class'],
        meta['exchange'], meta.get('mic'), meta['currency'],
        meta['country'], meta.get('sector'), meta.get('industry'),
    ])


def load_prices(
    con: duckdb.DuckDBPyConnection,
    sec_id: str,
    start: str,
    end: str,
) -> int:
    """Fetch from yfinance and insert into fact_price. Returns row count."""
    df = yf.download(sec_id, start=start, end=end, auto_adjust=False, progress=False)

    if df.empty:
        return 0

    # yfinance returns MultiIndex columns when single ticker: flatten
    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
        df.columns = df.columns.droplevel('Ticker')

    rows = []
    for idx, row in df.iterrows():
        trade_date = idx.date() if hasattr(idx, 'date') else idx
        o = float(row['Open']) if row['Open'] == row['Open'] else None
        h = float(row['High']) if row['High'] == row['High'] else None
        lo = float(row['Low']) if row['Low'] == row['Low'] else None
        c = float(row['Close']) if row['Close'] == row['Close'] else None
        ac = float(row['Adj Close']) if 'Adj Close' in row.index and row['Adj Close'] == row['Adj Close'] else c
        v = int(row['Volume']) if row['Volume'] == row['Volume'] and row['Volume'] else None

        if c is None:
            continue

        rows.append((sec_id, trade_date, o, h, lo, c, ac, v, 'yahoo'))

    if not rows:
        return 0

    con.executemany('''
        INSERT INTO fact_price
            (security_id, date, open, high, low, close, adj_close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (security_id, date) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume = EXCLUDED.volume,
            source = EXCLUDED.source
    ''', rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description='Load price data from yfinance')
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--start', default='2006-01-01')
    parser.add_argument('--end', default=str(date.today()))
    parser.add_argument('--ticker', '-t', default=None,
                        help='Load only this ticker (for testing)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    securities = SECURITIES
    if args.ticker:
        if args.ticker not in securities:
            print(f'Unknown ticker: {args.ticker}')
            print(f'Available: {", ".join(securities.keys())}')
            return
        securities = {args.ticker: securities[args.ticker]}

    con = duckdb.connect(args.db)

    # First, clear seed data for HMM (from earlier smoke test)
    con.execute("DELETE FROM fact_price WHERE source = 'seed'")

    total_rows = 0
    for sec_id, meta in securities.items():
        print(f'  {sec_id:15s} ({meta["name"]:30s}) ...', end=' ', flush=True)

        if args.dry_run:
            print('(dry-run)')
            continue

        upsert_security(con, sec_id, meta)
        n = load_prices(con, sec_id, args.start, args.end)
        print(f'{n:,} rows')
        total_rows += n

    con.close()
    print(f'\nDone: {len(securities)} securities, {total_rows:,} rows total.')


if __name__ == '__main__':
    main()
