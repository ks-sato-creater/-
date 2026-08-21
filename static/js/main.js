const map = L.map("map").setView([43.06, 141.35], 7); // 北海道全体
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 18,
}).addTo(map);

let clickMarker = null;
let stationMarkers = [];

function clearStationMarkers() {
  stationMarkers.forEach((m) => map.removeLayer(m));
  stationMarkers = [];
}

async function searchNearest(lat, lon) {
  const res = await fetch(`/api/nearest?lat=${lat}&lon=${lon}&limit=10`);
  const stations = await res.json();
  renderNearestList(stations);

  clearStationMarkers();
  stations.forEach((s, idx) => {
    const marker = L.marker([s.lat, s.lon]).addTo(map);
    marker.bindPopup(`${s.name}（${s.distance_km}km）`);
    marker.on("click", () => showStationDetail(s.name));
    stationMarkers.push(marker);
  });
}

function renderNearestList(stations) {
  const ol = document.getElementById("nearest-ol");
  ol.innerHTML = "";
  stations.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `${s.name} <span class="dist">(${s.distance_km}km / ${s.region || ""})</span>`;
    li.addEventListener("click", () => showStationDetail(s.name));
    ol.appendChild(li);
  });
}

map.on("click", (e) => {
  const { lat, lng } = e.latlng;
  if (clickMarker) map.removeLayer(clickMarker);
  clickMarker = L.marker([lat, lng], { opacity: 0.6 }).addTo(map);
  searchNearest(lat, lng);
});

document.getElementById("search-btn").addEventListener("click", async () => {
  const q = document.getElementById("address-input").value.trim();
  const status = document.getElementById("search-status");
  status.textContent = "";
  if (!q) return;
  status.textContent = "検索中...";
  try {
    const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
    const results = await res.json();
    if (!results.length) {
      status.textContent = "住所が見つかりませんでした";
      return;
    }
    status.textContent = "";
    const { lat, lon } = results[0];
    map.setView([lat, lon], 12);
    if (clickMarker) map.removeLayer(clickMarker);
    clickMarker = L.marker([lat, lon], { opacity: 0.6 }).addTo(map);
    searchNearest(lat, lon);
  } catch (err) {
    status.textContent = "検索に失敗しました";
  }
});

document.getElementById("address-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("search-btn").click();
});

const NENGEN_LIST = [3, 5, 7, 10, 20, 30, 50, 70, 100, 200];
const NENMAX_JIKAN_DURATIONS = [
  "10分", "30分", "60分", "1時間", "2時間", "3時間", "4時間",
  "5時間", "6時間", "8時間", "12時間", "24時間",
];

let currentStationData = null;

async function showStationDetail(name) {
  const res = await fetch(`/api/station/${encodeURIComponent(name)}`);
  if (!res.ok) return;
  const data = await res.json();
  currentStationData = data;

  document.getElementById("detail-panel").classList.remove("hidden");
  document.getElementById("detail-name").textContent = data.station.name;
  document.getElementById("detail-basic").textContent =
    `${data.station.region || ""} ${data.station.address || ""}` +
    `（緯度${data.station.lat}, 経度${data.station.lon}, 標高${data.station.elevation ?? "-"}m）`;

  document.getElementById("hl-nichi").textContent =
    data.highlight.kakuritsu_nichi_100 != null ? data.highlight.kakuritsu_nichi_100 + " mm" : "データなし";
  document.getElementById("hl-jikan24").textContent =
    data.highlight.kakuritsu_jikan_24h_100 != null ? data.highlight.kakuritsu_jikan_24h_100 + " mm" : "データなし";
  document.getElementById("hl-nenmax-nichi").textContent =
    data.highlight.nenmax_nichi != null ? data.highlight.nenmax_nichi + " mm" : "データなし";
  document.getElementById("hl-nenmax-jikan24").textContent =
    data.highlight.nenmax_jikan_24h != null ? data.highlight.nenmax_jikan_24h + " mm" : "データなし";

  renderNichiTable(data.kakuritsu_nichi);
  renderJikanTable(data.kakuritsu_jikan);
  renderNenmaxNichiTable("15");
  renderNenmaxJikanTable("15");
  renderKyokusenCoefTable(data.kyokusen_keisu);
  drawCurve(name);
}

