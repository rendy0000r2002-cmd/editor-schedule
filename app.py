import streamlit as st
import pandas as pd
import re
import io
import urllib.request
from datetime import datetime

st.set_page_config(
    page_title="剪輯行程表",
    layout="wide",
    page_icon="🎬"
)

# Auto refresh every 5 minutes via meta tag
st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1_naCZzjQ3G7W28RyaRe-ZuTEsr-sjeTBBf912pAJB-M"
    "/export?format=csv&gid=602539448"
)

EDITOR_MAP = {
    "jia": "游承佳", "Jia": "游承佳", "JIA": "游承佳", "承佳": "游承佳",
    "雅": "黃雅憶", "雅憶": "黃雅憶",
    "康": "蔡守康",
    "P": "游郁萍", "p": "游郁萍",
    "賢": "余品賢",
    "夏": "夏竹", "夏子": "夏竹",
    "耀陽": "耀陽",
    "瑞": "涂家瑞",
    "安": "安",
}

EXCLUDE_KEYWORDS = ["出國", "自己案子", "家裡有事", "下雨延", "喜華不行", "休假", "請假", "不在"]


def normalize_editor(name: str) -> str:
    return EDITOR_MAP.get(name.strip(), name.strip())


def extract_editor(text) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    m = re.search(r"[（(]([^）)\n]+)[）)]", text)
    if m:
        raw = m.group(1).strip()
        # Strict: only accept known abbreviations (exact match, case-insensitive for Latin)
        for key, val in EDITOR_MAP.items():
            if raw == key or raw.lower() == key.lower():
                return val
    return None


def remove_editor_tag(text) -> str:
    if not isinstance(text, str):
        return ""
    # Remove date prefix if present (e.g. "4/12Amber外..." → "Amber外...")
    cleaned = re.sub(r"^\d{1,2}/\d{1,2}", "", text).strip()
    # Remove time prefix like "0401 某某"
    cleaned = re.sub(r"^0?\d{3,4}\s", "", cleaned).strip()
    # Remove editor tag
    cleaned = re.sub(r"\s*[（(][^）)\n]+[）)]\s*", "", cleaned).strip()
    # Remove trailing notes after newline
    cleaned = cleaned.split("\n")[0].strip()
    # Clean up "AI" suffix, extra asterisks info
    return cleaned if cleaned else text


def is_date_cell(val) -> bool:
    """Match only pure date cells like '4/12', '4/12(一)', NOT '4/12Amber外...'"""
    if not isinstance(val, str):
        return False
    v = val.strip()
    # Must start with date pattern AND be short (≤10 chars = no extra case text)
    return bool(re.match(r"^\d{1,2}/\d{1,2}", v)) and len(v) <= 10


def normalize_date(val) -> str:
    m = re.match(r"^(\d{1,2})/(\d{1,2})", str(val).strip())
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        return f"{mo:02d}/{day:02d}"
    return str(val)


def is_excluded(text) -> bool:
    if not isinstance(text, str):
        return True
    return any(kw in text for kw in EXCLUDE_KEYWORDS)


@st.cache_data(ttl=300)
def load_and_parse():
    try:
        req = urllib.request.Request(SHEET_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8")
        df = pd.read_csv(io.StringIO(content), header=None, dtype=str)
    except Exception as e:
        return None, f"下載失敗：{e}"

    # Find all date-header rows (rows with ≥3 pure date cells)
    date_row_indices = []
    for i in range(len(df)):
        row = df.iloc[i]
        if sum(1 for v in row if is_date_cell(v)) >= 3:
            date_row_indices.append(i)

    if not date_row_indices:
        return pd.DataFrame(), "找不到日期列"

    # Take second half (lower block = current year)
    half = len(date_row_indices) // 2
    relevant = date_row_indices[half:] if half > 0 else date_row_indices

    # Detect year boundary: second half goes Sep2025→Dec2025→Jan2026→...
    # Find where month resets (Dec→Jan = year rollover), then keep only
    # records from March of the new year onwards.
    # Strategy: tag each record with whether it's post-rollover.
    month_seq = []
    date_row_to_year_flag = {}  # dr index → True if current year (2026)
    prev_month = None
    rolled_over = False
    for dr in relevant:
        header = df.iloc[dr]
        months_in_row = [
            int(v.strip().split("/")[0])
            for v in header
            if is_date_cell(v) and "/" in v.strip()
        ]
        if not months_in_row:
            continue
        cur_month = min(months_in_row)
        if prev_month is not None and cur_month < prev_month:
            rolled_over = True
        date_row_to_year_flag[dr] = rolled_over
        prev_month = cur_month

    # Re-parse with year flag so we can attach it to records
    records2 = []
    seen2 = set()
    for dr in relevant:
        is_current_year = date_row_to_year_flag.get(dr, False)
        header = df.iloc[dr]
        date_cols = [
            (ci, normalize_date(v))
            for ci, v in enumerate(header)
            if is_date_cell(v)
        ]
        for offset in range(1, 5):
            next_idx = dr + offset
            if next_idx >= len(df):
                break
            next_row = df.iloc[next_idx]
            if sum(1 for v in next_row if is_date_cell(v)) >= 3:
                break
            for col_i, date_str in date_cols:
                if col_i >= len(next_row):
                    continue
                cell = next_row.iloc[col_i]
                if not isinstance(cell, str) or cell.strip() in ("", "nan"):
                    continue
                if any(kw in cell for kw in EXCLUDE_KEYWORDS):
                    continue
                editor = extract_editor(cell)
                if not editor:
                    continue
                case_name = remove_editor_tag(cell)
                if not case_name:
                    continue
                mo = int(date_str.split("/")[0])
                # Only keep: current year (post-rollover), month 3-11
                # month 12 in post-rollover = transition week artifact (Dec 2025)
                if not is_current_year:
                    continue
                if not (3 <= mo <= 11):
                    continue
                key = (date_str, case_name[:20], editor)
                if key in seen2:
                    continue
                seen2.add(key)
                records2.append({"拍攝日期": date_str, "案子": case_name, "剪輯": editor})

    if not records2:
        return pd.DataFrame(), "未找到含剪輯分配的記錄（格式需為：案名(剪輯師)）"

    result = (
        pd.DataFrame(records2)
        .sort_values("拍攝日期")
        .reset_index(drop=True)
    )

    return result, None


# ── UI ──────────────────────────────────────────────────────────────────

st.title("🎬 剪輯行程表")

now = datetime.now()
today_str = now.strftime("%m/%d").lstrip("0").replace("/0", "/")  # "04/15" → "4/15"
today_padded = now.strftime("%m/%d")  # "04/15"

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

if df.empty:
    st.warning(f"⚠️ {err}")
    st.stop()

# ── Filters ─────────────────────────────────────────────────────────────
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
        st.markdown(
            f'<p style="color:#8B0000; font-size:1rem; margin:4px 0;">'
            f'<strong>{r["剪輯"]}</strong> — {r["案子"]}</p>',
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
        filtered[filtered["剪輯"] == editor][["拍攝日期", "案子"]]
        .reset_index(drop=True)
    )
    if edf.empty:
        continue

    with cols[idx % 2]:
        st.subheader(f"✂️ {editor}　{len(edf)} 案")

        def highlight_today(row):
            if row["拍攝日期"] in (today_str, today_padded):
                return ["color: #8B0000; font-weight: bold"] * len(row)
            return [""] * len(row)

        styled = edf.style.apply(highlight_today, axis=1)
        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            column_config={
                "拍攝日期": st.column_config.TextColumn("拍攝日期", width=85),
                "案子": st.column_config.TextColumn("案子名稱"),
            },
        )

