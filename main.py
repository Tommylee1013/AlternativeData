from pathlib import Path

import yaml

from src.jobs_xlsx.get_macro_data import save_macro_data
from src.jobs_api.get_korea_index_data import collect_korean_index_data
from src.jobs_api.get_japan_index_data import collect_japan_index_data
from src.jobs_api.get_china_index_data import collect_china_index_data

# Project root 지정
PROJECT_ROOT = Path.cwd()

CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "macro_jobs.yaml"
)

def load_macro_jobs(
    config_path: str | Path = CONFIG_PATH,
) -> list[dict]:
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"YAML 설정 파일을 찾을 수 없습니다: {config_path}"
        )

    with config_path.open(
        mode="r",
        encoding="utf-8",
    ) as file:
        config = yaml.safe_load(file)

    if not config:
        raise ValueError(
            f"YAML 설정 파일이 비어 있습니다: {config_path}"
        )

    jobs = config.get("macro_jobs")

    if not isinstance(jobs, list):
        raise ValueError(
            "YAML의 'macro_jobs'는 리스트여야 합니다."
        )

    required_keys = {
        "input_path",
        "output_path",
        "symbol",
        "exchange",
        "country",
    }

    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            raise TypeError(
                f"{index}번째 macro job은 "
                "dictionary 형식이어야 합니다."
            )

        missing_keys = required_keys - set(job)

        if missing_keys:
            raise ValueError(
                f"{index}번째 macro job에 "
                f"필수 항목이 없습니다: {sorted(missing_keys)}"
            )

    return jobs


def collect_macro_data() -> None:
    macro_jobs = load_macro_jobs()

    for job in macro_jobs:
        input_path = PROJECT_ROOT / job["input_path"]
        output_path = PROJECT_ROOT / job["output_path"]

        save_macro_data(
            input_path=input_path,
            output_path=output_path,
            symbol=job["symbol"],
            exchange=job["exchange"],
            country=job["country"],
            sheet_name=job.get("sheet_name", 0),
        )

def market_main() -> None :
    collect_korean_index_data()
    collect_japan_index_data()
    collect_china_index_data()

if __name__ == "__main__":
    # market_main()
    collect_macro_data()