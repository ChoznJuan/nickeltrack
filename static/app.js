// NickelTrack — single-page client logic
// All state lives in localStorage; no server-side persistence in v1.

const STORAGE_KEY = "nickeltrack.day.v1";
const PROFILE_KEY = "nickeltrack.profile.v1";

// ─────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────
let config = null;  // loaded once on page load
let day = loadDay();
let profile = localStorage.getItem(PROFILE_KEY) || "adult";

// ─────────────────────────────────────────────────────────────
// Online/offline indicator
// (Service worker handles the actual data; this just shows the banner)
// ─────────────────────────────────────────────────────────────
function updateOnlineStatus() {
    const banner = document.getElementById("offline-banner");
    if (!banner) return;
    if (navigator.onLine) {
        banner.hidden = true;
    } else {
        const last = localStorage.getItem("nickel…ync") || "never";
        document.getElementById("last-sync").textContent = last;
        banner.hidden = false;
    }
}
window.addEventListener("online", updateOnlineStatus);
window.addEventListener("offline", updateOnlineStatus);
// Record last successful sync
window.addEventListener("online", () => {
    localStorage.setItem("nickel…ync", new Date().toLocaleTimeString());
});
updateOnlineStatus();

// ─────────────────────────────────────────────────────────────
// Storage
// ─────────────────────────────────────────────────────────────
function loadDay() {
    try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY)) || { items: [] };
    } catch {
        return { items: [] };
    }
}

function saveDay() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(day));
}

// ─────────────────────────────────────────────────────────────
// API helpers
// ─────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
    const r = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...opts,
    });
    if (!r.ok) throw new Error(`API ${path} → ${r.status}`);
    return r.json();
}

// ─────────────────────────────────────────────────────────────
// Search
// ─────────────────────────────────────────────────────────────
const searchInput = document.getElementById("search-input");
const categoryFilter = document.getElementById("category-filter");
const resultsDiv = document.getElementById("search-results");
let searchAbort = null;
let searchDebounce = null;

async function runSearch() {
    const q = searchInput.value.trim();
    const cat = categoryFilter.value;
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (cat) params.set("category", cat);
    params.set("limit", "30");

    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();

    resultsDiv.innerHTML = `<p class="empty">Searching…</p>`;
    try {
        const data = await api(`/api/search?${params}`, { signal: searchAbort.signal });
        renderResults(data.results || []);
    } catch (e) {
        if (e.name !== "AbortError") {
            resultsDiv.innerHTML = `<p class="empty">Search failed: ${e.message}</p>`;
        }
    }
}

function renderResults(rows) {
    if (!rows.length) {
        resultsDiv.innerHTML = `<p class="empty">No foods found.</p>`;
        return;
    }
    resultsDiv.innerHTML = rows.map(r => {
        const pts = r.avoid ? "AVOID" : (r.points ?? "—") + " pt";
        const ug = r.nickel_ug_per_serving != null ? `${r.nickel_ug_per_serving} µg` : "—";
        const serving = r.serving || "per serving";
        return `
            <div class="food-card ${r.avoid ? "avoid" : ""}">
                <div class="food-info">
                    <span class="cat-badge ${r.category}">${r.category}</span>
                    <span class="food-name">${escapeHtml(r.name)}</span>
                    <div class="food-meta">${ug} · ${pts} · ${escapeHtml(serving)}</div>
                </div>
                <button data-food-id="${r.id}" data-food-name="${escapeHtml(r.name)}" class="add-btn">
                    ${r.avoid ? "Mark eaten" : "+ Add"}
                </button>
            </div>
        `;
    }).join("");

    // Wire add buttons
    resultsDiv.querySelectorAll(".add-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const id = parseInt(btn.dataset.foodId, 10);
            addToDay(id);
        });
    });
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(runSearch, 200);
});
categoryFilter.addEventListener("change", runSearch);

