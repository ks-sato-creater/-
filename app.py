"""
北海道大雨資料 観測所データ検索アプリ (Flask)

住所または地図クリックで指定した位置から近郊の雨量観測所を検索し、
確率日雨量・確率時間雨量・雨量強度曲線図(計算値)を表示する。
"""
import math
import sqlite3
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request

DB_PATH = Path(__file__).parent / "rainfall.db"
GSI_GEOCODE_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"

KAKURITSU_NENGEN = [3, 5, 7, 10, 20, 30, 50, 70, 100, 200]
JIKAN_DURATIONS = [
    "10分", "30分", "60分", "1時間", "2時間", "3時間", "4時間",
    "5時間", "6時間", "8時間", "12時間", "24時間",
]

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/nearest")
def api_nearest():
    lat = float(request.args.get("lat"))
    lon = float(request.args.get("lon"))
    limit = int(request.args.get("limit", 10))

    conn = get_db()
    stations = conn.execute(
        "SELECT name, region, address, lat, lon, elevation FROM stations"
    ).fetchall()
    conn.close()

    results = []
    for s in stations:
        dist = haversine_km(lat, lon, s["lat"], s["lon"])
        results.append({
            "name": s["name"], "region": s["region"], "address": s["address"],
            "lat": s["lat"], "lon": s["lon"], "elevation": s["elevation"],
            "distance_km": round(dist, 2),
        })
    results.sort(key=lambda r: r["distance_km"])
    return jsonify(results[:limit])


@app.route("/api/station/<name>")
def api_station(name):
    conn = get_db()
    station = conn.execute(
        "SELECT name, region, address, start_year, lat, lon, elevation "
        "FROM stations WHERE name = ?", (name,)
    ).fetchone()
    if station is None:
        conn.close()
        return jsonify({"error": "station not found"}), 404

    nichi_rows = conn.execute(
        "SELECT nengen, value_mm, bunpu, note FROM kakuritsu_nichi "
        "WHERE name = ? ORDER BY nengen", (name,)
    ).fetchall()
    jikan_rows = conn.execute(
        "SELECT duration, nengen, value_mm, bunpu FROM kakuritsu_jikan "
        "WHERE name = ?", (name,)
    ).fetchall()
    kyokusen_rows = conn.execute(
        "SELECT nengen, a, n, b FROM kyokusen_keisu WHERE name = ? ORDER BY nengen DESC",
        (name,),
    ).fetchall()

    # 年最大日雨量順位表(第14編/第15編)。表示が長くなりすぎないよう上位20位まで。
    nenmax_nichi_rows = conn.execute(
        "SELECT edition, rank, value_mm, date FROM nenmax_nichi "
        "WHERE name = ? AND rank <= 20 ORDER BY edition, rank",
        (name,),
    ).fetchall()
    # 年最大時間雨量順位表の順位1(過去最大値)。まれに生じる誤抽出の重複行は
    # MAXで吸収する(正しい値の方が常に大きいことを確認済み)。source_file/source_page は
    # SQLiteの仕様によりMAX(value_mm)と同じ行の値が採用される。
    nenmax_jikan_rows = conn.execute(
        "SELECT edition, duration, MAX(value_mm) AS value_mm, source_file, source_page "
        "FROM nenmax_jikan_rank1 WHERE name = ? GROUP BY edition, duration",
        (name,),
    ).fetchall()
    conn.close()

    # 確率時間雨量を「時間区分×確率年」の行列に整形
    jikan_matrix = {d: {} for d in JIKAN_DURATIONS}
    for r in jikan_rows:
        if r["duration"] in jikan_matrix:
            jikan_matrix[r["duration"]][r["nengen"]] = r["value_mm"]

    nenmax_nichi_by_edition = {"14": [], "15": []}
    for r in nenmax_nichi_rows:
        nenmax_nichi_by_edition.setdefault(r["edition"], []).append(
            {"rank": r["rank"], "value_mm": r["value_mm"], "date": r["date"]}
        )
    nenmax_jikan_by_edition = {"14": {}, "15": {}}
    for r in nenmax_jikan_rows:
        nenmax_jikan_by_edition.setdefault(r["edition"], {})[r["duration"]] = {
            "value_mm": r["value_mm"],
            "source_file": r["source_file"],
            "source_page": r["source_page"],
        }

    def _first_available(d, key):
        for ed in ("15", "14"):
            entry = d.get(ed, {}).get(key) if isinstance(d.get(ed), dict) else None
            v = entry["value_mm"] if entry else None
            if v is not None:
                return v
        return None

    nenmax_nichi_rank1_value = next(
        (
            nenmax_nichi_by_edition[ed][0]["value_mm"]
            for ed in ("15", "14")
            if nenmax_nichi_by_edition.get(ed)
        ),
        None,
    )

    return jsonify({
        "station": dict(station),
        "kakuritsu_nichi": [dict(r) for r in nichi_rows],
        "kakuritsu_jikan": {
            "durations": JIKAN_DURATIONS,
            "nengen_list": KAKURITSU_NENGEN,
            "matrix": jikan_matrix,
        },
        "kyokusen_keisu": [dict(r) for r in kyokusen_rows],
        "nenmax_nichi": nenmax_nichi_by_edition,
        "nenmax_jikan": nenmax_jikan_by_edition,
        "highlight": {
            "kakuritsu_nichi_100": next((r["value_mm"] for r in nichi_rows if r["nengen"] == 100), None),
            "kakuritsu_jikan_24h_100": jikan_matrix.get("24時間", {}).get(100),
            "nenmax_nichi": nenmax_nichi_rank1_value,
            "nenmax_jikan_24h": _first_available(nenmax_jikan_by_edition, "24時間"),
        },
    })


@app.route("/api/curve/<name>")
def api_curve(name):
    conn = get_db()
    coeffs = conn.execute(
        "SELECT nengen, a, n, b FROM kyokusen_keisu WHERE name = ? ORDER BY nengen DESC",
        (name,),
    ).fetchall()
    conn.close()
    if not coeffs:
        return jsonify({"error": "no curve data for this station"}), 404

    t_values = [round(0.5 + 0.1 * i, 2) for i in range(36)]  # 0.5h〜4.0h
    curves = []
    for c in coeffs:
        a, n, b = c["a"], c["n"], c["b"]
        points = []
        for t in t_values:
            denom = t + b
            if denom <= 0:
                continue
            intensity = a / (denom ** n)
            points.append({"t": t, "i": round(intensity, 2)})
        curves.append({"nengen": c["nengen"], "points": points})
    return jsonify({"name": name, "curves": curves})


@app.route("/api/curve_image/<name>")
def api_curve_image(name):
    conn = get_db()
    row = conn.execute(
        "SELECT image FROM kyokusen_images WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "no curve image for this station"}), 404
    return Response(row["image"], mimetype="image/png")


@app.route("/api/geocode")
def api_geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        resp = requests.get(GSI_GEOCODE_URL, params={"q": q}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

    results = [
        {
            "title": item["properties"]["title"],
            "lon": item["geometry"]["coordinates"][0],
            "lat": item["geometry"]["coordinates"][1],
        }
        for item in data
    ]
    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
