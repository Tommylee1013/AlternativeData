"""One-shot bootstrap: install deps, create the DuckDB schema, run smoke test.

Usage:
    python setup_all.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
REQUIRED = ["duckdb", "pandas", "openpyxl", "exchange_calendars"]


def run(cmd: list[str], desc: str) -> None:
    print(f"\n>>> {desc}")
    print(f"    $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print(f"\n!!! FAILED: {desc}")
        sys.exit(result.returncode)


def main() -> None:
    print("=" * 60)
    print("  Alternative Data DB - Setup")
    print("=" * 60)

    # 1. Python check
    print(f"\nPython: {sys.version.split()[0]}")
    if sys.version_info < (3, 9):
        print("!!! Python 3.9+ required.")
        sys.exit(1)

    # 2. Install dependencies
    run(
        [sys.executable, "-m", "pip", "install", "--quiet", *REQUIRED],
        f"Installing dependencies: {', '.join(REQUIRED)}",
    )

    # 3. Create schema
    run([sys.executable, "setup_db.py"], "Creating database schema")

    # 4. Populate trading calendars
    run([sys.executable, "populate_calendar.py"], "Populating trading calendars")

    # 5. Run smoke test
    run([sys.executable, "example_query.py"], "Running smoke-test query")

    # 5. Done
    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print("""
Next steps:
  1. Read schema-design.md for the full schema + example queries.
  2. Populate dim_indicator first (one row per indicator).
  3. Write a loader for your first Excel file (start with BDI).
  4. Hit it with query #1 from schema-design.md as the smoke test.

Database file: alternative_data.duckdb
Connect from Python:
    import duckdb
    con = duckdb.connect("alternative_data.duckdb")
    df = con.execute("SELECT * FROM v_indicator_latest").df()
""")


if __name__ == "__main__":
    main()