// ─────────────────────────────────────────────────────────────
// Day builder
// ─────────────────────────────────────────────────────────────
const dayDiv = document.getElementById("day-items");
const profileSelect = document.getElementById("profile-select");
const clearBtn = document.getElementById("clear-day");
const totalUgEl = document.getElementById("total-ug");
const totalPtsEl = document.getElementById("total-pts");
const targetUgEl = document.getElementById("target-ug");
const targetPtsEl = document.getElementById("target-pts");
const progressUg = document.getElementById("progress-ug");
const progressPts = document.getElementById("progress-pts");
const warningEl = document.getElementById("warning");

profileSelect.value = profile;

function addToDay(foodId) {
    // If already present, bump qty by 1
    const existing = day.items.find(i => i.food_id === foodId);
    if (existing) {
        existing.servings = +(existing.servings + 1).toFixed(2);
    } else {
        day.items.push({ food_id: foodId, servings: 1.0 });
    }
    saveDay();
    renderDay();
}

function updateQty(foodId, qty) {
    const it = day.items.find(i => i.food_id === foodId);
    if (it) {
        it.servings = Math.max(0.01, +qty);
        saveDay();
        renderDay();
    }
}

function removeFromDay(foodId) {
    day.items = day.items.filter(i => i.food_id !== foodId);
    saveDay();
    renderDay();
}

clearBtn.addEventListener("click", () => {
    if (confirm("Clear all foods from today's day?")) {
        day = { items: [] };
        saveDay();
        renderDay();
    }
});

profileSelect.addEventListener("change", () => {
    profile = profileSelect.value;
    localStorage.setItem(PROFILE_KEY, profile);
    renderDay();
});

async function renderDay() {
    if (!day.items.length) {
        dayDiv.innerHTML = `<p class="empty">No foods added yet. Search above and click "Add to day".</p>`;
        totalUgEl.textContent = "0 µg";
        totalPtsEl.textContent = "0";
        progressUg.style.width = "0%";
        progressPts.style.width = "0%";
        warningEl.textContent = "";
        return;
    }

    try {
        const totals = await api("/api/totals", {
            method: "POST",
            body: JSON.stringify({ items: day.items, profile }),
        });

        // Render breakdown
        dayDiv.innerHTML = totals.items.map(it => {
            let ptsLabel;
            if (it.avoid) ptsLabel = "AVOID";
            else if (it.points === 0) ptsLabel = "0";
            else ptsLabel = it.points.toFixed(1);
            return `
                <div class="day-item ${it.avoid ? "avoid" : ""}">
                    <div>
                        <span class="cat-badge ${it.category}">${it.category}</span>
                        <strong>${escapeHtml(it.name)}</strong>
                        <span class="food-meta">${it.ug} µg · ${ptsLabel} pts</span>
                    </div>
                    <div>
                        <span class="qty">×
                            <input type="number" min="0.1" step="0.1" value="${it.servings}"
                                   data-food-id="${it.food_id}" class="qty-input">
                        </span>
                        <button class="remove" data-food-id="${it.food_id}">×</button>
                    </div>
                </div>
            `;
        }).join("");

        // Wire qty + remove
        dayDiv.querySelectorAll(".qty-input").forEach(inp => {
            inp.addEventListener("change", () => {
                const id = parseInt(inp.dataset.foodId, 10);
                updateQty(id, inp.value);
            });
        });
        dayDiv.querySelectorAll(".remove").forEach(btn => {
            btn.addEventListener("click", () => {
                const id = parseInt(btn.dataset.foodId, 10);
                removeFromDay(id);
            });
        });

        // Render totals
        totalUgEl.textContent = `${totals.ug.toFixed(1)} µg`;
        totalPtsEl.textContent = totals.points.toFixed(1);
        targetUgEl.textContent = `/ ${totals.target_ug} µg`;
        targetPtsEl.textContent = `/ ${totals.target_pts}`;

        const ugPct = Math.min(100, (totals.ug / totals.target_ug) * 100);
        const ptsPct = Math.min(100, (totals.points / totals.target_pts) * 100);
        progressUg.style.width = ugPct + "%";
        progressPts.style.width = ptsPct + "%";
        progressUg.classList.toggle("over", totals.ug > totals.target_ug);
        progressPts.classList.toggle("over", totals.points > totals.target_pts);

        const avoidItems = totals.items.filter(i => i.avoid && i.servings > 0);
        const msgs = [];
        if (totals.ug > totals.target_ug) {
            msgs.push(`⚠️ Over daily nickel target by ${(totals.ug - totals.target_ug).toFixed(1)} µg.`);
        }
        if (totals.points > totals.target_pts) {
            msgs.push(`⚠️ Over daily points target by ${(totals.points - totals.target_pts).toFixed(1)} pts.`);
        }
        if (avoidItems.length) {
            const avoidTotal = avoidItems.reduce((a, i) => a + i.ug, 0);
            msgs.push(`⚠️ Contains foods above 100 µg/serving (${avoidTotal.toFixed(1)} µg total): ${avoidItems.map(i => i.name).join(", ")}. Consider smaller portions.`);
        }
        warningEl.innerHTML = msgs.join("<br>");
    } catch (e) {
        dayDiv.innerHTML = `<p class="empty">Failed to load totals: ${e.message}</p>`;
    }
}

