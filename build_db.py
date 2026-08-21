"""
北海道の大雨資料(第14編/第15編)のPDF群を rainfall.db (SQLite) へ変換する。

観測所調書ページと、別種の「時間雨量調書(欠測状況等)」ページが交互に出現するため、
緯度経度が載っている観測所調書ページだけを判定して抽出する。
"""
import re
import sqlite3
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

SRC_DIR = Path(__file__).parent.parent
OUT_DIR = Path(__file__).parent
DB_PATH = OUT_DIR / "rainfall.db"

STATIONS_PDF = SRC_DIR / "ooame14-1_1-1.pdf"
KAKURITSU_NICHI_PDF = SRC_DIR / "ooame14-2_1.pdf"
KAKURITSU_JIKAN_PDF = SRC_DIR / "ooame14-2_2.pdf"
KYOKUSEN_PDF = SRC_DIR / "ooame14-2_3-1.pdf"

NENMAX_NICHI_14_PDFS = [SRC_DIR / "ooame14-1_2.pdf"]
NENMAX_NICHI_15_PDFS = [
    SRC_DIR / "第15編_004_年最大日雨量順位表(1前半).pdf",
    SRC_DIR / "第15編_004_年最大日雨量順位表(2後半).pdf",
]
NENMAX_JIKAN_14_PDFS = [SRC_DIR / "ooame14-1_3-1.pdf", SRC_DIR / "ooame14-1_3-2.pdf"]
NENMAX_JIKAN_15_PDFS = [SRC_DIR / "第15編_005_年最大時間雨量順位表.pdf"]

KYOKUSEN_NENGEN = [200, 100, 70, 50, 30, 20, 10, 7, 5, 3]
# グラフの軸ラベル等、駅観測所名として誤検出しないための除外語
KYOKUSEN_LABEL_WORDS = {
    "雨 量 強 度 (mm/hr)", "時 間", "確 率 雨 量 強 度 曲 線 図", "Y a n b",
}
# 観測所名らしい行: 数字/記号を含まない、ある程度短い1トークン(和名・カタカナ)
KYOKUSEN_NAME_RE = re.compile(r"^[぀-ヿ一-鿿ｦ-ﾟー\(\)（）\*＊]{1,10}$")

KAKURITSU_NENGEN = [3, 5, 7, 10, 20, 30, 50, 70, 100, 200]
NENGEN_SET = set(KAKURITSU_NENGEN)

REGION_RE = re.compile(r"^(.+?(?:総合)?振興局)$")
# 例: "青山中央 石狩郡当別町青山奥3263　　　　 S27年 43ﾟ24.5' 資料数"
LINE1_RE = re.compile(
    r"^(?P<name>\S+?)\s+(?P<address>.+?)\s+"
    r"(?P<start>[MTSHR]\s?\d+)年\s+"
    r"(?P<lat_deg>\d{1,3})ﾟ(?P<lat_min>\d{1,2}\.\d)'"
)
# 例: "141ﾟ35.2' 収集開始年"
LINE2_RE = re.compile(r"^(?P<lon_deg>\d{1,3})ﾟ(?P<lon_min>\d{1,2}\.\d)'")
# 例: "ｱｵﾔﾏﾁｭｳｵｳ 14年 80 欠測"  /  "ｻｯﾎﾟﾛ 札幌管区気象台 143年 17 S40. 4. 1 欠測 -"
LINE3_RE = re.compile(
    r"^(?P<kana>[ｦ-ﾟA-Za-z\-]+)\s+(?P<rest>.*?)(?P<years>\d+)年\s+(?P<elev>\d+)\b"
)


def dms_to_decimal(deg: str, minute: str) -> float:
    return round(int(deg) + float(minute) / 60, 6)


