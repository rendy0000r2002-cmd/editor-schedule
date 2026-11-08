"""
每日自動為行程表補雲端連結。

策略：
- 掃描 Sheet「2026年」過去 30 天 ~ 未來 14 天 內、沒有連結的案件
- 預載「房產」底下 5 個分類資料夾的子資料夾清單
- 依案件文字判斷分類 → 在該分類 folders 跑 fuzzy score → 取最高分若達 threshold 則寫入
- 已有連結 / 含「雲端連結」/ 命中 SKIP_RE 都跳過
- log 寫到 auto_fill_links.log
"""
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_PATH = r'C:\Users\rendy\service_account.json'
SPREADSHEET_ID = '1_naCZzjQ3G7W28RyaRe-ZuTEsr-sjeTBBf912pAJB-M'
SHEET_GID = 602539448
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets',
]
LOG_PATH = Path(__file__).with_name('auto_fill_links.log')

DAYS_BACK = 30
DAYS_AHEAD = 14
DISPLAY_TEXT = '雲端連結'
LINK_COLOR = {'red': 0.07, 'green': 0.33, 'blue': 0.80}

CATEGORY_DEFS = [
    ('qiyu',  '5_琦郁房事悄悄話', '1_業配'),
    ('other', '6_其他外發',        None),
    ('girl',  '12_日常女子開箱',   '0_業配'),
    ('amber', '7_Amber',           None),
    ('ivy',   '5_Ivy',             None),
]

SKIP_RE = re.compile(
    r'^(【拍攝】|家裡有事|掃墓|出國|參加婚禮|自己案子|'
    r'有人能支援|康兒不在|雅出國|葉出國|威成飛空拍|'
    r'看要不要|鹹\s|已購客|活動\+已購客|安曼活動|休假|請假|不在|下雨延)',
    re.IGNORECASE,
)

GENERIC_KW = {
    '即新聞', '街訪', '短影', '短影音', '開箱', '即賞屋', '女子', '單機',
    '雙機', '正片', '精華', '直短', '原創', '外發', '直式', '橫式',
    '空拍', '拍攝', '雲端連結',
    '李尚潔', '陳儀潔', '陳怡潔', '張琳', '李佳玲', '徐苡嫚', '苡嫚',
    '朱琦郁', '耀陽', '葉子', 'enzo', 'eddie',
}


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def get_creds():
    return service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)


def is_date_cell(t):
    if not isinstance(t, str): return False
    return bool(re.match(r'^\d{1,2}/\d{1,2}', t.strip())) and len(t.strip()) <= 10


def norm_date(t):
    m = re.match(r'^(\d{1,2})/(\d{1,2})', t.strip())
    return f'{int(m.group(1)):02d}/{int(m.group(2)):02d}'


def get_url_from_cell(cell):
    u = cell.get('hyperlink', '')
    if u and 'drive.google.com' in u: return u
    for run in cell.get('textFormatRuns', []):
        uri = run.get('format', {}).get('link', {}).get('uri', '')
        if uri and 'drive.google.com' in uri: return uri
    return None


