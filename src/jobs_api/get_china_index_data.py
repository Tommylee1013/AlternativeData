import pandas as pd
import numpy as np

from tqdm import tqdm
from tvDatafeed import Interval
from src.dataloader.meta_info import get_master_table
from src.dataloader.data_downloader import get_data_from_tradingview
from src.utils.log import setup_logger

from pathlib import Path # Path lib 필요

# Const Values

OUTPUT_PATH = Path(
    "data_lake/raw/index/china_index.parquet"
)
LOGGER = setup_logger(
    name = 'china_index_job',
    log_path = 'logs/jobs/china_index.log'
) # Logger 설정

N_BARS = 13000
VERBOSE = True
NUM_TRIALS = 5

# function declaration
def get_china_symbol_info() -> pd.DataFrame:
    """
    거래소 지수의 TradingView 조회 정보와
    내부 표준 SYMBOL 정보를 반환합니다.

    반환 컬럼
    ----------
    symbol:
        내부 저장용 표준 심볼

    symbol_raw:
        TradingView 조회용 원본 심볼

    exchange:
        TradingView 조회용 거래소
    """

    data = get_master_table().copy()

    sub = data[
        (data["ASSET_CLASS"] == "Equity")
        & (data["INSTRUMENT_TYPE"] == "INDEX")
        & (
            (data["EXCHANGE"] == "SSE") |
            (data["EXCHANGE"] == "SZSE") |
            (data["EXCHANGE"] == "HSI")
        )
    ][
        [
            "SYMBOL",
            "SYMBOL_RAW",
            "EXCHANGE",
        ]
    ].copy()

    sub = sub.rename(
        columns={
            "SYMBOL": "symbol",
            "SYMBOL_RAW": "symbol_raw",
            "EXCHANGE": "exchange",
        }
    )

    for column in [
        "symbol",
        "symbol_raw",
        "exchange",
    ]:
        sub[column] = (
            sub[column]
            .astype("string")
            .str.strip()
        )

    # 필수값 누락 제거 또는 오류 처리
    missing_rows = sub[
        sub[
            [
                "symbol",
                "symbol_raw",
                "exchange",
            ]
        ].isna().any(axis=1)
    ]

    if not missing_rows.empty:
        raise ValueError(
            "SYMBOL, SYMBOL_RAW 또는 EXCHANGE가 "
            "비어 있는 중국 지수 항목이 있습니다.\n"
            f"{missing_rows}"
        )

    # SYMBOL_RAW가 중복이면 매핑이 불가능하므로 검증
    duplicated_raw = sub[
        sub["symbol_raw"].duplicated(keep=False)
    ]

    if not duplicated_raw.empty:
        raise ValueError(
            "중복된 SYMBOL_RAW가 존재합니다.\n"
            f"{duplicated_raw}"
        )

    return sub.reset_index(drop=True)