def parse_stations():
    reader = PdfReader(STATIONS_PDF)
    rows = []
    current_region = None
    skipped = 0
    for page in reader.pages:
        text = page.extract_text()
        if not text:
            continue
        lines = [ln for ln in text.split("\n")]
        # 登録所ページ判定: 見出し行があるページのみ対象
        if "観測所名" not in text or "緯度" not in text:
            continue

        i = 0
        while i < len(lines):
            ln = lines[i].strip()
            region_m = REGION_RE.match(ln)
            if region_m and ("振興局" in ln) and len(ln) < 20:
                current_region = region_m.group(1)
                i += 1
                continue

            m1 = LINE1_RE.match(ln)
            if m1 and i + 1 < len(lines):
                m2 = LINE2_RE.match(lines[i + 1].strip())
                if m2:
                    lat = dms_to_decimal(m1.group("lat_deg"), m1.group("lat_min"))
                    lon = dms_to_decimal(m2.group("lon_deg"), m2.group("lon_min"))
                    elev = None
                    if i + 2 < len(lines):
                        m3 = LINE3_RE.match(lines[i + 2].strip())
                        if m3:
                            elev = int(m3.group("elev"))
                    rows.append({
                        "name": m1.group("name"),
                        "region": current_region,
                        "address": m1.group("address").strip("　 "),
                        "start_year": m1.group("start"),
                        "lat": lat,
                        "lon": lon,
                        "elevation": elev,
                    })
                    i += 3
                    continue
                else:
                    skipped += 1
            i += 1
    print(f"stations parsed: {len(rows)}, line1-matched-but-no-line2: {skipped}")
    return rows


# 例: "帯広 80 92 100 109 124 133 144 151 159 174 Gumbel"
# 例: "上足寄(道) 66 77 84 91 104 112 121 128 134 147 Gumbel"
KAKURITSU_ROW_RE = re.compile(
    r"^(?P<name>\S+?)\s+(?P<vals>(?:\d+\s+){9}\d+)\s+(?P<dist>[A-Za-z][A-Za-z0-9\-]*)"
    r"(?:\s+(?P<note>\S.*))?$"
)


def parse_kakuritsu_nichi():
    reader = PdfReader(KAKURITSU_NICHI_PDF)
    rows = []
    current_region = None
    started = False
    for page in reader.pages:
        text = page.extract_text()
        if not text:
            continue
        if "確率日雨量表" in text or "Ⅰ確率日雨量" in text:
            started = True
        if not started:
            continue
        for ln in text.split("\n"):
            ln = ln.strip()
            region_m = REGION_RE.match(ln)
            if region_m and "振興局" in ln and len(ln) < 20:
                current_region = region_m.group(1)
                continue
            m = KAKURITSU_ROW_RE.match(ln)
            if m:
                vals = [int(v) for v in m.group("vals").split()]
                for nengen, val in zip(KAKURITSU_NENGEN, vals):
                    rows.append({
                        "name": m.group("name"),
                        "region": current_region,
                        "nengen": nengen,
                        "value_mm": val,
                        "bunpu": m.group("dist"),
                        "note": m.group("note"),
                    })
    print(f"kakuritsu_nichi rows parsed: {len(rows)} ({len(rows)//10} stations)")
    return rows


def _rows_from_words(words, top_tol=3):
    """wordsを行にグループ化し、各行をx0順に並べる。[(top, [(x0,text),...]), ...]"""
    rows = []
    for w in sorted(words, key=lambda w: w["top"]):
        placed = False
        for r in rows:
            if abs(r[0] - w["top"]) <= top_tol:
                r[1].append((w["x0"], w["text"]))
                placed = True
                break
        if not placed:
            rows.append([w["top"], [(w["x0"], w["text"])]])
    for r in rows:
        r[1].sort(key=lambda t: t[0])
    rows.sort(key=lambda r: r[0])
    return rows


def _extract_blocks(rows):
    """「地名」行を境にブロック分割する(観測所名が同じ行に載っているページ用)。"""
    starts = [i for i, (_, cells) in enumerate(rows) if cells and cells[0][1] == "地名"]
    blocks = []
    for bi, start in enumerate(starts):
        end = starts[bi + 1] if bi + 1 < len(starts) else len(rows)
        name = rows[start][1][1][1] if len(rows[start][1]) > 1 else None
        blocks.append({"name": name, "rows": rows[start:end]})
    return blocks


def _extract_blocks_no_name(rows, n_blocks):
    """観測所名の記載が無い継続ページ用: 「N時間」ヘッダ行を境にブロック分割する。"""
    header_idxs = [
        i for i, (_, cells) in enumerate(rows)
        if cells and re.match(r"^\d+時間$", cells[0][1])
    ]
    blocks = []
    for bi, start in enumerate(header_idxs[:n_blocks]):
        end = header_idxs[bi + 1] if bi + 1 < len(header_idxs) else len(rows)
        blocks.append({"rows": rows[start:end]})
    return blocks