// ─────────────────────────────────────────────────────────────
// v2: Scanner (html5-qrcode)
// ─────────────────────────────────────────────────────────────
const scanBtn = document.getElementById("scan-btn");
const scanViewport = document.getElementById("scan-viewport");
const scanStatus = document.getElementById("scan-status");
const scanStopBtn = document.getElementById("scan-stop");
const scanResultDiv = document.getElementById("scan-result");

let html5Qr = null;  // Html5Qrcode instance (lazily created on first scan)
let scanning = false;

async function startScanner() {
    if (scanning) return;
    if (typeof Html5Qrcode === "undefined") {
        scanStatus.textContent = "Scanner library not loaded. Check your network.";
        return;
    }
    scanBtn.disabled = true;
    scanViewport.hidden = false;
    scanResultDiv.innerHTML = "";
    scanStatus.textContent = "Starting camera…";
    if (!html5Qr) html5Qr = new Html5Qrcode("scan-reader");

    // Use the rear camera if available, fall back to any camera
    const config = {
        fps: 10,
        qrbox: { width: 280, height: 180 },
        aspectRatio: 1.777778,  // 16:9
        // Focus on formats relevant to packaged food: QR + 1D barcodes
        formatsToSupport: [
            Html5QrcodeSupportedFormats.QR_CODE,
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.UPC_E,
            Html5QrcodeSupportedFormats.CODE_128,
            Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.ITF,
        ],
    };
    try {
        await html5Qr.start(
            { facingMode: "environment" },
            config,
            onScanSuccess,
            () => {}  // ignore per-frame failures (decoding in progress)
        );
        scanning = true;
        scanStatus.textContent = "Point camera at a barcode or QR code…";
    } catch (err) {
        // Surface the underlying html5-qrcode error. Two common ones:
        //   "NotAllowedError"  → user denied the permission prompt
        //   "NotReadableError" or "NotSupportedError" →
        //       usually means the browser blocked camera access because the
        //       page is on an insecure origin (http://192.168.x.x:5100).
        //       Android Chrome requires HTTPS (or localhost) for getUserMedia.
        //       Workaround for dev: chrome://flags/#unsafely-treat-insecure-origin-as-secure
        //       Real fix: tunnel the app via cloudflared.
        const msg = (err && err.message) || String(err);
        let hint = "You may need to grant camera permission.";
        if (/secure|insecure|https|origin/i.test(msg) || err?.name === "NotSupportedError") {
            hint = "This page is served over plain HTTP, and Android Chrome blocks camera access on insecure origins. Either (a) open chrome://flags/#unsafely-treat-insecure-origin-as-secure and add this URL, or (b) expose the app over HTTPS via a cloudflared tunnel.";
        } else if (err?.name === "NotAllowedError") {
            hint = "You denied camera permission. Tap the camera icon in the URL bar to allow it, then try again.";
        } else if (err?.name === "NotFoundError") {
            hint = "No camera found on this device.";
        }
        scanStatus.textContent = `Camera failed: ${msg}. ${hint}`;
        scanBtn.disabled = false;
    }
}