function renderKyokusenCoefTable(rows) {
  const table = document.getElementById("table-kyokusen-coef");
  const thead = "<tr><th>確率年</th><th>a</th><th>n</th><th>b</th></tr>";
  const tbody = rows && rows.length
    ? rows.map((r) => `<tr><td>${r.nengen}</td><td>${r.a}</td><td>${r.n}</td><td>${r.b}</td></tr>`).join("")
    : `<tr><td colspan="4">データがありません</td></tr>`;
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody;
}

const CALC_HOURS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0];

function renderKyokusenCalcTable(curves) {
  const table = document.getElementById("table-kyokusen-calc");
  const thead = `<tr><th>確率年＼時間(hr)</th>${CALC_HOURS.map((h) => `<th>${h}</th>`).join("")}</tr>`;
  const tbody = curves && curves.length
    ? curves.map((c) => {
        const byT = {};
        c.points.forEach((p) => { byT[p.t] = p.i; });
        const cells = CALC_HOURS.map((h) => `<td>${byT[h] ?? "-"}</td>`).join("");
        return `<tr><th>Y=${c.nengen}</th>${cells}</tr>`;
      }).join("")
    : `<tr><td colspan="${CALC_HOURS.length + 1}">データがありません</td></tr>`;
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody;
}

function renderNenmaxNichiTable(edition) {
  const table = document.getElementById("table-nenmax-nichi");
  const rows = (currentStationData && currentStationData.nenmax_nichi[edition]) || [];
  const thead = "<tr><th>順位</th><th>雨量(mm)</th><th>年月日</th></tr>";
  const tbody = rows.length
    ? rows.map((r) => `<tr><td>${r.rank}</td><td>${r.value_mm}</td><td>${r.date || "-"}</td></tr>`).join("")
    : `<tr><td colspan="3">第${edition}編のデータがありません</td></tr>`;
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody;
}

function renderNenmaxJikanTable(edition) {
  const table = document.getElementById("table-nenmax-jikan");
  const row = (currentStationData && currentStationData.nenmax_jikan[edition]) || {};
  const thead = `<tr>${NENMAX_JIKAN_DURATIONS.map((d) => `<th>${d}</th>`).join("")}</tr>`;
  const hasAny = Object.keys(row).length > 0;
  const tbody = hasAny
    ? `<tr>${NENMAX_JIKAN_DURATIONS.map((d) => `<td>${row[d] ? row[d].value_mm : "-"}</td>`).join("")}</tr>`
    : `<tr><td colspan="${NENMAX_JIKAN_DURATIONS.length}">第${edition}編のデータがありません</td></tr>`;
  const sourceRow = hasAny
    ? `<tr class="source-row">${NENMAX_JIKAN_DURATIONS.map((d) => {
        const cell = row[d];
        return `<td>${cell ? `${cell.source_file} p.${cell.source_page}` : "-"}</td>`;
      }).join("")}</tr>`
    : "";
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody + sourceRow;
}

document.querySelectorAll(".edition-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.target;
    const edition = btn.dataset.edition;
    document
      .querySelectorAll(`.edition-tab[data-target="${target}"]`)
      .forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    if (target === "nenmax-nichi") renderNenmaxNichiTable(edition);
    if (target === "nenmax-jikan") renderNenmaxJikanTable(edition);
  });
});