def _parse_block_data(rows):
    """1ブロック内から (時間区分の並び, 分布形の並び, {確率年: [(雨量,強度), ...]}) を取り出す。"""
    durations, dists = [], []
    data = {}
    for _, cells in rows:
        texts = [c[1] for c in cells]
        if not texts:
            continue
        if texts[0] == "項目":
            durations = texts[1:]
        elif texts[0] == "確率雨量":
            dists = texts[1:]
        elif texts[0].isdigit() and int(texts[0]) in NENGEN_SET:
            nengen = int(texts[0])
            vals = texts[1:]
            pairs = [(vals[j], vals[j + 1]) for j in range(0, len(vals) - 1, 2)]
            data[nengen] = pairs
    return durations, dists, data


def _parse_long_block_data(rows):
    """継続ページ(時間区分ラベルのみ・確率年の明示列なし)のブロックを解析する。
    1行目=時間区分ラベル、2行目=分布形、3行目=r/i見出し(捨てる)、
    以降のデータ行は上からKAKURITSU_NENGENの順(3,5,7,...,200年)に対応する。"""
    if not rows:
        return [], [], {}
    durations = [c[1] for c in rows[0][1]]
    dists = [c[1] for c in rows[1][1]] if len(rows) > 1 else []
    data = {}
    data_rows = [r for r in rows[2:] if r[1] and r[1][0][1] != "r"]
    for nengen, (_, cells) in zip(KAKURITSU_NENGEN, data_rows):
        vals = [c[1] for c in cells]
        data[nengen] = [(vals[j], vals[j + 1]) for j in range(0, len(vals) - 1, 2)]
    return durations, dists, data


def _nearest_cell(cells, target_x0, tol=15):
    best, best_d = None, tol
    for x0, text in cells:
        d = abs(x0 - target_x0)
        if d <= best_d:
            best, best_d = text, d
    return best


JAPANESE_CHAR_RE = re.compile(r"[一-龠ぁ-んァ-ヶｦ-ﾟ]")


def parse_nenmax_nichi(pdf_paths):
    """年最大日雨量順位表。1ページが7列に分かれ、各列は独立して観測所が
    切り替わる(短い観測所は1列で収まり、長い観測所は複数列にまたがる)ため、
    列(x座標の範囲)ごとに独立して「直前に出現した観測所名」を追跡しながら
    上から順に読む。「前日雨量/当日(順位・雨量・年月日)/翌日雨量」の3行1組のうち、
    順位の入る中央行だけを対象にする(前後日の値は今回は取得しない)。"""
    rows_out = []
    current_region = None
    for pdf_path in pdf_paths:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                if not words:
                    continue
                rank_x0s = sorted(w["x0"] for w in words if w["text"] == "順位")
                if not rank_x0s:
                    continue
                n_groups = len(rank_x0s)

                region_word = next((w["text"] for w in words if "振興局" in w["text"]), None)
                if region_word:
                    current_region = region_word

                # グループ間隔は約70pxで、日(最終列)まででも順位から+56px程度に収まるため、
                # 順位のx0を基準に固定幅で区切る(次グループの境界に依存しない)
                bounds = [(x0 - 5, x0 + 65) for x0 in rank_x0s]

                for gi, (lo, hi) in enumerate(bounds):
                    col_words = [
                        w for w in words
                        if lo <= w["x0"] < hi and w["top"] > 60 and "振興局" not in w["text"]
                    ]
                    col_rows = _rows_from_words(col_words, top_tol=3)
                    col_station = None
                    for _, cells in col_rows:
                        texts = [c[1] for c in cells]
                        if len(texts) == 1 and JAPANESE_CHAR_RE.search(texts[0]):
                            col_station = texts[0]
                            continue
                        if len(cells) < 2 or not cells[0][1].isdigit():
                            continue  # 前日/翌日の補足行、またはヘッダ行
                        rank_text = cells[0][1]
                        rain_text = cells[1][1]
                        date_text = " ".join(c[1] for c in cells[2:])
                        if col_station is None:
                            continue
                        try:
                            rain_val = int(rain_text)
                        except ValueError:
                            continue
                        rows_out.append({
                            "name": col_station,
                            "region": current_region,
                            "rank": int(rank_text),
                            "value_mm": rain_val,
                            "date": date_text,
                        })
    print(f"nenmax_nichi rows parsed: {len(rows_out)} from {[p.name for p in pdf_paths]}")
    return rows_out


DURATION_TOKEN_RE = re.compile(r"^\d+分$|^\d+時間$")


def _topmost_group(words, pred, top_tol=3):
    """条件predを満たす単語群のうち、最も上(topが最小)にある行だけを
    x0昇順で返す。ページごとにヘッダの絶対y座標が微妙に異なる資料があるため、
    固定の閾値(top<85等)に頼らず、動的に検出する。"""
    matched = [w for w in words if pred(w)]
    if not matched:
        return []
    min_top = min(w["top"] for w in matched)
    row = [w for w in matched if abs(w["top"] - min_top) <= top_tol]
    row.sort(key=lambda w: w["x0"])
    return row


def parse_nenmax_jikan_rank1(pdf_paths):
    """年最大時間雨量順位表から、各観測所・各時間区分の順位1(過去最大値)だけを取得する。
    この表は1観測所のデータが複数ページ(短時間側10分〜3時間+長時間側4時間〜24時間の
    ページが交互に、かつ観測所によって必要ページ数が変わる形)にまたがるため、
    全順位を追うには複雑なページ跨ぎの継続判定が必要になる。順位1だけであれば、
    「観測所名の直後に現れる最初のデータ行」が必ず順位1であることを使い、
    対応する長時間側ページの先頭データ行(同じ相対位置)と組み合わせるだけでよい。"""
    rows_out = []
    current_region = None
    pages_all = []
    page_source = []  # pages_all と同じ並びで (元ファイル名, ファイル内ページ番号) を記録
    opened = [pdfplumber.open(p) for p in pdf_paths]
    for pdf, path in zip(opened, pdf_paths):
        pages_all.extend(pdf.pages)
        fname = Path(path).name
        page_source.extend((fname, n) for n in range(1, len(pdf.pages) + 1))

    try:
        i = 0
        while i < len(pages_all):
            words = pages_all[i].extract_words()
            if not words:
                i += 1
                continue
            rank_words = [w for w in words if w["text"] == "順位"]
            is_short_page = bool(rank_words) and any(
                w["text"] == "時間" and w["x0"] < 100 for w in words
            )
            if not is_short_page:
                i += 1
                continue

            region_word = next((w["text"] for w in words if "振興局" in w["text"]), None)
            if region_word:
                current_region = region_word

            header_dur_words = _topmost_group(words, lambda w: DURATION_TOKEN_RE.match(w["text"]))
            durations_short = [w["text"] for w in header_dur_words]

            # 「順位」ヘッダ行より下だけを本文行として扱う(絶対座標には依存しない)
            rank_header_top = min(w["top"] for w in rank_words)
            body_rows = _rows_from_words(
                [w for w in words if w["top"] > rank_header_top + 3], top_tol=3
            )

            data_row_idx = -1
            pending_station = None
            station_at_index = {}  # data_row_idx(0始まり) -> 観測所名(順位1の行のみ記録)
            for _, cells in body_rows:
                texts = [c[1] for c in cells]
                if len(texts) == 1 and JAPANESE_CHAR_RE.search(texts[0]):
                    pending_station = texts[0]
                    continue
                if not cells or not cells[0][1].isdigit():
                    continue  # ヘッダ行や解析不能な行
                data_row_idx += 1
                if pending_station is None:
                    continue  # 順位2以降の行(順位1のみ対象なのでスキップ)
                station_name = pending_station
                station_at_index[data_row_idx] = station_name
                pending_station = None

                # 順位1行なので、この行(短時間側)の値をそのまま記録
                vals = [c[1] for c in cells[1:]]
                for di, dur in enumerate(durations_short):
                    base = di * 4
                    if base >= len(vals):
                        break
                    rain = vals[base]
                    try:
                        rain_val = int(rain)
                    except ValueError:
                        continue
                    rows_out.append({
                        "name": station_name,
                        "region": current_region,
                        "duration": dur,
                        "value_mm": rain_val,
                        "source_file": page_source[i][0],
                        "source_page": page_source[i][1],
                    })

            # 対応する長時間側(続き)ページ: 直後のページで、見出しが「N時間」のみ
            # (「順位」列が無い)ものを1件だけ消費する
            if i + 1 < len(pages_all) and station_at_index:
                long_words = pages_all[i + 1].extract_words()
                has_rank_col = any(w["text"] == "順位" for w in long_words)
                long_dur_words = _topmost_group(long_words, lambda w: DURATION_TOKEN_RE.match(w["text"]))
                is_long_page = bool(long_words) and not has_rank_col and bool(long_dur_words)
                if is_long_page:
                    durations_long = [w["text"] for w in long_dur_words]
                    dur_header_top = long_dur_words[0]["top"]
                    subheader_words = [w for w in long_words if w["text"] in ("雨量", "年月日")]
                    subheader_top = (
                        min(w["top"] for w in subheader_words) if subheader_words else dur_header_top + 10
                    )
                    long_body_rows = _rows_from_words(
                        [w for w in long_words if w["top"] > subheader_top + 3], top_tol=3
                    )
                    for ri, (_, cells) in enumerate(long_body_rows):
                        if ri not in station_at_index:
                            continue
                        name = station_at_index[ri]
                        vals = [c[1] for c in cells]
                        for di, dur in enumerate(durations_long):
                            base = di * 4
                            if base >= len(vals):
                                break
                            rain = vals[base]
                            try:
                                rain_val = int(rain)
                            except ValueError:
                                continue
                            rows_out.append({
                                "name": name, "region": current_region,
                                "duration": dur, "value_mm": rain_val,
                                "source_file": page_source[i + 1][0],
                                "source_page": page_source[i + 1][1],
                            })
                    i += 1  # 長時間側ページも消費済み
            i += 1
    finally:
        for pdf in opened:
            pdf.close()

    print(f"nenmax_jikan_rank1 rows parsed: {len(rows_out)} from {[p.name for p in pdf_paths]}")
    return rows_out


def parse_kyokusen():
    """確率雨量強度曲線図(君島式 I=a/(t+b)^n の係数表)を解析する。
    ページ内では「観測所名がまとまって先に出現→Y a n bの表がその順で後に出現」という
    構成のため、観測所名を溜めておくキューとして扱い、表が出現するたびに1つずつ消費する。"""
    reader = PdfReader(KYOKUSEN_PDF)
    name_queue = []
    rows = []
    table_count = 0
    for page in reader.pages:
        text = page.extract_text()
        if not text:
            continue
        lines = [ln.strip() for ln in text.split("\n")]
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln == "Y a n b":
                table_count += 1
                name = name_queue.pop(0) if name_queue else None
                for j in range(1, 11):
                    if i + j >= len(lines):
                        break
                    parts = lines[i + j].split()
                    if len(parts) != 4:
                        continue
                    try:
                        y = int(parts[0])
                        a, n, b = float(parts[1]), float(parts[2]), float(parts[3])
                    except ValueError:
                        continue
                    rows.append({"name": name, "nengen": y, "a": a, "n": n, "b": b})
                i += 11
                continue
            if (
                KYOKUSEN_NAME_RE.match(ln)
                and ln not in KYOKUSEN_LABEL_WORDS
                and not ln.startswith("Y=")
            ):
                name_queue.append(ln)
            i += 1
    print(f"kyokusen tables: {table_count}, rows: {len(rows)}, names left unused in queue: {len(name_queue)}")
    return rows


