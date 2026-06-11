/* Roam frontend: pick a point + mode + limit, shade reachability, overlay routes. */

const map = L.map("map").setView([42.36, -71.06], 13);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}).addTo(map);

const state = { start: null, mode: "walk", busy: false, jobId: null };
let startMarker = null;
let resultLayers = L.layerGroup().addTo(map);

const $ = (id) => document.getElementById(id);
const status = (msg, isError = false) => {
  $("status").textContent = msg;
  $("status").className = isError ? "error" : "";
};

// The panel is user-resizable; tell Leaflet when its box changes.
new ResizeObserver(() => map.invalidateSize()).observe($("panel"));

/* ---- start point ---- */
function setStart(lat, lng, label) {
  state.start = { lat, lng };
  if (startMarker) startMarker.remove();
  startMarker = L.marker([lat, lng], { draggable: true }).addTo(map);
  startMarker.on("dragend", () => {
    const p = startMarker.getLatLng();
    state.start = { lat: p.lat, lng: p.lng };
  });
  $("go").disabled = false;
  $("save-place").disabled = false;
  if (label) status(`Start: ${label.split(",").slice(0, 2).join(",")}`);
}
map.on("click", (e) => setStart(e.latlng.lat, e.latlng.lng));

/* ---- saved places (localStorage) ---- */
const loadPlaces = () => JSON.parse(localStorage.getItem("roam.places") || "[]");
const storePlaces = (p) => localStorage.setItem("roam.places", JSON.stringify(p));

function renderPlaces() {
  const places = loadPlaces();
  const box = $("places");
  box.innerHTML = "";
  if (!places.length) {
    box.innerHTML = '<span class="hint">None yet — set a start point, then save it.</span>';
    return;
  }
  places.forEach((pl, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    const name = document.createElement("span");
    name.textContent = pl.name;
    chip.appendChild(name);
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.title = `Remove ${pl.name}`;
    x.onclick = (e) => {
      e.stopPropagation();
      if (confirm(`Remove saved place "${pl.name}"?`)) {
        const ps = loadPlaces();
        ps.splice(i, 1);
        storePlaces(ps);
        renderPlaces();
      }
    };
    chip.appendChild(x);
    chip.onclick = () => {
      map.setView([pl.lat, pl.lng], 14);
      setStart(pl.lat, pl.lng, pl.name);
    };
    box.appendChild(chip);
  });
}
$("save-place").addEventListener("click", () => {
  if (!state.start) return;
  const name = prompt("Name this place:", loadPlaces().length ? "" : "Home");
  if (!name) return;
  const places = loadPlaces().filter((p) => p.name !== name);
  places.push({ name, lat: state.start.lat, lng: state.start.lng });
  storePlaces(places);
  renderPlaces();
});
renderPlaces();

/* ---- geocoding ---- */
let searchTimer = null;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("search").value.trim();
  if (q.length < 3) { $("search-results").innerHTML = ""; return; }
  searchTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
      if (!r.ok) throw new Error((await r.json()).detail);
      const { results } = await r.json();
      $("search-results").innerHTML = "";
      results.forEach((res) => {
        const li = document.createElement("li");
        li.textContent = res.label;
        li.onclick = () => {
          $("search-results").innerHTML = "";
          $("search").value = res.label;
          map.setView([res.lat, res.lng], 14);
          setStart(res.lat, res.lng, res.label);
        };
        $("search-results").appendChild(li);
      });
    } catch (err) { status(`Search failed: ${err.message}`, true); }
  }, 350);
});

/* ---- modes ---- */
async function loadModes() {
  const r = await fetch("/api/modes");
  const data = await r.json();
  const box = $("modes");
  data.modes.forEach((m) => {
    const b = document.createElement("button");
    b.textContent = m.label;
    b.dataset.key = m.key;
    if (m.key === state.mode) b.classList.add("active");
    b.onclick = () => {
      state.mode = m.key;
      box.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
    };
    box.appendChild(b);
  });
  $("planned-modes").textContent =
    "Coming later: " + data.planned.map((p) => p.label).join(", ");
}
loadModes();

