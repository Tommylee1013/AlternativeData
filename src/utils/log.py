import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(
    name: str,
    log_path: str | Path,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """
    콘솔과 파일에 동시에 로그를 출력하는 공통 Logger를 생성합니다.

    Parameters
    ----------
    name:
        Logger 이름

    log_path:
        로그 파일 저장 경로

    level:
        로그 레벨

    max_bytes:
        단일 로그 파일 최대 크기

    backup_count:
        보관할 백업 로그 파일 개수
    """

    log_path = Path(log_path)

    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # 동일한 Logger를 여러 번 호출해도 Handler 중복 방지
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger