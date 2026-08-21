"""
年最大時間雨量順位表(第14編・第15編)を、観測所ごと・編ごとのシートに分けた
Excelファイルに変換する。rainfall.db/Webアプリとは独立したツール。

PDFは各観測所の順位1件ごとに5列(順位/雨量/年/月/日)×12時間区分という
非常に横長のレイアウトをA4縦に収めるため独特な構成になっており閲覧しにくい。
ここでは観測所単位でシートを分け、行=順位、列=時間区分(雨量・年月日)という
見やすい形に組み直す。

既知の制約: ごく一部の統計期間が長い観測所では、順位表が複数ページ組にまたがり、
継続ページの一部行が完全に空白(欠測)になることがある。その場合、短時間側ページと
長時間側(続き)ページの行対応がずれ、深い順位(概ね順位90位以降)のデータが
誤った年月日と組み合わさることがある。これを検出するため、順位が進むほど雨量が
単調に減少するはずという前提で、逆転が起きた時点以降を打ち切って出力する
(_truncate_non_monotonic)。影響は既知の範囲でおよそ全体の0.3%の行(145/2771観測所
×時間区分の組)に限られ、浅い順位(実務上よく使われる範囲)には影響しない。
"""
from collections import defaultdict

import build_db
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

DURATIONS = [
    "10分", "30分", "60分", "1時間", "2時間", "3時間",
    "4時間", "5時間", "6時間", "8時間", "12時間", "24時間",
]
OUT_PATH = build_db.OUT_DIR / "年最大時間雨量順位表.xlsx"


def _truncate_non_monotonic(rows):
    """順位が進んでも雨量は単調減少のはず、という前提が崩れた時点(=ページ跨ぎの
    行ずれが疑われる箇所)以降を切り捨てる。戻り値は(採用した行, 打ち切り箇所の一覧)。
    打ち切り箇所には、元PDFで確認する際の目印として直前の有効行の出典ページも添える。"""
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["name"], r["duration"])].append(r)
    out = []
    truncations = []
    for (name, duration), rs in by_key.items():
        rs.sort(key=lambda r: r["rank"])
        prev = None
        cut_at = None
        for idx, r in enumerate(rs):
            if prev is not None and r["value_mm"] > prev:
                cut_at = idx
                break
            out.append(r)
            prev = r["value_mm"]
        if cut_at is not None:
            last_valid = rs[cut_at - 1]
            first_dropped = rs[cut_at]
            truncations.append({
                "name": name,
                "duration": duration,
                "last_valid_rank": last_valid["rank"],
                "last_valid_source_file": last_valid["source_file"],
                "last_valid_source_page": last_valid["source_page"],
                "dropped_count": len(rs) - cut_at,
                "first_dropped_rank": first_dropped["rank"],
                "first_dropped_source_file": first_dropped["source_file"],
                "first_dropped_source_page": first_dropped["source_page"],
            })
    return out, truncations


def _sanitize_sheet_name(name):
    for ch in "\\/?*[]:":
        name = name.replace(ch, "")
    return name[:31] or "sheet"


def _unique_sheet_name(wb, base):
    name = _sanitize_sheet_name(base)
    if name not in wb.sheetnames:
        return name
    for i in range(2, 100):
        candidate = _sanitize_sheet_name(f"{base}_{i}")
        if candidate not in wb.sheetnames:
            return candidate
    raise RuntimeError(f"could not generate unique sheet name for {base!r}")


