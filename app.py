import streamlit as st
import re
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

st.set_page_config(
    page_title="剪輯行程表",
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
        for key, val in EDITOR_MAP.items():
            if raw == key or raw.lower() == key.lower():
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


@st.cache_data(ttl=300)
def load_and_parse():
    try:
        creds, creds_err = get_credentials()
        if not creds:
            return None, creds_err or "無法取得 Google 憑證"
        service = build('sheets', 'v4', credentials=creds)
        result = service.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID,
            ranges=[SHEET_NAME],
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

    half = len(date_row_indices) // 2
    relevant = date_row_indices[half:] if half > 0 else date_row_indices

    # Year rollover detection
    date_row_to_year_flag = {}
    prev_month = None
    rolled_over = False
    for dr in relevant:
        header = grid[dr]
        months_in_row = [
            int(t.strip().split("/")[0])
            for (t, _) in header
            if is_date_cell(t) and "/" in t.strip()
        ]
        if not months_in_row:
            continue
        cur_month = min(months_in_row)
        if prev_month is not None and cur_month < prev_month:
            rolled_over = True
        date_row_to_year_flag[dr] = rolled_over
        prev_month = cur_month

    records = []
    seen = set()
    for dr in relevant:
        is_current_year = date_row_to_year_flag.get(dr, False)
        header = grid[dr]
        date_cols = [
            (ci, normalize_date(t))
            for ci, (t, _) in enumerate(header)
            if is_date_cell(t)
        ]
        for offset in range(1, 5):
            next_idx = dr + offset
            if next_idx >= num_rows:
                break
            next_row = grid[next_idx]
            if sum(1 for (t, _) in next_row if is_date_cell(t)) >= 3:
                break
            for col_i, date_str in date_cols:
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
                mo = int(date_str.split("/")[0])
                if not is_current_year:
                    continue
                if not (3 <= mo <= 11):
                    continue
                key = (date_str, case_name[:20], editor)
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "拍攝日期": date_str,
                    "案子": case_name,
                    "剪輯": editor,
                    "連結": cell_url or "",
                })

    if not records:
        return None, "未找到含剪輯分配的記錄（格式需為：案名(剪輯師)）"

    import pandas as pd
    result_df = (
        pd.DataFrame(records)
        .sort_values("拍攝日期")
        .reset_index(drop=True)
    )
    return result_df, None


# ── UI ──────────────────────────────────────────────────────────────────

st.title("🎬 剪輯行程表")

now = datetime.now(timezone(timedelta(hours=8)))
today_str = now.strftime("%m/%d").lstrip("0").replace("/0", "/")
today_padded = now.strftime("%m/%d")

col_title, col_btn = st.columns([5, 1])
with col_title:
    st.caption(f"每 5 分鐘自動更新 ｜ 最後載入：{now.strftime('%Y-%m-%d %H:%M')} ｜ 今天 {today_str}")
with col_btn:
    if st.button("🔄 重新載入", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df, err = load_and_parse()

if df is None:
    st.error(err)
    st.stop()

all_editors = sorted(df["剪輯"].unique().tolist())

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

# ── Today highlight ──────────────────────────────────────────────────────
today_rows = filtered[
    filtered["拍攝日期"].isin([today_str, today_padded])
]
if not today_rows.empty:
    st.subheader(f"📅 今日 {today_str}")
    for _, r in today_rows.iterrows():
        link_html = ""
        if r["連結"]:
            link_html = f' &nbsp;<a href="{r["連結"]}" target="_blank" style="color:#555;font-size:0.85rem;">雲端連結</a>'
        st.markdown(
            f'<p style="color:#8B0000; font-size:1rem; margin:4px 0;">'
            f'<strong>{r["剪輯"]}</strong> — {r["案子"]}{link_html}</p>',
            unsafe_allow_html=True,
        )
    st.markdown("---")

# ── Per-editor cards ─────────────────────────────────────────────────────
if not selected:
    st.markdown("#### 請在上方選擇剪輯師")
    st.stop()

show_editors = selected
cols = st.columns(2)

for idx, editor in enumerate(show_editors):
    edf = (
        filtered[filtered["剪輯"] == editor][["拍攝日期", "案子", "連結"]]
        .reset_index(drop=True)
    )
    if edf.empty:
        continue

    with cols[idx % 2]:
        st.subheader(f"✂️ {editor}　{len(edf)} 案")

        for _, row in edf.iterrows():
            is_today = row["拍攝日期"] in (today_str, today_padded)
            color = "#8B0000" if is_today else "#333"
            weight = "bold" if is_today else "normal"
            link_html = ""
            if row["連結"]:
                link_html = f' <a href="{row["連結"]}" target="_blank" style="color:#888;font-size:0.8rem;">雲端</a>'
            st.markdown(
                f'<p style="color:{color}; font-weight:{weight}; font-size:0.95rem; margin:2px 0;">'
                f'{row["拍攝日期"]} &nbsp;{row["案子"]}{link_html}</p>',
                unsafe_allow_html=True,
            )