/* ---- units (km/mi) ---- */
const KM_PER_MI = 1.609344;
// Default to miles for US locales; the API itself is always metric.
$("units").value = (navigator.language || "").toLowerCase().endsWith("-us") ? "mi" : "km";
const usingMiles = () => $("units").value === "mi";
const fmtDist = (meters) =>
  usingMiles() ? `${(meters / 1000 / KM_PER_MI).toFixed(1)} mi` : `${(meters / 1000).toFixed(1)} km`;

/* ---- limit controls ---- */
function syncLimitControls() {
  const isTime = $("limit-type").value === "time";
  $("limit-slider").max = isTime ? 120 : usingMiles() ? 30 : 50;
  $("limit-value").value = isTime ? 15 : usingMiles() ? 2 : 3;
  $("limit-slider").value = $("limit-value").value;
}
$("limit-slider").addEventListener("input", () => { $("limit-value").value = $("limit-slider").value; });
$("limit-value").addEventListener("input", () => { $("limit-slider").value = $("limit-value").value; });
$("limit-type").addEventListener("change", syncLimitControls);
$("units").addEventListener("change", () => {
  if ($("limit-type").value === "distance") {
    // Convert the current value so 5 km becomes ~3.1 mi rather than 5 mi.
    const v = Number($("limit-value").value);
    const converted = usingMiles() ? v / KM_PER_MI : v * KM_PER_MI;
    $("limit-slider").max = usingMiles() ? 30 : 50;
    $("limit-value").value = Math.round(converted * 2) / 2 || 1;
    $("limit-slider").value = $("limit-value").value;
  }
});

/* ---- rendering ---- */
const ROUTE_COLORS = ["#e74c3c", "#9b59b6", "#e67e22", "#1abc9c", "#f1c40f", "#2ecc71"];

function fmtRoute(rt) {
  const dist = fmtDist(rt.length_m);
  if (rt.unit === "s") {
    const min = Math.round(rt.cost / 60);
    return `${rt.kind === "loop" ? "Loop" : "Out & back"} ${rt.bearing} — ${min} min, ${dist}`;
  }
  return `${rt.kind === "loop" ? "Loop" : "Out & back"} ${rt.bearing} — ${dist}`;
}

function render(data) {
  resultLayers.clearLayers();
  $("routes").innerHTML = "";

  const poly = L.geoJSON(
    { type: "Feature", geometry: data.polygon },
    { style: { color: "#3b82f6", weight: 2, fillColor: "#3b82f6", fillOpacity: 0.25 } }
  ).addTo(resultLayers);
  map.fitBounds(poly.getBounds(), { padding: [30, 30] });

  if (data.overlays.tree) {
    L.geoJSON(
      { type: "Feature", geometry: data.overlays.tree },
      { style: { color: "#1d4ed8", weight: 1, opacity: 0.45 } }
    ).addTo(resultLayers);
  }

  let colorIdx = 0;
  for (const key of ["loops", "out_and_back"]) {
    (data.overlays[key] || []).forEach((rt) => {
      const color = ROUTE_COLORS[colorIdx++ % ROUTE_COLORS.length];
      const line = L.polyline(rt.coords.map(([lng, lat]) => [lat, lng]), {
        color, weight: 4, opacity: 0.85,
      }).bindPopup(fmtRoute(rt)).addTo(resultLayers);
      const item = document.createElement("div");
      item.className = "route-item";
      item.innerHTML = `<div class="swatch" style="background:${color}"></div>
        <div style="flex:1">${fmtRoute(rt)}</div>`;
      item.onclick = () => { map.fitBounds(line.getBounds(), { padding: [40, 40] }); line.openPopup(); };
      $("routes").appendChild(item);
    });
  }

  let msg = `Done (${data.provider}).`;
  if (data.stats.reached_nodes) msg += ` ${data.stats.reached_nodes} street corners reachable.`;
  if (data.warning) msg += ` ${data.warning}`;
  status(msg);
}

/* ---- compute via background jobs (progress + cancel) ---- */
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