def _write_station_sheet(wb, edition_label, name, station_info, rows):
    ws = wb.create_sheet(_unique_sheet_name(wb, f"{name}_{edition_label}"))
    bold = Font(bold=True)
    center = Alignment(horizontal="center")

    ws.cell(row=1, column=1, value=f"{name}（第{edition_label}編）").font = bold
    if station_info:
        ws.cell(
            row=2, column=1,
            value=f"{station_info.get('region') or ''} {station_info.get('address') or ''}",
        )

    header_row, subheader_row = 4, 5
    ws.cell(row=header_row, column=1, value="順位").font = bold
    ws.merge_cells(start_row=header_row, start_column=1, end_row=subheader_row, end_column=1)

    by_duration_rank = defaultdict(dict)
    for r in rows:
        by_duration_rank[r["duration"]][r["rank"]] = r

    max_rank = max((r["rank"] for r in rows), default=0)

    for di, dur in enumerate(DURATIONS):
        col0 = 2 + di * 2
        ws.merge_cells(start_row=header_row, start_column=col0, end_row=header_row, end_column=col0 + 1)
        c = ws.cell(row=header_row, column=col0, value=dur)
        c.font = bold
        c.alignment = center
        ws.cell(row=subheader_row, column=col0, value="雨量(mm)").font = bold
        ws.cell(row=subheader_row, column=col0 + 1, value="年月日").font = bold

    for rank in range(1, max_rank + 1):
        row = subheader_row + rank
        ws.cell(row=row, column=1, value=rank)
        for di, dur in enumerate(DURATIONS):
            col0 = 2 + di * 2
            rec = by_duration_rank[dur].get(rank)
            if rec:
                ws.cell(row=row, column=col0, value=rec["value_mm"])
                ws.cell(row=row, column=col0 + 1, value=rec["date"])

    ws.column_dimensions[get_column_letter(1)].width = 6
    for di in range(len(DURATIONS)):
        col0 = 2 + di * 2
        ws.column_dimensions[get_column_letter(col0)].width = 9
        ws.column_dimensions[get_column_letter(col0 + 1)].width = 12
    ws.freeze_panes = ws.cell(row=subheader_row + 1, column=2).coordinate


def _write_truncation_report(wb, all_truncations):
    ws = wb.create_sheet("要確認(順位ずれ)", 0)
    bold = Font(bold=True)
    ws.cell(row=1, column=1, value=(
        "順位が進むほど雨量は単調減少するはず、という前提が崩れた箇所の一覧です。"
        "ページ跨ぎでの行ずれが疑われるため、それ以降の順位はこのExcelから除外しています。"
        "深い順位のデータが必要な場合は、右の出典ページで元PDFをご確認ください。"
    ))
    headers = [
        "編", "観測所", "時間区分", "採用した最終順位", "最終順位の出典",
        "除外した順位(先頭)", "除外した順位の出典", "除外した件数",
    ]
    for col, h in enumerate(headers, start=1):
        ws.cell(row=3, column=col, value=h).font = bold

    row = 4
    for t in all_truncations:
        ws.cell(row=row, column=1, value=t["edition"])
        ws.cell(row=row, column=2, value=t["name"])
        ws.cell(row=row, column=3, value=t["duration"])
        ws.cell(row=row, column=4, value=t["last_valid_rank"])
        ws.cell(row=row, column=5, value=f"{t['last_valid_source_file']} p.{t['last_valid_source_page']}")
        ws.cell(row=row, column=6, value=t["first_dropped_rank"])
        ws.cell(row=row, column=7, value=f"{t['first_dropped_source_file']} p.{t['first_dropped_source_page']}")
        ws.cell(row=row, column=8, value=t["dropped_count"])
        row += 1

    widths = [6, 14, 10, 14, 30, 16, 30, 12]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = "A4"


def main():
    station_rows = build_db.parse_stations()
    station_names = {r["name"] for r in station_rows}
    station_info_by_name = {r["name"]: r for r in station_rows}

    wb = Workbook()
    wb.remove(wb.active)

    all_truncations = []
    for edition_label, pdfs in [("14", build_db.NENMAX_JIKAN_14_PDFS), ("15", build_db.NENMAX_JIKAN_15_PDFS)]:
        rows = build_db.parse_nenmax_jikan_all(pdfs)
        rows = build_db._normalize_names(rows, station_names)
        rows, truncations = _truncate_non_monotonic(rows)
        for t in truncations:
            t["edition"] = edition_label
        all_truncations.extend(truncations)

        by_name = defaultdict(list)
        for r in rows:
            by_name[r["name"]].append(r)

        for name in sorted(by_name):
            _write_station_sheet(wb, edition_label, name, station_info_by_name.get(name), by_name[name])

    all_truncations.sort(key=lambda t: (t["name"], t["edition"], t["duration"]))
    _write_truncation_report(wb, all_truncations)

    wb.save(OUT_PATH)
    print(f"saved -> {OUT_PATH} ({len(wb.sheetnames)} sheets, {len(all_truncations)} truncation notes)")


if __name__ == "__main__":
    main()
