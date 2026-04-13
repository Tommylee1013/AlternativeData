"""Generic indicator loader: reads YAML catalog → loads Excel data → inserts into DuckDB."""
import sys
import argparse
from pathlib import Path
from datetime import datetime, date

import duckdb
import yaml
import openpyxl

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'
CATALOG_DIR = Path(__file__).parent / 'catalogs'

# Standard header columns in all Excel files (0-based index)
COL_BASE_DATE = 0
COL_RELEASE_DATE = 1
# cols 2,3 = Time, Time Zone (ignored)
VALUE_COL_OFFSET = 4  # value columns start at index 4


def load_catalog(catalog_file: Path) -> list[dict]:
    with open(catalog_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data['indicators']


def extract_rows(indicator: dict, project_root: Path) -> list[tuple]:
    """Extract (observation_date, release_date, value) from one Excel column."""
    filepath = project_root / indicator['file']
    sheet = indicator['sheet']
    col_idx = VALUE_COL_OFFSET + indicator['column_index']

    wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
    ws = wb[sheet]

    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:  # skip header
            continue

        obs_date_raw = row[COL_BASE_DATE]
        release_date_raw = row[COL_RELEASE_DATE]
        value_raw = row[col_idx] if col_idx < len(row) else None

        # Parse dates — fall back to Release Date as observation_date when Base Date is missing
        obs_date = _parse_date(obs_date_raw)
        release_date = _parse_date(release_date_raw)
        if obs_date is None:
            if release_date is not None:
                obs_date = release_date
                release_date = None  # avoid storing same date in both fields
            else:
                continue  # skip rows without any valid date

        # Parse value
        if value_raw is None:
            continue
        try:
            value = float(value_raw)
        except (ValueError, TypeError):
            continue  # skip non-numeric

        rows.append((obs_date, release_date, value))

    wb.close()
    return rows


def _parse_date(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y'):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
    return None


def upsert_dim_indicator(con: duckdb.DuckDBPyConnection, ind: dict, source_file: str) -> None:
    """Insert or update dim_indicator row."""
    con.execute('''
        INSERT INTO dim_indicator (
            indicator_id, name, category, subcategory, country,
            frequency, unit, source, collection_method, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (indicator_id) DO UPDATE SET
            name = EXCLUDED.name
    ''', [
        ind['indicator_id'],
        ind['name'],
        ind['category'],
        ind.get('subcategory'),
        ind.get('country'),
        ind['frequency'],
        ind.get('unit'),
        ind['source'],
        ind['collection_method'],
        ind.get('description'),
    ])


def insert_fact_rows(
    con: duckdb.DuckDBPyConnection,
    indicator_id: str,
    rows: list[tuple],
    source_file: str,
) -> int:
    """Bulk insert into fact_indicator_value. Returns rows inserted."""
    if not rows:
        return 0

    # Build parameter list
    params = [
        (indicator_id, obs, rel, val, 0, source_file)
        for obs, rel, val in rows
    ]

    con.executemany('''
        INSERT INTO fact_indicator_value
            (indicator_id, observation_date, release_date, value, revision, source_file)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (indicator_id, observation_date, revision) DO UPDATE SET
            value = EXCLUDED.value,
            release_date = EXCLUDED.release_date,
            source_file = EXCLUDED.source_file
    ''', params)

    return len(params)


def main() -> None:
    parser = argparse.ArgumentParser(description='Load indicators from YAML catalog into DuckDB')
    parser.add_argument('catalog', nargs='?', default=None,
                        help='Catalog YAML file (default: all files in catalogs/)')
    parser.add_argument('--db', default=str(DB_PATH), help='DuckDB file path')
    parser.add_argument('--indicator', '-i', default=None,
                        help='Load only this indicator_id (for testing)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse Excel but do not write to DB')
    args = parser.parse_args()

    project_root = Path(__file__).parent

    # Collect catalog files
    if args.catalog:
        catalog_files = [Path(args.catalog)]
    else:
        catalog_files = sorted(CATALOG_DIR.glob('*.yaml'))

    if not catalog_files:
        print('No catalog files found.')
        sys.exit(1)

    indicators = []
    for cf in catalog_files:
        indicators.extend(load_catalog(cf))
        print(f'Loaded catalog: {cf.name} ({len(load_catalog(cf))} indicators)')

    if args.indicator:
        indicators = [i for i in indicators if i['indicator_id'] == args.indicator]
        if not indicators:
            print(f'Indicator {args.indicator} not found in catalog.')
            sys.exit(1)

    con = duckdb.connect(args.db) if not args.dry_run else None

    total_rows = 0
    total_indicators = 0

    for ind in indicators:
        iid = ind['indicator_id']
        print(f'  {iid:20s} ...', end=' ', flush=True)

        rows = extract_rows(ind, project_root)

        if not rows:
            print('0 rows (skipped)')
            continue

        if args.dry_run:
            print(f'{len(rows)} rows (dry-run)')
            total_rows += len(rows)
            total_indicators += 1
            continue

        source_file = ind['file']
        upsert_dim_indicator(con, ind, source_file)
        n = insert_fact_rows(con, iid, rows, source_file)
        print(f'{n} rows')

        total_rows += n
        total_indicators += 1

    if con:
        con.close()

    print(f'\nDone: {total_indicators} indicators, {total_rows} rows total.')


if __name__ == '__main__':
    main()
