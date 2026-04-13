"""Incremental indicator updater: re-scan catalogs + industry, upsert only new rows."""
import argparse
from pathlib import Path
from datetime import date

import duckdb

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'
CATALOG_DIR = Path(__file__).parent / 'catalogs'
PROJECT_ROOT = Path(__file__).parent


def _get_last_dates(con: duckdb.DuckDBPyConnection) -> dict[str, date]:
    """Get max observation_date per indicator_id."""
    rows = con.execute('''
        SELECT indicator_id, MAX(observation_date) AS last_date
        FROM fact_indicator_value
        GROUP BY indicator_id
    ''').fetchall()
    return {r[0]: r[1] for r in rows}


def update_catalog_indicators(db_path: str, dry_run: bool = False) -> dict[str, dict]:
    """Update indicators from YAML catalogs. Returns {category: {'new_rows': int, 'indicators_updated': int, 'errors': []}}"""
    import yaml
    from load_indicators import extract_rows, upsert_dim_indicator, insert_fact_rows, load_catalog

    con = duckdb.connect(db_path, read_only=dry_run)
    last_dates = _get_last_dates(con)

    catalog_files = sorted(CATALOG_DIR.glob('*.yaml'))
    results = {}

    for cf in catalog_files:
        category = cf.stem  # freight, macro, commodity, sentiment, market
        cat_result = {'new_rows': 0, 'indicators_updated': 0, 'errors': [], 'details': {}}

        try:
            indicators = load_catalog(cf)
        except Exception as e:
            cat_result['errors'].append(f'Failed to load {cf.name}: {e}')
            results[category] = cat_result
            continue

        for ind in indicators:
            iid = ind['indicator_id']
            try:
                all_rows = extract_rows(ind, PROJECT_ROOT)
            except Exception as e:
                cat_result['errors'].append(f'{iid}: {e}')
                continue

            if not all_rows:
                continue

            # Filter to only rows newer than last loaded date
            last = last_dates.get(iid)
            if last:
                new_rows = [(obs, rel, val) for obs, rel, val in all_rows if obs > last]
            else:
                new_rows = all_rows

            if not new_rows:
                cat_result['details'][iid] = 0
                continue

            cat_result['details'][iid] = len(new_rows)
            cat_result['new_rows'] += len(new_rows)
            cat_result['indicators_updated'] += 1

            if not dry_run:
                upsert_dim_indicator(con, ind, ind['file'])
                insert_fact_rows(con, iid, new_rows, ind['file'])

        results[category] = cat_result

    con.close()
    return results


def update_industry_indicators(db_path: str, dry_run: bool = False) -> dict[str, dict]:
    """Update CFM + TrendForce industry indicators. Returns same format."""
    from load_industry import load_cfm, load_trendforce

    con = duckdb.connect(db_path, read_only=dry_run) if not dry_run else None

    # For industry, we re-run the loaders which use ON CONFLICT DO UPDATE
    # This is safe for upsert - existing rows get updated, new rows get inserted
    results = {}

    # Temporarily suppress print output from load_industry
    import io, sys
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    try:
        if not dry_run:
            # Get row counts before
            before_count = duckdb.connect(db_path, read_only=True).execute(
                "SELECT COUNT(*) FROM fact_indicator_value WHERE indicator_id LIKE 'CFM_%'"
            ).fetchone()[0]

            ni_cfm, nr_cfm = load_cfm(con, dry_run=False)

            after_cfm = duckdb.connect(db_path, read_only=True).execute(
                "SELECT COUNT(*) FROM fact_indicator_value WHERE indicator_id LIKE 'CFM_%'"
            ).fetchone()[0]
            new_cfm = after_cfm - before_count

            before_tf = duckdb.connect(db_path, read_only=True).execute(
                "SELECT COUNT(*) FROM fact_indicator_value WHERE indicator_id LIKE 'TF_%'"
            ).fetchone()[0]

            ni_tf, nr_tf = load_trendforce(con, dry_run=False)

            after_tf = duckdb.connect(db_path, read_only=True).execute(
                "SELECT COUNT(*) FROM fact_indicator_value WHERE indicator_id LIKE 'TF_%'"
            ).fetchone()[0]
            new_tf = after_tf - before_tf
        else:
            # Dry run: just count what would be loaded
            ni_cfm, nr_cfm = load_cfm(None, dry_run=True)
            ni_tf, nr_tf = load_trendforce(None, dry_run=True)
            new_cfm = 0  # can't know exact new count in dry run
            new_tf = 0
    finally:
        sys.stdout = old_stdout

    results['industry_cfm'] = {
        'new_rows': new_cfm if not dry_run else nr_cfm,
        'indicators_updated': ni_cfm,
        'errors': [],
        'details': {},
    }
    results['industry_tf'] = {
        'new_rows': new_tf if not dry_run else nr_tf,
        'indicators_updated': ni_tf,
        'errors': [],
        'details': {},
    }

    if con:
        con.close()

    return results


def update_all_indicators(db_path: str, dry_run: bool = False) -> dict[str, dict]:
    """Update all indicators (catalog + industry). Returns merged results."""
    results = update_catalog_indicators(db_path, dry_run)
    industry_results = update_industry_indicators(db_path, dry_run)
    results.update(industry_results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description='Incremental indicator update')
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated')
    parser.add_argument('--catalog-only', action='store_true', help='Skip industry files')
    args = parser.parse_args()

    mode = '[DRY RUN] ' if args.dry_run else ''
    print(f'{mode}Updating indicators...\n')

    if args.catalog_only:
        results = update_catalog_indicators(args.db, args.dry_run)
    else:
        results = update_all_indicators(args.db, args.dry_run)

    grand_total = 0
    for category, r in results.items():
        new = r['new_rows']
        grand_total += new
        status = f'{new} new rows ({r["indicators_updated"]} indicators)' if new > 0 else 'up to date'
        print(f'  {category:20s}  {status}')

        # Show per-indicator details if there are updates
        for iid, count in r.get('details', {}).items():
            if count > 0:
                print(f'    {iid:50s}  +{count}')

        for err in r.get('errors', []):
            print(f'    ERROR: {err}')

    print(f'\n{mode}Total: {grand_total} new indicator rows.')


if __name__ == '__main__':
    main()
