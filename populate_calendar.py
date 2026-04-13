"""Populate dim_calendar with trading sessions from exchange_calendars.

Stores only open (trading) days per exchange. Missing row = closed.
Re-runnable: INSERT OR REPLACE upserts idempotently.

Usage:
    python populate_calendar.py
"""
import duckdb
import exchange_calendars as xcals
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent / "alternative_data.duckdb"

# ISO MICs to load. Add more as needed (KRX, NYSE, NASDAQ, LSE, TSE, HKEX).
EXCHANGES = ["XKRX", "XNYS", "XNAS", "XLON", "XTKS", "XHKG"]

START = "2000-01-01"
END   = "2030-12-31"


def main() -> None:
    print(f"Loading trading calendars for: {', '.join(EXCHANGES)}")
    print(f"Range: {START} to {END}")

    rows: list[tuple[str, object]] = []
    for mic in EXCHANGES:
        cal = xcals.get_calendar(mic)
        # exchange_calendars clips to the calendar's own valid range automatically
        start = max(pd.Timestamp(START), cal.first_session)
        end   = min(pd.Timestamp(END),   cal.last_session)
        sessions = cal.sessions_in_range(start, end)
        rows.extend((mic, d.date()) for d in sessions)
        print(f"  {mic}: {len(sessions):,} sessions")

    df = pd.DataFrame(rows, columns=["mic", "session_date"])

    con = duckdb.connect(str(DB_PATH))
    con.register("df", df)
    con.execute("INSERT OR REPLACE INTO dim_calendar SELECT mic, session_date FROM df")

    # Sanity check
    (total,) = con.execute("SELECT COUNT(*) FROM dim_calendar").fetchone()
    print(f"\nTotal rows in dim_calendar: {total:,}")

    print("\nPer-exchange summary:")
    summary = con.execute("""
        SELECT
            mic,
            COUNT(*)            AS sessions,
            MIN(session_date)   AS first_session,
            MAX(session_date)   AS last_session
        FROM dim_calendar
        GROUP BY mic
        ORDER BY mic
    """).fetchall()
    for mic, n, first, last in summary:
        print(f"  {mic}: {n:,} sessions  ({first}  →  {last})")

    con.close()
    print("\nCalendar populated.")


if __name__ == "__main__":
    main()