def search_folder(drive, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    r = drive.files().list(q=q, fields='files(id,name)', pageSize=5).execute()
    return r.get('files', [])


def list_subfolders(drive, parent_id):
    q = (f"'{parent_id}' in parents and "
         f"mimeType='application/vnd.google-apps.folder' and trashed=false")
    r = drive.files().list(q=q, fields='files(id,name)', pageSize=500).execute()
    return r.get('files', [])


def build_folder_db(drive):
    """預載房產底下 5 個分類資料夾的子資料夾清單"""
    log('Loading 房產 folder DB...')
    fang = search_folder(drive, '房產')
    if not fang:
        log('  ERR: 找不到「房產」根目錄')
        return {}
    fang_id = fang[0]['id']

    db = {}
    for cat, top_name, sub_name in CATEGORY_DEFS:
        tops = search_folder(drive, top_name, parent_id=fang_id)
        if not tops:
            tops = search_folder(drive, top_name)  # fallback
        if not tops:
            log(f'  WARN: 找不到 {top_name}')
            db[cat] = []
            continue
        parent_id = tops[0]['id']
        if sub_name:
            subs = search_folder(drive, sub_name, parent_id=parent_id)
            if not subs:
                log(f'  WARN: 找不到 {top_name}/{sub_name}')
                db[cat] = []
                continue
            parent_id = subs[0]['id']
        folders = list_subfolders(drive, parent_id)
        db[cat] = [(f['name'], f['id']) for f in folders]
        log(f'  {cat}: {len(db[cat])} 個資料夾')
    return db


def determine_category(text):
    if re.search(r'Amber', text, re.IGNORECASE): return 'amber'
    if re.search(r'Ivy', text, re.IGNORECASE): return 'ivy'
    if '琦郁' in text: return 'qiyu'
    if '即賞屋' in text or '即新聞' in text or '街訪' in text: return 'other'
    if re.search(r'外[\s　]', text): return 'other'
    return 'girl'


def should_skip(text):
    t = text.strip()
    if not t: return True
    if '暫' in t: return True
    if SKIP_RE.match(t): return True
    if DISPLAY_TEXT in t: return True
    if 'drive.google.com' in t: return True
    return False


def strip_generic(s):
    out = s
    for gk in sorted(GENERIC_KW, key=len, reverse=True):
        out = out.replace(gk.lower(), '')
    return out


def normalize_for_score(s):
    return re.sub(r'[^一-鿿A-Za-z0-9]', '', s).lower()


def score_match(cell_text, folder_name):
    """fuzzy score：n-gram 比對，strip generic 後計分"""
    cell_norm = normalize_for_score(cell_text)
    cell_has_date = bool(re.match(r'\d{4}', cell_norm))

    if cell_has_date:
        f_norm = normalize_for_score(folder_name)
    else:
        f_core = re.sub(r'^\d{4}[\s_-]?', '', folder_name)
        f_norm = normalize_for_score(f_core)

    cell_scored = strip_generic(cell_norm)
    f_scored = strip_generic(f_norm)

    score = 0
    for n in range(min(8, len(f_scored)), 1, -1):
        for i in range(len(f_scored) - n + 1):
            chunk = f_scored[i:i+n]
            has_chinese = bool(re.search(r'[一-鿿]', chunk))
            if not has_chinese: continue  # 只計中文 chunk，數字交給 MMDD prefix 把關
            if chunk in cell_scored:
                score += n
    return score


def find_best_folder(cell_text, mmdd, folder_db):
    """在分類資料夾內找最佳匹配。優先取名稱以 MMDD 開頭的；若無則允許任意。"""
    cat = determine_category(cell_text)
    folders = folder_db.get(cat, [])
    if not folders: return None, cat

    # 優先在「名稱以 MMDD 開頭」的資料夾找；找不到就 fallback 到全分類（適用 amber/qiyu 不帶日期前綴的命名）
    mmdd_folders = [(n, fid) for n, fid in folders if re.match(rf'^{mmdd}', n)]
    if mmdd_folders:
        candidates = mmdd_folders
        strict = True
    else:
        # fallback 是給不帶日期前綴的命名（amber/qiyu）用；帶別的日期前綴、差超過 7 天的資料夾＝別的案子
        # （2026-11-08：11/20 新潤世界城 被配到 0820 新潤世界都心）
        def _far(n):
            m = re.match(r'^(\d{2})(\d{2})', n)
            if not m: return False
            try:
                a1 = datetime(2000, int(mmdd[:2]), int(mmdd[2:])); a2 = datetime(2000, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return False
            d = abs((a1 - a2).days)
            return min(d, 366 - d) > 7
        candidates = [(n, fid) for n, fid in folders if not _far(n)]
        if not candidates: return None, cat
        strict = False

    scored = [(n, fid, score_match(cell_text, n)) for n, fid in candidates]
    scored.sort(key=lambda x: -x[2])
    best = scored[0]
    if best[2] == 0: return None, cat

    # 動態 threshold（中文核心字數）
    f_core = re.sub(r'^\d{4}[\s_-]?', '', best[0])
    zh_len = len(re.findall(r'[一-鿿]', f_core))
    threshold = 3 if zh_len <= 4 else 4
    # 沒有 MMDD 約束時要求更高分數（純中文 chunk 計分，門檻 8 = 至少 4 字共現或多組 2-3 字）
    if not strict:
        threshold = max(threshold, 8)

    if best[2] < threshold: return None, cat
    return best, cat


def main(dry_run=False):
    creds = get_creds()
    sheets = build('sheets', 'v4', credentials=creds)
    drive = build('drive', 'v3', credentials=creds)

    folder_db = build_folder_db(drive)
    if not any(folder_db.values()):
        log('FATAL: FOLDER_DB 全空')
        return

    # 找 2026年 sheet（或最新 YYYY年/YYYY年M月）
    meta = sheets.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_name = sheet_id = None
    for s in meta['sheets']:
        if s['properties'].get('sheetId') == SHEET_GID:
            sheet_name = s['properties']['title']
            sheet_id = SHEET_GID
            break
    if not sheet_name:
        log(f'FATAL: 找不到 sheetId={SHEET_GID}')
        return
    log(f'Target sheet: {sheet_name} (id={sheet_id}), dry_run={dry_run}')

    result = sheets.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, ranges=[sheet_name], includeGridData=True
    ).execute()
    rows = result['sheets'][0]['data'][0].get('rowData', [])
    grid = []
    for row in rows:
        rc = []
        for cell in row.get('values', []):
            rc.append({
                'text': (cell.get('formattedValue', '') or '').strip(),
                'url': get_url_from_cell(cell),
            })
        grid.append(rc)
    maxc = max((len(r) for r in grid), default=0)
    for r in grid:
        while len(r) < maxc: r.append({'text': '', 'url': None})

    date_rows = [i for i, r in enumerate(grid) if sum(1 for c in r if is_date_cell(c['text'])) >= 3]
    if not date_rows:
        log('No date rows found.')
        return

    def wcols(r):
        return [(ci, norm_date(c['text'])) for ci, c in enumerate(r) if is_date_cell(c['text'])][:7]

    m_year = re.match(r'^(\d{4})年', sheet_name)
    sheet_year = int(m_year.group(1)) if m_year else datetime.now().year

    rollovers = 0; pm = None
    for dr in date_rows:
        for _, ds in wcols(grid[dr]):
            mo = int(ds.split('/')[0])
            if pm is not None and mo < pm: rollovers += 1
            pm = mo
    m_month = re.match(r'^\d{4}年(\d{1,2})月$', sheet_name)
    running = sheet_year if m_month else (sheet_year - rollovers)
    if not m_month:
        # 2026-11-08：分頁排到隔年（「2026年」最後一列 12/27–1/2）→ 末段其實是 YYYY+1，整張會差一年。
        # 用最後一個日期校正（同 editor_schedule app.py）：早於今天 60 天前 +1、晚於 400 天後 −1。
        last = None
        for dr in date_rows:
            for _, ds in wcols(grid[dr]):
                last = tuple(map(int, ds.split('/')))
        t0 = datetime.now(timezone(timedelta(hours=8))).date()
        for _ in range(3):
            if not last: break
            try: ld = datetime(running + rollovers, last[0], last[1]).date()
            except ValueError: break
            if ld < t0 - timedelta(days=60): running += 1
            elif ld > t0 + timedelta(days=400): running -= 1
            else: break

    cell_dates = {}
    pm = None
    for dr in date_rows:
        for ci, ds in wcols(grid[dr]):
            mo, da = map(int, ds.split('/'))
            if pm is not None and mo < pm: running += 1
            try:
                cell_dates[(dr, ci)] = datetime(running, mo, da)
            except ValueError: pass
            pm = mo

    today = datetime.now(timezone(timedelta(hours=8))).date()
    start = today - timedelta(days=DAYS_BACK)
    end = today + timedelta(days=DAYS_AHEAD)

    targets = []
    for dr in date_rows:
        date_cols = [ci for ci, _ in wcols(grid[dr])]
        for off in range(1, 5):
            ni = dr + off
            if ni >= len(grid): break
            if sum(1 for c in grid[ni] if is_date_cell(c['text'])) >= 3: break
            for ci in date_cols:
                dt = cell_dates.get((dr, ci))
                if not dt: continue
                d = dt.date()
                if not (start <= d <= end): continue
                if ci >= len(grid[ni]): continue
                cell = grid[ni][ci]
                text = cell['text']
                if not text: continue
                if cell['url']: continue
                if should_skip(text): continue
                targets.append({'row': ni, 'col': ci, 'date': d, 'text': text})

    log(f'{len(targets)} cells need lookup.')
    if not targets:
        return

    updates = []
    for t in targets:
        mmdd = t['date'].strftime('%m%d')
        result = find_best_folder(t['text'], mmdd, folder_db)
        best, cat = result
        if not best:
            log(f"SKIP [{mmdd}] [{cat}] {t['text'][:40]} — no match")
            continue
        fname, fid, score = best
        url = f'https://drive.google.com/drive/folders/{fid}'

        new_text = t['text'].rstrip() + '\n' + DISPLAY_TEXT
        link_start = len(t['text'].rstrip()) + 1
        updates.append({
            'row': t['row'], 'col': t['col'], 'date': mmdd, 'cat': cat,
            'snippet': t['text'][:30], 'folder': fname, 'score': score,
            'new_text': new_text, 'link_start': link_start, 'url': url,
        })
        log(f"MATCH [{mmdd}] [{cat}] {t['text'][:30]} → {fname} (score={score})")

    if dry_run:
        log(f'[dry-run] Would update {len(updates)} cells.')
        return

    if not updates:
        log('Nothing to update.')
        return

    requests = []
    for u in updates:
        requests.append({
            'updateCells': {
                'rows': [{
                    'values': [{
                        'userEnteredValue': {'stringValue': u['new_text']},
                        'textFormatRuns': [
                            {'startIndex': 0, 'format': {}},
                            {
                                'startIndex': u['link_start'],
                                'format': {
                                    'foregroundColor': LINK_COLOR,
                                    'underline': True,
                                    'link': {'uri': u['url']},
                                },
                            },
                        ],
                    }],
                }],
                'fields': 'userEnteredValue,textFormatRuns',
                'start': {'sheetId': sheet_id, 'rowIndex': u['row'], 'columnIndex': u['col']},
            }
        })

    BATCH = 500
    for i in range(0, len(requests), BATCH):
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': requests[i:i+BATCH]},
        ).execute()
    log(f'Updated {len(requests)} cells.')


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    try:
        main(dry_run=dry)
    except Exception as e:
        log(f'FATAL: {e}')
        raise
