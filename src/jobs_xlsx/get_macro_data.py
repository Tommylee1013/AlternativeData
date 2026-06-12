from pathlib import Path
from src.utils.log import setup_logger
import pandas as pd

logger = setup_logger(
    name=__name__,
    log_path="logs/jobs/macro.log",
)

def transform_macro_data(
    df: pd.DataFrame,
    symbol: str,
    exchange: str,
    country: str,
) -> pd.DataFrame:
    """
    Macro Excel 데이터를 공통 Parquet 적재 형식으로 변환합니다.

    Parameters
    ----------
    df:
        원본 Macro DataFrame

    symbol:
        내부 표준 심볼
        예: CPIYOY, CPIMOM, NFP, UNRATE

    exchange:
        데이터 제공 기관 또는 분류 코드
        예: BOL

    country:
        국가명
        예: United States
    """

    data = df.copy()

    data.columns = (
        data.columns
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
    )

    required_columns = {
        "base_date",
        "release_date",
        "time",
        "time_zone",
        "actual",
        "forecast",
        "previous",
    }

    missing_columns = required_columns - set(data.columns)

    if missing_columns:
        raise ValueError(
            "필수 컬럼이 없습니다: "
            f"{sorted(missing_columns)}"
        )

    # 날짜형 변환
    for column in [
        "base_date",
        "release_date",
    ]:
        data[column] = pd.to_datetime(
            data[column],
            errors="raise",
        ).dt.normalize()

    # 시간형 변환
    data["time"] = pd.to_datetime(
        data["time"].astype(str),
        errors="raise",
    ).dt.time

    # 시간대 문자열 정리
    data["time_zone"] = (
        data["time_zone"]
        .astype("string")
        .str.strip()
    )

    # 수치형 변환
    value_columns = [
        "actual",
        "forecast",
        "previous",
    ]

    for column in value_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    # 메타데이터 추가
    data["symbol"] = str(symbol).strip().upper()
    data["exchange"] = str(exchange).strip().upper()
    data["country"] = str(country).strip()

    output_columns = [
        "base_date",
        "release_date",
        "time",
        "time_zone",
        "symbol",
        "exchange",
        "country",
        "actual",
        "forecast",
        "previous",
    ]

    data = data[output_columns]

    # 값이 모두 비어 있는 행 제거
    data = data.dropna(
        how="all",
        subset=value_columns,
    )

    # 중복 검증
    duplicated_rows = data[
        data.duplicated(
            subset=[
                "base_date",
                "symbol",
            ],
            keep=False,
        )
    ]

    if not duplicated_rows.empty:
        raise ValueError(
            "동일한 base_date와 symbol을 가진 "
            "중복 데이터가 있습니다.\n"
            f"{duplicated_rows}"
        )

    data = (
        data.sort_values(
            by=[
                "base_date",
                "release_date",
            ]
        )
        .reset_index(drop=True)
    )

    return data


def save_macro_data(
    input_path: str | Path,
    output_path: str | Path,
    symbol: str,
    exchange: str,
    country: str,
    sheet_name: str | int = 0,
) -> pd.DataFrame:
    """
    Macro Excel 파일을 읽고 공통 형식으로 변환한 뒤
    Parquet 파일로 저장합니다.

    변환된 DataFrame도 반환합니다.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"cannot find input file : {input_path}"
        )

    raw_data = pd.read_excel(
        input_path,
        sheet_name=sheet_name,
    )

    cleansing_data = transform_macro_data(
        df=raw_data,
        symbol=symbol,
        exchange=exchange,
        country=country,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    cleansing_data.to_parquet(
        output_path,
        index=False,
    )

    logger.info(
        "Macro data has been downloaded | symbol=%s | rows=%s | output_path=%s",
        symbol,
        f"{len(cleansing_data):,}",
        output_path,
    )

    return cleansing_data