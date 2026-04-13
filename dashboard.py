"""Alternative Data Dashboard — Streamlit GUI.

Launch:  streamlit run dashboard.py
"""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent / 'alternative_data.duckdb'

st.set_page_config(
    page_title='Alternative Data DB',
    page_icon='📊',
    layout='wide',
)

# ── DB Connection ──────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)


def get_rw_connection():
    return duckdb.connect(str(DB_PATH))


def query(sql: str, params=None) -> pd.DataFrame:
    con = get_connection()
    if params:
        return con.execute(sql, params).df()
    return con.execute(sql).df()


# ── Sidebar ────────────────────────────────────────────────────────

st.sidebar.title('📊 Alternative Data DB')
tab = st.sidebar.radio('메뉴', [
    '📋 대시보드',
    '🔍 데이터 탐색',
    '📈 크로스 분석',
    '🔄 데이터 업데이트',
    '➕ 새 데이터 추가',
])


# ====================================================================
# TAB 1: Dashboard Overview
# ====================================================================
if tab == '📋 대시보드':
    st.title('📋 대시보드 개요')

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    n_ind = query('SELECT COUNT(*) AS n FROM dim_indicator').iloc[0]['n']
    n_sec = query('SELECT COUNT(*) AS n FROM dim_security').iloc[0]['n']
    n_facts = query('SELECT COUNT(*) AS n FROM fact_indicator_value').iloc[0]['n']
    n_prices = query('SELECT COUNT(*) AS n FROM fact_price').iloc[0]['n']
    c1.metric('지표 수', f'{n_ind}')
    c2.metric('종목 수', f'{n_sec}')
    c3.metric('지표 행수', f'{n_facts:,}')
    c4.metric('가격 행수', f'{n_prices:,}')

    st.divider()

    # Category breakdown
    st.subheader('카테고리별 현황')
    cat_df = query('''
        SELECT di.category AS "카테고리",
               COUNT(DISTINCT di.indicator_id) AS "지표 수",
               COUNT(*) AS "총 행수",
               MIN(fv.observation_date) AS "시작일",
               MAX(fv.observation_date) AS "최종일"
        FROM fact_indicator_value fv
        JOIN dim_indicator di USING (indicator_id)
        GROUP BY di.category ORDER BY COUNT(*) DESC
    ''')
    st.dataframe(cat_df, use_container_width=True, hide_index=True)

    # Securities
    st.subheader('종목별 현황')
    sec_df = query('''
        SELECT s.security_id AS "종목코드", s.name AS "종목명",
               s.asset_class AS "유형", s.currency AS "통화",
               COUNT(*) AS "거래일수",
               MIN(p.date) AS "시작일", MAX(p.date) AS "최종일"
        FROM fact_price p JOIN dim_security s USING (security_id)
        GROUP BY ALL ORDER BY s.asset_class, s.security_id
    ''')
    st.dataframe(sec_df, use_container_width=True, hide_index=True)

    # Staleness warnings
    st.subheader('⚠️ 오래된 데이터 (7일 이상 미갱신)')
    stale_threshold = (date.today() - timedelta(days=7)).isoformat()

    stale_ind = query('''
        SELECT di.indicator_id AS "지표 ID", di.name AS "이름",
               di.category AS "카테고리", di.frequency AS "주기",
               MAX(fv.observation_date) AS "최종 관측일",
               CURRENT_DATE - MAX(fv.observation_date) AS "경과일"
        FROM dim_indicator di
        JOIN fact_indicator_value fv USING (indicator_id)
        WHERE di.category != 'industry'
        GROUP BY di.indicator_id, di.name, di.category, di.frequency
        HAVING MAX(fv.observation_date) < ?
        ORDER BY MAX(fv.observation_date) ASC LIMIT 20
    ''', [stale_threshold])

    if len(stale_ind) > 0:
        st.warning(f'{len(stale_ind)}개 지표가 7일 이상 업데이트되지 않았습니다.')
        st.dataframe(stale_ind, use_container_width=True, hide_index=True)
    else:
        st.success('모든 지표가 최신 상태입니다.')