async function stopScanner() {
    if (!scanning || !html5Qr) return;
    try {
        await html5Qr.stop();
        await html5Qr.clear();
    } catch (_) {}
    scanning = false;
    scanViewport.hidden = true;
    scanBtn.disabled = false;
    scanStatus.textContent = "";
}

async function onScanSuccess(decodedText) {
    // Stop the scanner immediately so we don't keep scanning
    await stopScanner();
    scanResultDiv.innerHTML = `<p class="empty">Looking up <code>${escapeHtml(decodedText)}</code>…</p>`;
    try {
        const food = await api(`/api/lookup?barcode=${encodeURIComponent(decodedText)}`);
        renderScanResult(food);
    } catch (e) {
        if (e.message.includes("404")) {
            renderScanResult({ source_origin: "not_found", barcode: decodedText, message: "Not in our database or Open Food Facts." });
        } else {
            scanResultDiv.innerHTML = `<p class="empty">Lookup failed: ${escapeHtml(e.message)}</p>`;
        }
    }
}

function renderScanResult(food) {
    if (food.source_origin === "not_found") {
        scanResultDiv.innerHTML = `
            <div class="scan-result-card notfound">
                <h3>Not in database</h3>
                <div class="meta">Barcode: <code>${escapeHtml(food.barcode || "")}</code></div>
                <p>${escapeHtml(food.message || "This product isn't in our database or Open Food Facts.")}</p>
            </div>
        `;
        return;
    }
    const isEstimate = food.source_origin === "off" && food.estimated;
    const pts = food.avoid ? "AVOID" : (food.points ?? "—") + " pt";
    const ug = food.nickel_ug_per_serving != null ? `${food.nickel_ug_per_serving} µg` : "—";
    const serving = food.serving || "per serving";
    const sourceLabel = food.source_origin === "local" ? "In our database" : "From Open Food Facts (estimate)";
    const sourceIcon = food.source_origin === "local" ? "✅" : "🌐";

    scanResultDiv.innerHTML = `
        <div class="scan-result-card ${isEstimate ? "estimate" : ""}">
            <h3>
                <span class="cat-badge ${food.category}">${escapeHtml(food.category || "?")}</span>
                ${escapeHtml(food.name)}
                ${isEstimate ? '<span class="badge-estimate">⚠️ estimate</span>' : ""}
            </h3>
            <div class="meta">${sourceIcon} ${sourceLabel} · ${ug} · ${pts} · ${escapeHtml(serving)}</div>
            ${isEstimate ? `<div class="meta">Nickel value is a category-based estimate, not a measured value. The exact value for this specific product may differ.</div>` : ""}
            ${food.ingredients ? `<details><summary>Ingredients</summary><div class="meta">${escapeHtml(food.ingredients).slice(0, 600)}${food.ingredients.length > 600 ? "…" : ""}</div></details>` : ""}
            <div class="actions">
                <button class="add-btn" data-food-id="${food.id}">${food.avoid ? "Mark eaten" : "+ Add to day"}</button>
                <button id="scan-again" type="button" class="secondary">Scan another</button>
            </div>
        </div>
    `;
    scanResultDiv.querySelector(".add-btn").addEventListener("click", () => addToDay(food.id));
    scanResultDiv.querySelector("#scan-again").addEventListener("click", () => {
        scanResultDiv.innerHTML = "";
        startScanner();
    });
}

scanBtn.addEventListener("click", startScanner);
scanStopBtn.addEventListener("click", stopScanner);

// ─────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────
(async function init() {
    try {
        config = await api("/api/config");
        targetUgEl.textContent = `/ ${config.daily_target_ug} µg`;
        targetPtsEl.textContent = `/ ${config.daily_target_pts}`;
    } catch (e) {
        console.error("Config load failed:", e);
    }
    await renderDay();
    // Initial search to populate the list
    runSearch();
})();