function renderNichiTable(rows) {
  const table = document.getElementById("table-nichi");
  const byNengen = {};
  rows.forEach((r) => (byNengen[r.nengen] = r.value_mm));
  const thead = `<tr><th>確率年</th>${NENGEN_LIST.map((y) => `<th>${y}</th>`).join("")}</tr>`;
  const tbody = `<tr><td>雨量(mm)</td>${NENGEN_LIST.map((y) => `<td>${byNengen[y] ?? "-"}</td>`).join("")}</tr>`;
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody;
}

function renderJikanTable(jikan) {
  const table = document.getElementById("table-jikan");
  const thead =
    `<tr><th>時間区分＼確率年</th>${jikan.nengen_list.map((y) => `<th>${y}年</th>`).join("")}</tr>`;
  const tbody = jikan.durations
    .map((d) => {
      const row = jikan.matrix[d] || {};
      const cells = jikan.nengen_list.map((y) => `<td>${row[y] ?? "-"}</td>`).join("");
      return `<tr><th>${d}</th>${cells}</tr>`;
    })
    .join("");
  table.querySelector("thead").innerHTML = thead;
  table.querySelector("tbody").innerHTML = tbody;
}

async function drawCurve(name) {
  const canvas = document.getElementById("curve-canvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const res = await fetch(`/api/curve/${encodeURIComponent(name)}`);
  if (!res.ok) {
    ctx.fillText("曲線データがありません", 20, 20);
    renderKyokusenCalcTable(null);
    return;
  }
  const data = await res.json();
  const curves = data.curves;
  renderKyokusenCalcTable(curves);

  const padL = 50, padB = 40, padT = 20, padR = 20;
  const w = canvas.width - padL - padR;
  const h = canvas.height - padT - padB;

  const minT = 0.5, maxT = 4.0;
  const inRange = (p) => p.t >= minT && p.t <= maxT;
  const allI = curves.flatMap((c) => [
    ...c.points.map((p) => p.i),
    ...(c.observed || []).filter(inRange).map((p) => p.i),
  ]);
  const maxI = Math.max(...allI, 10);

  function xOf(t) { return padL + ((t - minT) / (maxT - minT)) * w; }
  function yOf(i) { return padT + h - (i / maxI) * h; }

  // 軸
  ctx.strokeStyle = "#333";
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, padT + h); ctx.lineTo(padL + w, padT + h);
  ctx.stroke();
  ctx.fillStyle = "#333";
  ctx.font = "11px sans-serif";
  ctx.fillText("雨量強度 (mm/hr)", 4, 12);
  ctx.fillText("時間 (hr)", padL + w - 40, padT + h + 30);
  for (let t = 0.5; t <= 4.0; t += 0.5) {
    const x = xOf(t);
    ctx.fillText(t.toString(), x - 5, padT + h + 14);
  }
  for (let i = 0; i <= maxI; i += Math.ceil(maxI / 8)) {
    const y = yOf(i);
    ctx.fillText(i.toString(), padL - 30, y + 3);
  }

  const colors = ["#c0392b", "#d35400", "#e67e22", "#f39c12", "#27ae60",
                  "#16a085", "#2980b9", "#8e44ad", "#2c3e50", "#7f8c8d"];
  curves.forEach((c, idx) => {
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    c.points.forEach((p, i) => {
      const x = xOf(p.t), y = yOf(p.i);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // 実測値(確率時間雨量表)を□マーカーで重ね描き。曲線は広い時間帯の回帰式のため、
    // 短時間側では実測点とややずれることがある(元のPDF図も点と曲線を並記している)。
    ctx.fillStyle = "#fff";
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = 1;
    (c.observed || []).filter(inRange).forEach((p) => {
      const x = xOf(p.t), y = yOf(p.i);
      ctx.fillRect(x - 3, y - 3, 6, 6);
      ctx.strokeRect(x - 3, y - 3, 6, 6);
    });

    const last = c.points[c.points.length - 1];
    ctx.fillStyle = colors[idx % colors.length];
    ctx.fillText(`Y=${c.nengen}`, xOf(last.t) - 30, yOf(last.i) - 4);
  });
}