# ====================================================================
# TAB 2: Data Explorer
# ====================================================================
elif tab == '🔍 데이터 탐색':
    st.title('🔍 데이터 탐색')

    mode = st.radio('데이터 유형', ['지표 (Indicator)', '종목 (Price)'], horizontal=True)

    if mode == '지표 (Indicator)':
        # Indicator selector
        ind_list = query('''
            SELECT indicator_id, name, category, frequency
            FROM dim_indicator WHERE category != 'industry'
            ORDER BY category, indicator_id
        ''')
        ind_options = {
            f'{r["category"]} | {r["indicator_id"]} — {r["name"]}': r['indicator_id']
            for _, r in ind_list.iterrows()
        }

        selected = st.multiselect(
            '지표 선택 (최대 5개 비교 가능)',
            options=list(ind_options.keys()),
            max_selections=5,
        )

        if selected:
            selected_ids = [ind_options[s] for s in selected]
            col1, col2 = st.columns(2)
            with col1:
                start = st.date_input('시작일', value=date(2020, 1, 1))
            with col2:
                end = st.date_input('종료일', value=date.today())

            placeholders = ','.join(['?' for _ in selected_ids])
            df = query(f'''
                SELECT indicator_id, observation_date AS date, value
                FROM v_indicator_latest
                WHERE indicator_id IN ({placeholders})
                  AND observation_date BETWEEN ? AND ?
                ORDER BY observation_date
            ''', selected_ids + [start.isoformat(), end.isoformat()])

            if len(df) > 0:
                # Chart
                st.subheader('시계열 차트')
                chart_df = df.pivot(index='date', columns='indicator_id', values='value')
                st.line_chart(chart_df, use_container_width=True)

                # Data table
                st.subheader('데이터 테이블')
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Stats
                st.subheader('기초 통계')
                for iid in selected_ids:
                    sub = df[df['indicator_id'] == iid]['value']
                    st.write(f'**{iid}**: 평균={sub.mean():.4f}, 표준편차={sub.std():.4f}, '
                             f'최소={sub.min():.4f}, 최대={sub.max():.4f}, 관측수={len(sub):,}')
            else:
                st.info('선택한 기간에 데이터가 없습니다.')

    else:
        # Price selector
        sec_list = query('SELECT security_id, name FROM dim_security ORDER BY security_id')
        sec_options = {f'{r["security_id"]} — {r["name"]}': r['security_id'] for _, r in sec_list.iterrows()}

        selected_secs = st.multiselect(
            '종목 선택 (최대 5개 비교 가능)',
            options=list(sec_options.keys()),
            max_selections=5,
        )

        if selected_secs:
            selected_ids = [sec_options[s] for s in selected_secs]
            col1, col2 = st.columns(2)
            with col1:
                start = st.date_input('시작일', value=date(2020, 1, 1), key='price_start')
            with col2:
                end = st.date_input('종료일', value=date.today(), key='price_end')

            rebase = st.checkbox('리베이스 비교 (시작=1.0)', value=len(selected_ids) > 1)

            placeholders = ','.join(['?' for _ in selected_ids])
            df = query(f'''
                SELECT security_id, date, adj_close AS value
                FROM fact_price
                WHERE security_id IN ({placeholders})
                  AND date BETWEEN ? AND ?
                ORDER BY date
            ''', selected_ids + [start.isoformat(), end.isoformat()])

            if len(df) > 0:
                chart_df = df.pivot(index='date', columns='security_id', values='value')
                if rebase:
                    chart_df = chart_df / chart_df.iloc[0]
                st.subheader('리베이스 차트' if rebase else '가격 차트')
                st.line_chart(chart_df, use_container_width=True)

                st.subheader('데이터 테이블')
                st.dataframe(df, use_container_width=True, hide_index=True)


