import streamlit as st
import re
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

st.set_page_config(
    page_title="原初映像影音製作剪輯行程表",
    layout="wide",
    page_icon="🎬"
)

st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

SPREADSHEET_ID = '1_naCZzjQ3G7W28RyaRe-ZuTEsr-sjeTBBf912pAJB-M'
SHEET_NAME = '2024年12月'
SCOPES = ['https://www.googleapis.com/auth/drive']

EDITOR_MAP = {
    "jia": "游承佳", "Jia": "游承佳", "JIA": "游承佳", "承佳": "游承佳",
    "雅": "黃雅憶", "雅憶": "黃雅憶",
    "康": "蔡守康",
    "P": "游郁萍", "p": "游郁萍",
    "賢": "余品賢",
    "夏": "夏竹", "夏子": "夏竹",
    "耀陽": "王耀陽",
    "葉": "葉亭萱", "葉子": "葉亭萱",
    "瑞": "涂家瑞",
    "安": "安",
}

EXCLUDE_KEYWORDS = ["出國", "自己案子", "家裡有事", "下雨延", "喜華不行", "休假", "請假", "不在"]


def get_credentials():
    """回傳 (creds, error_str)"""
    import os
    # 優先用 Streamlit secrets
    try:
        rt = st.secrets["GOOGLE_REFRESH_TOKEN"]
        cid = st.secrets["GOOGLE_CLIENT_ID"]
        cs = st.secrets["GOOGLE_CLIENT_SECRET"]
        creds = Credentials(
            token=None,
            refresh_token=rt,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=cs,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return creds, None
    except KeyError as e:
        secrets_err = f"Secrets 缺少欄位：{e}"
    except Exception as e:
        secrets_err = f"Secrets 授權失敗：{e}"
    # 本機 fallback
    token_path = r'C:\Users\rendy\token.json'
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
            return creds, None
        except Exception as e:
            return None, f"{secrets_err}；本機 token 也失敗：{e}"
    return None, secrets_err


def get_cell_drive_url(cell):
    url = cell.get('hyperlink', '')
    if url and 'drive.google.com' in url:
        return url
    for run in cell.get('textFormatRuns', []):
        uri = run.get('format', {}).get('link', {}).get('uri', '')
        if uri and 'drive.google.com' in uri:
            return uri
    return None


def is_date_cell(text) -> bool:
    if not isinstance(text, str):
        return False
    v = text.strip()
    return bool(re.match(r"^\d{1,2}/\d{1,2}", v)) and len(v) <= 10


def normalize_date(text) -> str:
    m = re.match(r"^(\d{1,2})/(\d{1,2})", str(text).strip())
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        return f"{mo:02d}/{day:02d}"
    return str(text)


def extract_editor(text) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    m = re.search(r"[（(]([^）)\n]+)[）)]", text)
    if m:
        raw = m.group(1).strip()
        # 1) 完整字串比對（最優先）
        for key, val in EDITOR_MAP.items():
            if raw == key or raw.lower() == key.lower():
                return val
        # 2) 首個 token 比對（例如「賢 可晚點交」→ 取「賢」）
        first_token = re.split(r"[\s　,，、]+", raw, maxsplit=1)[0]
        for key, val in EDITOR_MAP.items():
            if first_token == key or first_token.lower() == key.lower():
                return val
    return None


def remove_editor_tag(text) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = re.sub(r"^\d{1,2}/\d{1,2}", "", text).strip()
    cleaned = re.sub(r"^0?\d{3,4}\s", "", cleaned).strip()
    cleaned = re.sub(r"\s*[（(][^）)\n]+[）)]\s*", "", cleaned).strip()
    cleaned = cleaned.split("\n")[0].strip()
    return cleaned if cleaned else text


def pick_recent_sheet(service):
    """挑選最新分頁：支援 YYYY年M月 與 YYYY年 兩種命名。"""
    try:
        meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    except Exception:
        return None, None
    candidates = []
    for s in meta.get('sheets', []):
        title = s['properties'].get('title', '')
        rc = s['properties'].get('gridProperties', {}).get('rowCount', 0)
        m1 = re.match(r'^(\d{4})年(\d{1,2})月$', title)
        if m1:
            y, mo = int(m1.group(1)), int(m1.group(2))
            candidates.append((y, mo, title, rc))
            continue
        m2 = re.match(r'^(\d{4})年$', title)
        if m2:
            y = int(m2.group(1))
            # 純年份排序權重 = 13，使其優先於同年的 YYYY年M月
            candidates.append((y, 13, title, rc))
            continue
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    for y, mo, title, rc in candidates:
        if rc > 100:
            return title, (y, mo)
    y, mo, title, _ = candidates[0]
    return title, (y, mo)


@st.cache_data(ttl=300)
def load_and_parse():
    try:
        creds, creds_err = get_credentials()
        if not creds:
            return None, creds_err or "無法取得 Google 憑證"
        service = build('sheets', 'v4', credentials=creds)
        sheet_name, base_ym = pick_recent_sheet(service)
        if not sheet_name:
            sheet_name, base_ym = SHEET_NAME, None
        result = service.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID,
            ranges=[sheet_name],
            includeGridData=True
        ).execute()
    except Exception as e:
        return None, f"載入失敗：{e}"

    sheet_rows = result['sheets'][0]['data'][0].get('rowData', [])

    # Build 2D grid: list of list of (text, drive_url)
    grid = []
    for row in sheet_rows:
        row_cells = []
        for cell in row.get('values', []):
            text = (cell.get('formattedValue', '') or '').strip()
            url = get_cell_drive_url(cell)
            row_cells.append((text, url))
        grid.append(row_cells)

    num_rows = len(grid)
    if num_rows == 0:
        return None, "工作表無資料"

    max_cols = max((len(r) for r in grid), default=0)
    for row in grid:
        while len(row) < max_cols:
            row.append(('', None))

    # Find date header rows (≥3 pure date cells)
    date_row_indices = []
    for i, row in enumerate(grid):
        if sum(1 for (t, _) in row if is_date_cell(t)) >= 3:
            date_row_indices.append(i)

    if not date_row_indices:
        return None, "找不到日期列"

    # 每列只取前 7 個日期格（週行事曆格式），避免右側殘留文字被誤判
    def week_date_cols(row):
        cols = [(ci, normalize_date(t)) for ci, (t, _) in enumerate(row) if is_date_cell(t)]
        return cols[:7]

    # 逐格掃描：從分頁名推定基準年份，遇到月份回轉就 +1
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    running_year = base_ym[0] if base_ym else now_tw.year

    # 預掃描一輪，計算總回轉次數（for 純年份分頁用）
    # 僅看每列前 7 個日期格，與主掃描一致
    total_rollovers = 0
    prev_mo_pre = None
    for dr in date_row_indices:
        for _, date_str in week_date_cols(grid[dr]):
            try:
                mo = int(date_str.split("/")[0])
            except Exception:
                continue
            if prev_mo_pre is not None and mo < prev_mo_pre:
                total_rollovers += 1
            prev_mo_pre = mo

    # 若分頁名為「YYYY年」(mo=13 標記)，假設 YYYY 是分頁最末段所屬年份，
    # 首段年份 = YYYY − 總回轉次數。
    if base_ym and base_ym[1] == 13:
        running_year = base_ym[0] - total_rollovers

    cell_dates = {}  # (dr, col_i) -> datetime
    prev_mo = None
    for dr in date_row_indices:
        for col_i, date_str in week_date_cols(grid[dr]):
            try:
                mo, da = map(int, date_str.split("/"))
            except Exception:
                continue
            if prev_mo is not None and mo < prev_mo:
                running_year += 1
            try:
                cell_dates[(dr, col_i)] = datetime(year=running_year, month=mo, day=da)
            except ValueError:
                pass
            prev_mo = mo

    records = []
    seen = set()
    for dr in date_row_indices:
        date_cols = [ci for ci, _ in week_date_cols(grid[dr])]
        for offset in range(1, 5):
            next_idx = dr + offset
            if next_idx >= num_rows:
                break
            next_row = grid[next_idx]
            if sum(1 for (t, _) in next_row if is_date_cell(t)) >= 3:
                break
            for col_i in date_cols:
                dt = cell_dates.get((dr, col_i))
                if dt is None:
                    continue
                if col_i >= len(next_row):
                    continue
                cell_text, cell_url = next_row[col_i]
                if not cell_text or cell_text in ("nan",):
                    continue
                if any(kw in cell_text for kw in EXCLUDE_KEYWORDS):
                    continue
                editor = extract_editor(cell_text)
                if not editor:
                    continue
                case_name = remove_editor_tag(cell_text)
                if not case_name:
                    continue
                key = (dt, case_name[:20], editor)
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "full_date": dt,
                    "拍攝日期": f"{dt.month}/{dt.day}",
                    "案子": case_name,
                    "剪輯": editor,
                    "連結": cell_url or "",
                })

    if not records:
        return None, f"工作表「{sheet_name}」找不到含剪輯分配的記錄"

    import pandas as pd
    df_all = pd.DataFrame(records)
    # 只保留近期：過去 60 天 ~ 未來 1 年
    today_d = now_tw.date()
    mask = (
        (df_all["full_date"].dt.date >= today_d - timedelta(days=60)) &
        (df_all["full_date"].dt.date <= today_d + timedelta(days=365))
    )
    result_df = df_all[mask].sort_values("full_date").reset_index(drop=True)
    if result_df.empty:
        return None, f"工作表「{sheet_name}」沒有近期（±60天內）案件記錄"
    return result_df, None


