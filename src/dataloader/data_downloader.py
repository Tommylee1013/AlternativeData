import numpy as np
import pandas as pd

import time
from tqdm import tqdm
from tvDatafeed import TvDatafeed, Interval

def get_data_from_tradingview(
    tickers: list[str],
    interval: Interval, # tvDatafeed.Interval 객체 형태
    exchange: list[str],
    n_bars: int, # 충분히 큰 수. Tradingview는 기간이 아닌 candle bar의 개수로 호출 가능
    column: str | None = None,
    verbose: bool = True, # 종목을 얼마만큼 호출 성공했는지 progress bar로 표현
    num_trials: int = 5, # 호출 실패 시 재시도 횟수
    multi_level_index: bool = True, # yfinance에서의 attribute와 동일한 기능
    tz_cleansing: bool = False,
    session_duration_map: dict[str, tuple[int, int]] | None = None,
    duplicate_index_method: str = "last",  # "last", "first", "mean"
) -> pd.DataFrame:
    """
    TradingView(tvDatafeed)로부터 가격데이터 import

    Params
    - column:
        * str  : 해당 컬럼만 반환 (columns=tickers)
        * None : OHLCV 전체 반환

    - multi_level_index:
        * True  : columns=MultiIndex (ticker, field)  -> ("AAPL","close") 형태
        * False : columns=MultiIndex (field, ticker)  -> data["close"] 로 모든 종목 접근 가능

    - tz_cleansing:
        * True  : 인덱스를 날짜 단위로 정규화
        * False : 원본 인덱스 유지

    - session_duration_map:
        * None : 인덱스 변경 없음
        * dict : 거래소별 open -> close 시간 차이
            예:
            {
                "NASDAQ": (6, 30),
                "NYSE": (6, 30),
                "KRX": (6, 30),
                "TSE": (6, 0),
            }

    - duplicate_index_method:
        * "last"  : 같은 timestamp가 여러 개 있으면 마지막 값 사용
        * "first" : 같은 timestamp가 여러 개 있으면 첫 번째 값 사용
        * "mean"  : 같은 timestamp가 여러 개 있으면 평균값 사용

    Notes
    - column이 str이면 multi_level_index 설정은 의미가 거의 없고, 그냥 (columns=tickers)로 반환.
    """

    def _shift_index_by_session_duration(
        idx: pd.Index,
        exch: str,
        session_duration_map: dict[str, tuple[int, int]] | None,
    ) -> pd.DatetimeIndex:
        idx = pd.to_datetime(idx)

        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)

        if session_duration_map is None:
            return pd.DatetimeIndex(idx)

        if exch not in session_duration_map:
            return pd.DatetimeIndex(idx)

        hours, minutes = session_duration_map[exch]
        delta = pd.Timedelta(hours=hours, minutes=minutes)

        return pd.DatetimeIndex(idx + delta)

    def _remove_duplicate_index(obj: pd.Series | pd.DataFrame):
        """
        index가 중복된 Series/DataFrame을 정리.
        TradingView continuous futures, timezone cleansing, session shift 이후
        동일 날짜/시간 index가 생길 수 있으므로 concat 전에 반드시 처리.
        """
        if not obj.index.has_duplicates:
            return obj

        if duplicate_index_method == "last":
            return obj.groupby(level=0).last()

        elif duplicate_index_method == "first":
            return obj.groupby(level=0).first()

        elif duplicate_index_method == "mean":
            return obj.groupby(level=0).mean(numeric_only=True)

        else:
            raise ValueError(
                "duplicate_index_method must be one of ['last', 'first', 'mean']"
            )

    tv = TvDatafeed()
    iterator = tqdm(list(zip(tickers, exchange)), disable=not verbose)

    ohlcv_cols = ["open", "high", "low", "close", "volume"]
    frames: list[pd.DataFrame] = []
    series_list: list[pd.Series] = []

    for ticker, exch in iterator:
        got = False

        for attempt in range(num_trials):
            try:
                temp = tv.get_hist(
                    symbol=ticker,
                    exchange=exch,
                    interval=interval,
                    n_bars=n_bars,
                )

                if temp is None or temp.empty:
                    raise ValueError(f"Empty data returned for {ticker} ({exch}).")

                temp.columns = [c.lower() for c in temp.columns]
                temp.index = pd.to_datetime(temp.index)

                # 거래소별 open -> close shift
                if session_duration_map is not None:
                    temp.index = _shift_index_by_session_duration(
                        idx=temp.index,
                        exch=exch,
                        session_duration_map=session_duration_map,
                    )

                elif tz_cleansing:
                    temp.index = pd.to_datetime(temp.index.strftime("%Y-%m-%d"))

                else:
                    if getattr(temp.index, "tz", None) is not None:
                        temp.index = temp.index.tz_localize(None)

                # timezone cleansing, session shift, continuous futures rollover 등으로
                # 동일 timestamp가 생기는 경우를 여기서 제거
                temp = _remove_duplicate_index(temp)

                if column is None:
                    use_cols = [c for c in ohlcv_cols if c in temp.columns]
                    temp2 = temp[use_cols].copy()

                    # OHLCV DataFrame 기준으로도 한 번 더 방어
                    temp2 = _remove_duplicate_index(temp2)

                    temp2.columns = pd.MultiIndex.from_product(
                        [[ticker], temp2.columns.tolist()],
                        names=["ticker", "field"],
                    )
                    frames.append(temp2)

                else:
                    col = column.lower()
                    if col not in temp.columns:
                        raise KeyError(
                            f"Column '{column}' not in data columns for {ticker}: {list(temp.columns)}"
                        )

                    series = temp[col].copy()
                    series.name = ticker

                    # pd.concat(series_list, axis=1)에서 터지는 것을 방지
                    series = _remove_duplicate_index(series)

                    series_list.append(series)

                got = True
                break

            except Exception as e:
                if attempt < num_trials - 1:
                    time.sleep(1)
                else:
                    print(f"[FAIL] {ticker} ({exch}) after {num_trials} trials: {e}")

        if not got:
            continue

    if column is not None:
        if not series_list:
            return pd.DataFrame()

        # concat 직전 최종 방어
        series_list = [_remove_duplicate_index(s) for s in series_list]

        return pd.concat(series_list, axis=1).sort_index()

    if not frames:
        return pd.DataFrame()

    # OHLCV 전체 반환 시 concat 직전 최종 방어
    frames = [_remove_duplicate_index(f) for f in frames]

    out = pd.concat(frames, axis=1).sort_index()

    if multi_level_index:
        return out.sort_index(axis=1)

    out.columns = out.columns.swaplevel(0, 1)
    return out.sort_index(axis=1)