# ====================================================================
# TAB 3: Cross-Layer Analysis
# ====================================================================
elif tab == '📈 크로스 분석':
    st.title('📈 크로스 레이어 분석 (ASOF JOIN)')

    st.markdown('''
    지표(Indicator)와 종목(Security)을 선택하면 **ASOF JOIN**으로 자동 정렬하여
    이중 축 차트, 상관계수, 산점도를 보여줍니다.
    ''')

    col1, col2 = st.columns(2)

    with col1:
        ind_list = query('''
            SELECT indicator_id, name, category FROM dim_indicator
            WHERE category != 'industry' ORDER BY category, indicator_id
        ''')
        ind_options = {f'{r["category"]} | {r["indicator_id"]} — {r["name"]}': r['indicator_id']
                       for _, r in ind_list.iterrows()}
        ind_choice = st.selectbox('지표 선택', options=list(ind_options.keys()))

    with col2:
        sec_list = query('SELECT security_id, name FROM dim_security ORDER BY security_id')
        sec_options = {f'{r["security_id"]} — {r["name"]}': r['security_id'] for _, r in sec_list.iterrows()}
        sec_choice = st.selectbox('종목 선택', options=list(sec_options.keys()))

    col3, col4 = st.columns(2)
    with col3:
        start = st.date_input('시작일', value=date(2015, 1, 1), key='cross_start')
    with col4:
        end = st.date_input('종료일', value=date.today(), key='cross_end')

    if ind_choice and sec_choice:
        iid = ind_options[ind_choice]
        sid = sec_options[sec_choice]

        df = query('''
            SELECT p.date, p.adj_close AS price,
                   ind.value AS indicator_value,
                   pr.ret_5d
            FROM fact_price p
            ASOF LEFT JOIN (
                SELECT observation_date, value FROM v_indicator_latest
                WHERE indicator_id = ?
            ) ind ON ind.observation_date <= p.date
            LEFT JOIN v_price_returns pr
                ON pr.security_id = p.security_id AND pr.date = p.date
            WHERE p.security_id = ?
              AND p.date BETWEEN ? AND ?
              AND ind.value IS NOT NULL
            ORDER BY p.date
        ''', [iid, sid, start.isoformat(), end.isoformat()])

        if len(df) > 0:
            # Dual-axis chart using Streamlit columns
            st.subheader('이중 축 시계열')

            import matplotlib.pyplot as plt
            fig, ax1 = plt.subplots(figsize=(14, 5))
            ax1.plot(df['date'], df['price'], color='#2196F3', linewidth=0.8, label=sid)
            ax1.set_ylabel(sid, color='#2196F3')
            ax1.tick_params(axis='y', labelcolor='#2196F3')

            ax2 = ax1.twinx()
            ax2.plot(df['date'], df['indicator_value'], color='#FF5722', linewidth=0.8, alpha=0.7, label=iid)
            ax2.set_ylabel(iid, color='#FF5722')
            ax2.tick_params(axis='y', labelcolor='#FF5722')

            lines = ax1.get_lines() + ax2.get_lines()
            ax1.legend(lines, [l.get_label() for l in lines], loc='upper left')
            ax1.set_title(f'{iid} vs {sid}')
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()

            # Correlation
            valid = df.dropna(subset=['indicator_value', 'ret_5d'])
            if len(valid) > 10:
                # Level correlation
                corr_level = df['price'].corr(df['indicator_value'])
                # Return correlation
                df['ind_pct'] = df['indicator_value'].pct_change()
                corr_ret = df['ind_pct'].corr(df['ret_5d'])

                c1, c2, c3 = st.columns(3)
                c1.metric('레벨 상관계수', f'{corr_level:.3f}')
                c2.metric('변화율 상관계수 (5d)', f'{corr_ret:.3f}')
                c3.metric('관측 수', f'{len(valid):,}')

                # Scatter
                st.subheader('산점도 (지표 변화율 vs 종목 5일 수익률)')
                scatter_df = df.dropna(subset=['ind_pct', 'ret_5d'])
                if len(scatter_df) > 0:
                    fig2, ax = plt.subplots(figsize=(8, 5))
                    ax.scatter(scatter_df['ind_pct'], scatter_df['ret_5d'],
                               alpha=0.15, s=8, color='#666')
                    ax.axhline(0, color='gray', linewidth=0.5)
                    ax.axvline(0, color='gray', linewidth=0.5)
                    ax.set_xlabel(f'{iid} 변화율')
                    ax.set_ylabel(f'{sid} 5일 수익률')
                    ax.set_title(f'r = {corr_ret:.3f}')
                    st.pyplot(fig2)
                    plt.close()
        else:
            st.warning('선택한 기간에 겹치는 데이터가 없습니다.')