def transform_index_data(
    df: pd.DataFrame,
    symbol_mapping: dict[str, str],
    exchange_mapping: dict[str, str],
) -> pd.DataFrame:
    """
    TradingView 지수 데이터를 DB 적재용 Long Form으로 변환합니다.

    TradingView에서 조회된 SYMBOL_RAW를
    metadata의 내부 표준 SYMBOL로 치환합니다.

    입력 구조
    ----------
    index:
        DatetimeIndex 또는 날짜로 변환 가능한 Index

    columns:
        MultiIndex
        Level 0 = TradingView 원본 심볼(SYMBOL_RAW)
        Level 1 = open, high, low, close, volume

    출력 컬럼
    ----------
    base_date, release_date, time, time_zone,
    symbol, open, high, low, close, volume

    symbol:
        metadata에 정의된 내부 표준 SYMBOL
    """

    if not isinstance(df.columns, pd.MultiIndex):
        raise TypeError(
            "df.columns는 MultiIndex여야 합니다. "
            "Level 0은 지수명, Level 1은 OHLCV여야 합니다."
        )

    required_fields = {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    df = df.copy()

    # 매핑 비교를 위해 key를 문자열로 정규화
    normalized_mapping = {
        str(raw_symbol).strip().upper(): str(symbol).strip()
        for raw_symbol, symbol in symbol_mapping.items()
    }

    normalized_symbol_mapping = {
        str(raw_symbol).strip().upper(): str(symbol).strip()
        for raw_symbol, symbol in symbol_mapping.items()
    }

    normalized_exchange_mapping = {
        str(raw_symbol).strip().upper(): str(exchange).strip().upper()
        for raw_symbol, exchange in exchange_mapping.items()
    }

    raw_symbols = (
        df.columns
        .get_level_values(0)
        .astype(str)
        .str.strip()
        .str.upper()
    )

    fields = (
        df.columns
        .get_level_values(1)
        .astype(str)
        .str.strip()
        .str.lower()
    )

    # metadata에 존재하지 않는 TradingView 심볼 검증
    unmapped_symbols = sorted(
        set(raw_symbols) - set(normalized_symbol_mapping)
    )

    if unmapped_symbols:
        raise ValueError(
            "metadata에서 매핑할 수 없는 SYMBOL_RAW가 있습니다: "
            f"{unmapped_symbols}"
        )

    # SYMBOL_RAW → 내부 표준 SYMBOL
    mapped_symbols = [
        normalized_symbol_mapping[raw_symbol]
        for raw_symbol in raw_symbols
    ]

    mapped_exchanges = [
        normalized_exchange_mapping[raw_symbol]
        for raw_symbol in raw_symbols
    ]

    df.columns = pd.MultiIndex.from_arrays(
        [
            mapped_symbols,
            mapped_exchanges,
            fields,
        ],
        names=[
            "symbol",
            "exchange",
            "field",
        ],
    )

    available_fields = set(
        df.columns.get_level_values("field")
    )

    missing_fields = required_fields - available_fields

    if missing_fields:
        raise ValueError(
            f"필수 OHLCV 컬럼이 없습니다: "
            f"{sorted(missing_fields)}"
        )

    # Index를 base_date로 사용
    df.index = pd.to_datetime(
        df.index,
        errors="raise",
    ).normalize()

    df.index.name = "base_date"

    # Level 0의 내부 표준 SYMBOL을 행으로 변환
    long_df = (
        df.stack(
            level=["symbol", "exchange"],
            future_stack=True,
        )
        .reset_index()
    )

    # 시간대 기록 (중요)
    long_df["release_date"] = long_df["base_date"]

    market_close_time = {
        "SSE": pd.Timestamp("15:00:00").time(),
        "SZSE": pd.Timestamp("15:00:00").time(),
        "HSI": pd.Timestamp("16:10:00").time(),
    }

    long_df["time"] = (
        long_df["exchange"]
        .astype("string")
        .str.strip()
        .str.upper()
        .map(market_close_time)
    )

    undefined_exchanges = (
        long_df.loc[
            long_df["time"].isna(),
            "exchange",
        ]
        .dropna()
        .drop_duplicates()
        .tolist()
    )

    if undefined_exchanges:
        raise ValueError(
            "장 마감시간이 정의되지 않은 거래소가 있습니다: "
            f"{undefined_exchanges}"
        )

    long_df["time_zone"] = "UTC+08:00"
    long_df["country"] = np.where(
        long_df["exchange"].eq("HSI"),
        "Hong Kong",
        "China",
    )
    output_columns = [
        "base_date",
        "release_date",
        "time",
        "time_zone",
        "symbol",
        "exchange",
        'country',
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    long_df = long_df[output_columns]

    # 모든 OHLCV 값이 결측인 행 제거
    long_df = long_df.dropna(
        how="all",
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    long_df = (
        long_df.sort_values(
            by=[
                "base_date",
                "symbol",
            ],
        )
        .reset_index(drop=True)
    )

    return long_df


def collect_china_index_data() -> None:
    LOGGER.info(
        'starting download China market index data.'
    )
    symbol_info = get_china_symbol_info()

    tickers = symbol_info["symbol_raw"].tolist()
    exchanges = symbol_info["exchange"].tolist()

    symbol_mapping = dict(
        zip(
            symbol_info["symbol_raw"],
            symbol_info["symbol"],
        )
    )

    exchange_mapping = dict(
        zip(
            symbol_info["symbol_raw"],
            symbol_info["exchange"],
        )
    )

    data = get_data_from_tradingview(
        tickers=tickers,
        interval=Interval.in_daily,
        exchange=exchanges,
        n_bars=N_BARS,
        verbose=VERBOSE,
        num_trials=NUM_TRIALS,
        multi_level_index=True,
        tz_cleansing=True,
    )

    cleansing_data = transform_index_data(
        df=data,
        symbol_mapping=symbol_mapping,
        exchange_mapping=exchange_mapping,
    )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    cleansing_data.to_parquet(
        OUTPUT_PATH,
        index=False,
    )

    LOGGER.info(
        'All of China market index data has been downloaded.'
    )


if __name__ == "__main__":
    collect_china_index_data()