# ── Theme / CSS ──────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 溫暖紙質風 */
html, body, [class*="css"] {
    font-family: "Noto Serif TC", "PingFang TC", "Microsoft JhengHei", serif;
}
.stApp {
    background: #F5EEDC;
    background-image:
        radial-gradient(rgba(139, 111, 71, 0.04) 1px, transparent 1px),
        radial-gradient(rgba(139, 111, 71, 0.03) 1px, transparent 1px);
    background-size: 24px 24px, 48px 48px;
    background-position: 0 0, 12px 12px;
}
section.main > div.block-container {
    padding-top: 2.5rem;
    padding-bottom: 3rem;
    max-width: 1200px;
}
/* 主標 */
.app-title {
    font-size: 1.7rem;
    color: #3E2723;
    letter-spacing: 0.08em;
    margin: 0;
    padding-bottom: 0.2rem;
    border-bottom: 2px double #8B6F47;
    display: inline-block;
}
.app-sub {
    color: #795548;
    font-size: 0.82rem;
    margin-top: 0.4rem;
    letter-spacing: 0.08em;
}
/* 按鈕 */
.stButton > button {
    background: #FFFDF7;
    color: #5D4037;
    border: 1px solid #C7B299;
    border-radius: 6px;
    font-weight: 500;
    letter-spacing: 0.05em;
    transition: all 0.15s;
}
.stButton > button:hover {
    background: #EFE4D0;
    border-color: #8B6F47;
    color: #3E2723;
}
/* Streamlit 預設文字強制深色 */
.stApp, .stApp p, .stApp span, .stApp div,
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
[data-testid="stMarkdownContainer"] p,
.stMarkdown, .stMarkdown p {
    color: #3E2723;
}
[data-testid="stWidgetLabel"] label,
[data-testid="stWidgetLabel"] p {
    color: #5D4037 !important;
    font-weight: 500 !important;
    letter-spacing: 0.05em;
}
/* 多選框 */
[data-baseweb="select"] > div {
    background: #FFFDF7 !important;
    border-color: #D7CCBE !important;
    border-radius: 6px !important;
}
[data-baseweb="select"] > div > div {
    color: #3E2723 !important;
}
[data-baseweb="select"] input {
    color: #3E2723 !important;
}
[data-baseweb="select"] [aria-label="open"] svg,
[data-baseweb="select"] svg {
    fill: #8B6F47 !important;
}
[data-baseweb="tag"] {
    background: #E8DCC4 !important;
    color: #3E2723 !important;
}
[data-baseweb="tag"] span {
    color: #3E2723 !important;
}
[data-baseweb="popover"] li,
[data-baseweb="menu"] li {
    color: #3E2723 !important;
    background: #FFFDF7 !important;
}
[data-baseweb="popover"] li:hover,
[data-baseweb="menu"] li:hover {
    background: #EFE4D0 !important;
}
/* 佔位符 */
::placeholder {
    color: #A89080 !important;
    opacity: 1;
}
/* 分隔線 */
hr {
    border-top: 1px dashed #B8A68A !important;
    margin: 1.5rem 0 !important;
}
/* 今日 區塊 */
.today-label {
    display: inline-block;
    background: #8B4513;
    color: #FFFDF7 !important;
    padding: 6px 18px;
    border-radius: 4px;
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: 0.15em;
    margin-bottom: 0.8rem;
    box-shadow: 0 2px 4px rgba(93, 64, 55, 0.15);
}
.today-label * { color: #FFFDF7 !important; }
.today-empty {
    background: #FFFDF7;
    border: 1px dashed #C7B299;
    padding: 12px 18px;
    border-radius: 6px;
    color: #8B6F47 !important;
    font-size: 0.92rem;
    margin-top: 6px;
}
.today-item {
    background: #FFFDF7;
    border-left: 4px solid #A0522D;
    padding: 12px 16px;
    margin: 8px 0;
    border-radius: 0 6px 6px 0;
    font-size: 1rem;
    color: #3E2723 !important;
    box-shadow: 0 1px 3px rgba(93, 64, 55, 0.08);
}
.today-item * { color: #3E2723; }
.today-item strong {
    color: #8B4513 !important;
    margin-right: 10px;
    font-weight: 700;
    display: inline-block;
    min-width: 70px;
}
/* 本週 expander */
[data-testid="stExpander"] {
    background: #FFFDF7;
    border: 1px solid #D7CCBE !important;
    border-radius: 6px !important;
    box-shadow: 0 1px 3px rgba(93, 64, 55, 0.05);
    margin-bottom: 1rem;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] details > summary {
    background: #6D4C41;
    color: #FFFDF7 !important;
    padding: 10px 16px !important;
    border-radius: 5px !important;
    font-weight: 600;
    letter-spacing: 0.12em;
    font-size: 1rem;
}
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary div {
    color: #FFFDF7 !important;
}
[data-testid="stExpander"] summary svg {
    fill: #FFFDF7 !important;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    padding: 12px 16px !important;
}
.week-day-header {
    color: #5D4037;
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    margin: 14px 0 4px 2px;
    padding-bottom: 4px;
    border-bottom: 1px dashed #C7B299;
}
.week-day-header.is-today {
    color: #8B4513;
}
.week-day-header.is-today::before {
    content: "● ";
    color: #A0522D;
}
/* 剪輯師卡片 */
.editor-card {
    background: #FFFDF7;
    border: 1px solid #D7CCBE;
    border-radius: 8px;
    padding: 18px 20px 14px;
    margin-bottom: 1rem;
    box-shadow: 0 2px 6px rgba(93, 64, 55, 0.05);
}
.editor-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid #EDE3D0;
    padding-bottom: 10px;
    margin-bottom: 12px;
}
.editor-name {
    font-size: 1.25rem;
    color: #3E2723;
    font-weight: 600;
    letter-spacing: 0.08em;
}
.editor-count {
    background: #EFE4D0;
    color: #6D4C41;
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 0.78rem;
    letter-spacing: 0.05em;
}
.case-row {
    display: flex;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px dotted #EDE3D0;
    font-size: 0.95rem;
}
.case-row:last-child { border-bottom: none; }
.case-date {
    color: #8B6F47;
    font-family: "Courier New", monospace;
    font-size: 0.85rem;
    min-width: 52px;
    margin-right: 12px;
}
.case-name {
    color: #3E2723;
    flex: 1;
}
.case-link {
    color: #8B6F47;
    font-size: 0.78rem;
    text-decoration: none;
    border: 1px solid #D7CCBE;
    padding: 1px 8px;
    border-radius: 10px;
    margin-left: 8px;
    white-space: nowrap;
}
.case-link:hover {
    background: #EFE4D0;
    color: #3E2723;
}
.case-row.today .case-date,
.case-row.today .case-name {
    color: #8B4513;
    font-weight: 700;
}
.case-row.today .case-date::before {
    content: "●";
    color: #A0522D;
    margin-right: 4px;
    font-size: 0.7rem;
}
.empty-hint {
    background: #FFFDF7;
    border: 1px dashed #C7B299;
    border-radius: 6px;
    padding: 16px 20px;
    color: #8B6F47;
    text-align: center;
    letter-spacing: 0.05em;
}
</style>
""", unsafe_allow_html=True)

# ── UI ──────────────────────────────────────────────────────────────────

now = datetime.now(timezone(timedelta(hours=8)))
today_str = now.strftime("%m/%d").lstrip("0").replace("/0", "/")
today_padded = now.strftime("%m/%d")

col_title, col_btn = st.columns([5, 1])
with col_title:
    st.markdown('<h1 class="app-title">原初映像影音製作 ・ 剪輯行程表</h1>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="app-sub">每 5 分鐘自動更新 ・ 最後載入 {now.strftime("%Y-%m-%d %H:%M")} ・ 今天 {today_str}</div>',
        unsafe_allow_html=True,
    )
with col_btn:
    st.write("")
    if st.button("重新載入", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df, err = load_and_parse()

if df is None:
    st.error(err)
    st.stop()

all_editors = sorted(df["剪輯"].unique().tolist())

st.markdown("<br>", unsafe_allow_html=True)
col_f1, col_f2 = st.columns([2, 3])
with col_f1:
    selected = st.multiselect(
        "篩選剪輯師",
        all_editors,
        default=[],
        placeholder="選擇剪輯師…",
    )
with col_f2:
    all_months = sorted(df["拍攝日期"].str.split("/").str[0].unique().tolist(),
                        key=lambda x: int(x))
    selected_months = st.multiselect(
        "篩選月份",
        all_months,
        default=[],
        format_func=lambda m: f"{int(m)} 月",
        placeholder="選擇月份…",
    )

filtered = df.copy()
if selected:
    filtered = filtered[filtered["剪輯"].isin(selected)]
if selected_months:
    filtered = filtered[filtered["拍攝日期"].str.split("/").str[0].isin(selected_months)]

st.markdown("---")

# ── Today highlight（永遠顯示，不受篩選影響） ────────────────────────────
today_date = now.date()
today_rows_all = df[df["full_date"].dt.date == today_date]
st.markdown(f'<div class="today-label">今 日 ・ {today_str}</div>', unsafe_allow_html=True)
if today_rows_all.empty:
    st.markdown(
        '<div class="today-empty">今天沒有剪輯案件</div>',
        unsafe_allow_html=True,
    )
else:
    for _, r in today_rows_all.iterrows():
        link_html = ""
        if r["連結"]:
            link_html = f' <a class="case-link" href="{r["連結"]}" target="_blank">雲端</a>'
        st.markdown(
            f'<div class="today-item"><strong>{r["剪輯"]}</strong>{r["案子"]}{link_html}</div>',
            unsafe_allow_html=True,
        )
st.markdown("---")

# ── This week（今天起 7 天，依日期分組，不受篩選影響） ────────────────────
weekday_names = ['一', '二', '三', '四', '五', '六', '日']
week_days = []
for i in range(7):
    d = now + timedelta(days=i)
    week_days.append({
        "date_obj": d,
        "mmdd_short": d.strftime("%m/%d").lstrip("0").replace("/0", "/"),
        "mmdd_padded": d.strftime("%m/%d"),
        "weekday": weekday_names[d.weekday()],
    })

week_start_disp = week_days[0]["mmdd_short"]
week_end_disp = week_days[-1]["mmdd_short"]
# 首頁（未選剪輯師）預設展開，個人搜尋後預設收起
week_expanded = len(selected) == 0
week_expander = st.expander(
    f"本 週 ・ {week_start_disp} ~ {week_end_disp}",
    expanded=week_expanded,
)

week_date_objs = [d["date_obj"].date() for d in week_days]
week_rows_all = df[df["full_date"].dt.date.isin(week_date_objs)]

with week_expander:
    if week_rows_all.empty:
        st.markdown(
            '<div class="today-empty">本週沒有剪輯案件</div>',
            unsafe_allow_html=True,
        )
    else:
        for day in week_days:
            day_d = day["date_obj"].date()
            day_rows = df[df["full_date"].dt.date == day_d]
            if day_rows.empty:
                continue
            is_today_day = day_d == today_date
            header_cls = "week-day-header is-today" if is_today_day else "week-day-header"
            today_tag = "（今日）" if is_today_day else ""
            st.markdown(
                f'<div class="{header_cls}">{day["mmdd_short"]}（{day["weekday"]}）{today_tag}</div>',
                unsafe_allow_html=True,
            )
            for _, r in day_rows.iterrows():
                link_html = ""
                if r["連結"]:
                    link_html = f' <a class="case-link" href="{r["連結"]}" target="_blank">雲端</a>'
                st.markdown(
                    f'<div class="today-item"><strong>{r["剪輯"]}</strong>{r["案子"]}{link_html}</div>',
                    unsafe_allow_html=True,
                )

st.markdown("---")

# ── Per-editor cards ─────────────────────────────────────────────────────
if not selected:
    st.markdown(
        '<div class="empty-hint">請在上方選擇剪輯師，查看個別案件</div>',
        unsafe_allow_html=True,
    )
    st.stop()

show_editors = selected
cols = st.columns(2)

for idx, editor in enumerate(show_editors):
    edf = (
        filtered[filtered["剪輯"] == editor][["full_date", "拍攝日期", "案子", "連結"]]
        .reset_index(drop=True)
    )
    if edf.empty:
        continue

    rows_html = ""
    for _, row in edf.iterrows():
        is_today = row["full_date"].date() == today_date
        cls = "case-row today" if is_today else "case-row"
        link_html = ""
        if row["連結"]:
            link_html = f'<a class="case-link" href="{row["連結"]}" target="_blank">雲端</a>'
        rows_html += (
            f'<div class="{cls}">'
            f'<span class="case-date">{row["拍攝日期"]}</span>'
            f'<span class="case-name">{row["案子"]}</span>'
            f'{link_html}'
            f'</div>'
        )

    card_html = (
        '<div class="editor-card">'
        '<div class="editor-head">'
        f'<span class="editor-name">{editor}</span>'
        f'<span class="editor-count">共 {len(edf)} 案</span>'
        '</div>'
        f'{rows_html}'
        '</div>'
    )

    with cols[idx % 2]:
        st.markdown(card_html, unsafe_allow_html=True)