# ====================================================================
# TAB 4: Update Data
# ====================================================================
elif tab == '🔄 데이터 업데이트':
    st.title('🔄 데이터 업데이트')

    st.markdown('''
    Excel 파일을 `data/` 폴더에 업데이트한 후 아래 버튼을 눌러 DB에 반영하세요.
    가격 데이터는 yfinance에서 자동으로 최신 데이터를 가져옵니다.
    ''')

    dry_run = st.checkbox('🧪 Dry Run (미리보기만, DB 변경 없음)', value=False)

    col1, col2, col3 = st.columns(3)

    with col1:
        run_prices = st.button('📈 가격 업데이트', use_container_width=True)
    with col2:
        run_indicators = st.button('📊 지표 업데이트', use_container_width=True)
    with col3:
        run_all = st.button('🔄 전체 업데이트', type='primary', use_container_width=True)

    def run_script(cmd_args: list[str]) -> str:
        result = subprocess.run(
            [sys.executable] + cmd_args,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=str(Path(__file__).parent),
            timeout=600,
        )
        output = result.stdout
        if result.stderr:
            # Filter out yfinance warnings
            stderr_lines = [
                l for l in result.stderr.splitlines()
                if not any(x in l for x in ['possibly delisted', 'Failed download', '$'])
            ]
            if stderr_lines:
                output += '\n--- stderr ---\n' + '\n'.join(stderr_lines)
        return output

    if run_prices or run_all:
        with st.spinner('가격 데이터 업데이트 중...' if not dry_run else '가격 데이터 미리보기 중...'):
            args = ['update_prices.py']
            if dry_run:
                args.append('--dry-run')
            try:
                output = run_script(args)
                st.code(output, language='text')
            except subprocess.TimeoutExpired:
                st.error('시간 초과 (10분). 네트워크 연결을 확인하세요.')
            except Exception as e:
                st.error(f'오류: {e}')

    if run_indicators or run_all:
        with st.spinner('지표 데이터 업데이트 중...' if not dry_run else '지표 데이터 미리보기 중...'):
            args = ['update_indicators.py']
            if dry_run:
                args.append('--dry-run')
            try:
                output = run_script(args)
                st.code(output, language='text')
            except subprocess.TimeoutExpired:
                st.error('시간 초과 (10분).')
            except Exception as e:
                st.error(f'오류: {e}')

    if run_all or run_prices or run_indicators:
        st.cache_resource.clear()
        st.info('업데이트 완료. 대시보드를 새로고침하면 최신 데이터가 반영됩니다.')