function setProgressVisible(on) {
  $("progress").hidden = !on;
  if (!on) { $("bar-fill").style.width = "0%"; }
}

function buildBody(forceLocal) {
  const isTime = $("limit-type").value === "time";
  const overlays = [];
  if ($("ov-loops").checked) overlays.push("loops");
  if ($("ov-oab").checked) overlays.push("out_and_back");
  if ($("ov-tree").checked) overlays.push("tree");
  const distVal = Number($("limit-value").value) * (usingMiles() ? KM_PER_MI : 1);
  return {
    lat: state.start.lat, lng: state.start.lng, mode: state.mode,
    limit_minutes: isTime ? Number($("limit-value").value) : null,
    limit_km: isTime ? null : distVal,
    overlays, provider: "auto", force_local: forceLocal,
  };
}

function confirmBigArea(error) {
  const radius = usingMiles()
    ? `${(error.radius_km / KM_PER_MI).toFixed(1)} mi` : `${error.radius_km} km`;
  const extra = error.hosted_available
    ? "" : "\n(Tip: set ORS_API_KEY to offload big areas to a hosted provider.)";
  return confirm(
    `This needs the street network for a ~${radius} radius area. ` +
    `The first download for an area this size can take a few minutes, ` +
    `but it's cached — repeat queries are fast. You can cancel anytime.` +
    `\n\nDownload and compute?${extra}`
  );
}

async function compute(forceLocal = false) {
  if (!state.start || state.busy) return;
  state.busy = true;
  $("go").disabled = true;
  status("");
  saveSettings();

  try {
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildBody(forceLocal)),
    });
    if (!r.ok) {
      const err = await r.json();
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    state.jobId = (await r.json()).job_id;
    setProgressVisible(true);

    while (true) {
      await sleep(500);
      const s = await (await fetch(`/api/jobs/${state.jobId}`)).json();
      $("bar-fill").style.width = `${Math.round(s.progress * 100)}%`;
      $("progress-stage").textContent = s.stage;
      $("progress-time").textContent = `${Math.round(s.elapsed_s)}s`;

      if (s.status === "done") {
        setProgressVisible(false);
        render(s.result);
        return;
      }
      if (s.status === "cancelled") {
        setProgressVisible(false);
        status("Cancelled. (Any download already in flight still finishes and is cached, so retrying later is faster.)");
        return;
      }
      if (s.status === "error") {
        setProgressVisible(false);
        if (s.error && s.error.reason === "area_too_large") {
          if (confirmBigArea(s.error)) {
            state.busy = false;
            return compute(true);
          }
          status("Skipped — try a smaller limit, or confirm next time to proceed.");
          return;
        }
        throw new Error(s.error ? s.error.message : "computation failed");
      }
    }
  } catch (err) {
    setProgressVisible(false);
    status(`Failed: ${err.message}`, true);
  } finally {
    state.busy = false;
    state.jobId = null;
    $("go").disabled = !state.start;
  }
}
$("go").addEventListener("click", () => compute(false));
$("cancel").addEventListener("click", () => {
  if (state.jobId) fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
});

/* ---- remember last settings ---- */
function saveSettings() {
  localStorage.setItem("roam.settings", JSON.stringify({
    mode: state.mode,
    limitType: $("limit-type").value,
    limitValue: $("limit-value").value,
    units: $("units").value,
    loops: $("ov-loops").checked,
    oab: $("ov-oab").checked,
    tree: $("ov-tree").checked,
  }));
}
(function restoreSettings() {
  const s = JSON.parse(localStorage.getItem("roam.settings") || "null");
  if (!s) return;
  state.mode = s.mode || "walk";
  $("limit-type").value = s.limitType || "time";
  $("units").value = s.units || $("units").value;
  $("limit-slider").max = $("limit-type").value === "time" ? 120 : usingMiles() ? 30 : 50;
  $("limit-value").value = s.limitValue || 15;
  $("limit-slider").value = $("limit-value").value;
  $("ov-loops").checked = !!s.loops;
  $("ov-oab").checked = !!s.oab;
  $("ov-tree").checked = !!s.tree;
})();