def parse_kakuritsu_jikan():
    rows_out = []
    with pdfplumber.open(KAKURITSU_JIKAN_PDF) as pdf:
        pages = pdf.pages
        p = 1  # 0番目は表紙(セクション扉)なのでスキップ
        while p + 1 < len(pages):
            short_words = pages[p].extract_words()
            if not any(w["text"] == "地名" for w in short_words):
                p += 1
                continue
            long_words = pages[p + 1].extract_words()

            short_blocks = _extract_blocks(_rows_from_words(short_words))
            long_blocks = _extract_blocks_no_name(_rows_from_words(long_words), len(short_blocks))

            for bi, sb in enumerate(short_blocks):
                name = sb["name"]
                if not name:
                    continue
                s_dur, s_dist, s_data = _parse_block_data(sb["rows"])
                all_dur, all_dist = list(s_dur), list(s_dist)
                all_data = {k: list(v) for k, v in s_data.items()}
                if bi < len(long_blocks):
                    l_dur, l_dist, l_data = _parse_long_block_data(long_blocks[bi]["rows"])
                    all_dur += l_dur
                    all_dist += l_dist
                    for k, v in l_data.items():
                        all_data.setdefault(k, []).extend(v)

                for nengen, pairs in all_data.items():
                    for di, (r, _i) in enumerate(pairs):
                        if r == "-" or di >= len(all_dur):
                            continue
                        try:
                            r_val = float(r)
                        except ValueError:
                            continue
                        rows_out.append({
                            "name": name,
                            "duration": all_dur[di],
                            "nengen": nengen,
                            "value_mm": r_val,
                            "bunpu": all_dist[di] if di < len(all_dist) else None,
                        })
            p += 2
    print(f"kakuritsu_jikan rows parsed: {len(rows_out)}")
    return rows_out


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE stations (
            name TEXT, region TEXT, address TEXT, start_year TEXT,
            lat REAL, lon REAL, elevation INTEGER
        )
    """)
    rows = parse_stations()
    cur.executemany(
        "INSERT INTO stations VALUES (:name,:region,:address,:start_year,:lat,:lon,:elevation)",
        rows,
    )

    cur.execute("""
        CREATE TABLE kakuritsu_nichi (
            name TEXT, region TEXT, nengen INTEGER, value_mm INTEGER,
            bunpu TEXT, note TEXT
        )
    """)
    rows = parse_kakuritsu_nichi()
    cur.executemany(
        "INSERT INTO kakuritsu_nichi VALUES (:name,:region,:nengen,:value_mm,:bunpu,:note)",
        rows,
    )

    cur.execute("""
        CREATE TABLE kakuritsu_jikan (
            name TEXT, duration TEXT, nengen INTEGER, value_mm REAL, bunpu TEXT
        )
    """)
    rows = parse_kakuritsu_jikan()
    cur.executemany(
        "INSERT INTO kakuritsu_jikan VALUES (:name,:duration,:nengen,:value_mm,:bunpu)",
        rows,
    )

    cur.execute("""
        CREATE TABLE kyokusen_keisu (
            name TEXT, nengen INTEGER, a REAL, n REAL, b REAL
        )
    """)
    rows = parse_kyokusen()
    cur.executemany(
        "INSERT INTO kyokusen_keisu VALUES (:name,:nengen,:a,:n,:b)",
        rows,
    )

    cur.execute("""
        CREATE TABLE nenmax_nichi (
            edition TEXT, name TEXT, region TEXT, rank INTEGER,
            value_mm INTEGER, date TEXT
        )
    """)
    for edition, pdfs in [("14", NENMAX_NICHI_14_PDFS), ("15", NENMAX_NICHI_15_PDFS)]:
        rows = parse_nenmax_nichi(pdfs)
        for r in rows:
            r["edition"] = edition
        cur.executemany(
            "INSERT INTO nenmax_nichi VALUES (:edition,:name,:region,:rank,:value_mm,:date)",
            rows,
        )

    cur.execute("""
        CREATE TABLE nenmax_jikan_rank1 (
            edition TEXT, name TEXT, region TEXT, duration TEXT, value_mm INTEGER,
            source_file TEXT, source_page INTEGER
        )
    """)
    for edition, pdfs in [("14", NENMAX_JIKAN_14_PDFS), ("15", NENMAX_JIKAN_15_PDFS)]:
        rows = parse_nenmax_jikan_rank1(pdfs)
        for r in rows:
            r["edition"] = edition
        cur.executemany(
            "INSERT INTO nenmax_jikan_rank1 VALUES "
            "(:edition,:name,:region,:duration,:value_mm,:source_file,:source_page)",
            rows,
        )

    conn.commit()
    conn.close()
    print("done ->", DB_PATH)


if __name__ == "__main__":
    main()