# ====================================================================
# TAB 5: Add New
# ====================================================================
elif tab == '➕ 새 데이터 추가':
    st.title('➕ 새 데이터 추가')

    add_type = st.radio('추가 유형', ['종목 추가', '지표 추가'], horizontal=True)

    if add_type == '종목 추가':
        st.subheader('새 종목 등록 + 가격 로드')
        st.markdown('yfinance에서 가격을 가져올 수 있는 종목 코드를 입력하세요.')

        with st.form('add_security'):
            col1, col2 = st.columns(2)
            with col1:
                sec_id = st.text_input('종목 코드 (yfinance ticker)', placeholder='예: AAPL, 005930.KS')
                name = st.text_input('종목명', placeholder='예: Apple Inc')
                asset_class = st.selectbox('자산 유형', ['stock', 'etf', 'index', 'crypto'])
            with col2:
                exchange = st.text_input('거래소', placeholder='예: NASDAQ, KRX')
                currency = st.selectbox('통화', ['USD', 'KRW', 'JPY', 'EUR', 'CNY'])
                country = st.text_input('국가', placeholder='예: US, KR')

            sector = st.text_input('섹터 (선택)', placeholder='예: Technology')
            start_date = st.date_input('가격 로드 시작일', value=date(2006, 1, 1))
            submitted = st.form_submit_button('종목 등록 + 가격 로드', type='primary')

        if submitted and sec_id and name:
            with st.spinner(f'{sec_id} 등록 중...'):
                try:
                    con = get_rw_connection()
                    con.execute('''
                        INSERT INTO dim_security (
                            security_id, ticker, name, asset_class, exchange,
                            currency, country, sector
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (security_id) DO UPDATE SET name = EXCLUDED.name
                    ''', [sec_id, sec_id.split('.')[0], name, asset_class,
                          exchange or None, currency, country or None, sector or None])
                    con.close()

                    # Load prices
                    output = run_script(['load_prices.py', '-t', sec_id, '--start', start_date.isoformat()])
                    st.code(output, language='text')
                    st.cache_resource.clear()
                    st.success(f'{sec_id} ({name}) 등록 완료!')
                except Exception as e:
                    st.error(f'오류: {e}')

    else:
        st.subheader('새 지표 등록')
        st.markdown('''
        `data/` 폴더에 Excel 파일을 추가한 후, 아래 양식으로 지표 메타데이터를 등록하세요.
        Excel 파일은 `Base Date | Release Date | Time | Time Zone | 값 컬럼...` 형식이어야 합니다.
        ''')

        # Show available Excel files
        data_dir = Path(__file__).parent / 'data'
        excel_files = sorted(data_dir.rglob('*.xlsx'))
        file_options = [str(f.relative_to(Path(__file__).parent)) for f in excel_files]

        with st.form('add_indicator'):
            col1, col2 = st.columns(2)
            with col1:
                indicator_id = st.text_input('지표 ID (UPPER_SNAKE)', placeholder='예: NEW_INDEX')
                ind_name = st.text_input('지표명', placeholder='예: New Weekly Index')
                category = st.selectbox('카테고리', ['freight', 'macro', 'commodity', 'sentiment', 'market', 'industry'])
                frequency = st.selectbox('주기', ['daily', 'weekly', 'monthly', 'quarterly', 'irregular'])
            with col2:
                file_path = st.selectbox('Excel 파일', options=file_options)
                # Get sheets for selected file
                source = st.text_input('출처', placeholder='예: Bloomberg')
                country = st.text_input('국가 코드', placeholder='예: US, KR, GLOBAL')
                unit = st.text_input('단위', placeholder='예: index, pct, usd')

            # Try to read sheet names
            if file_path:
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(str(Path(__file__).parent / file_path),
                                                read_only=True, data_only=True)
                    sheets = [s for s in wb.sheetnames if s.lower() not in ('fig', 'info')]
                    wb.close()
                    sheet = st.selectbox('시트', options=sheets)
                except Exception:
                    sheet = st.text_input('시트명')
            else:
                sheet = st.text_input('시트명')

            column_index = st.number_input(
                '값 컬럼 인덱스 (0부터, 헤더 4열 이후)',
                min_value=0, max_value=20, value=0,
                help='Base Date, Release Date, Time, Time Zone 이후 컬럼 기준. 0 = 첫 번째 값 컬럼'
            )

            submitted = st.form_submit_button('지표 등록 + 데이터 로드', type='primary')

        if submitted and indicator_id and ind_name and file_path and sheet:
            with st.spinner(f'{indicator_id} 로드 중...'):
                try:
                    from load_indicators import extract_rows, upsert_dim_indicator, insert_fact_rows

                    ind_config = {
                        'indicator_id': indicator_id,
                        'name': ind_name,
                        'file': file_path,
                        'sheet': sheet,
                        'column_index': column_index,
                        'category': category,
                        'frequency': frequency,
                        'unit': unit or None,
                        'source': source or 'manual_excel',
                        'collection_method': 'manual_excel',
                    }
                    if country:
                        ind_config['country'] = country

                    rows = extract_rows(ind_config, Path(__file__).parent)

                    if not rows:
                        st.warning('해당 시트/컬럼에서 데이터를 찾을 수 없습니다.')
                    else:
                        con = get_rw_connection()
                        upsert_dim_indicator(con, ind_config, file_path)
                        n = insert_fact_rows(con, indicator_id, rows, file_path)
                        con.close()
                        st.cache_resource.clear()
                        st.success(f'{indicator_id}: {n}행 로드 완료!')
                        st.dataframe(
                            pd.DataFrame(rows[:10], columns=['관측일', '발표일', '값']),
                            hide_index=True,
                        )
                except Exception as e:
                    st.error(f'오류: {e}')
