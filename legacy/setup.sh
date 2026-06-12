#!/bin/bash
echo "============================================================"
echo "  Alternative Data DB - Setup (macOS)"
echo "============================================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3이 설치되어 있지 않습니다."
    echo "        brew install python3  또는"
    echo "        https://www.python.org/downloads/ 에서 설치해주세요."
    exit 1
fi

echo "[1/3] Python 확인..."
python3 --version
echo ""

echo "[2/3] 패키지 설치 중..."
python3 -m pip install --upgrade pip -q
python3 -m pip install duckdb pandas openpyxl pyyaml matplotlib streamlit yfinance exchange_calendars -q

if [ $? -ne 0 ]; then
    echo "[ERROR] 패키지 설치에 실패했습니다. 인터넷 연결을 확인해주세요."
    exit 1
fi
echo "   설치 완료!"
echo ""

echo "[3/3] 데이터베이스 확인 중..."
if [ -f "alternative_data.duckdb" ]; then
    echo "   DB 파일 발견: alternative_data.duckdb"
    python3 -c "
import duckdb
c = duckdb.connect('alternative_data.duckdb', read_only=True)
print(f'    지표: {c.execute(\"SELECT COUNT(*) FROM dim_indicator\").fetchone()[0]}개')
print(f'    종목: {c.execute(\"SELECT COUNT(*) FROM dim_security\").fetchone()[0]}개')
n = c.execute('SELECT COUNT(*) FROM fact_indicator_value').fetchone()[0] + c.execute('SELECT COUNT(*) FROM fact_price').fetchone()[0]
print(f'    데이터: {n:,}행')
c.close()
"
else
    echo "   DB 파일이 없습니다. 스키마를 생성합니다..."
    python3 setup_db.py
    echo "   데이터를 로드합니다..."
    python3 load_indicators.py
    python3 load_industry.py
    python3 load_prices.py
fi
echo ""

echo "============================================================"
echo "  Setup 완료!"
echo ""
echo "  대시보드 실행:"
echo "    python3 -m streamlit run dashboard.py"
echo ""
echo "  주간 데이터 업데이트:"
echo "    python3 update_all.py"
echo ""
echo "  Jupyter 노트북:"
echo "    jupyter notebook research.ipynb"
echo "============================================================"
