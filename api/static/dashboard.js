/* Agritech Command Center — dashboard UI logic.
 * Pure vanilla JS; talks to the same-origin REST API (/api/*). */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const CATEGORY_ICONS = { Grain: "🌾", Vegetable: "🥬", Fruit: "🍎", Organic: "🌱" };
  const SENTIMENT_EMOJI = { POSITIVE: "🟢", NEUTRAL: "🟡", NEGATIVE: "🔴" };
  const INTENT_EMOJI = {
    PRICE_INQUIRY: "💰", QUALITY_INQUIRY: "🌿", DELIVERY_INQUIRY: "🚚", GENERAL_CHAT: "💬",
  };

  // ---------------- API helpers ----------------
  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    let body;
    try { body = await res.json(); } catch { body = {}; }
    if (!res.ok || body.ok === false) {
      throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
    }
    return body.data;
  }

  function apiPost(path, payload) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // ---------------- Toast ----------------
  let toastTimer = null;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("error", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
  }

  async function guard(fn, failMsg = "Request failed") {
    try {
      return await fn();
    } catch (e) {
      console.error(failMsg, e);
      toast(`${failMsg}: ${e.message}`, true);
      return null;
    }
  }

  // ---------------- State & helpers ----------------
  let users = [];
  let buyers = [];
  let sellers = [];
  let products = [];
  let activeStreams = [];

  function fmtMoney(n) {
    return `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  function fmtPct(n) {
    const v = Number(n || 0);
    return `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString();
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  function badge(status) {
    return `<span class="badge ${escapeHtml(String(status).toLowerCase())}">${escapeHtml(status)}</span>`;
  }

  // ---------------- Navigation ----------------
  function initNav() {
    $$(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$(".nav-item").forEach((b) => b.classList.remove("active"));
        $$(".tab").forEach((t) => t.classList.remove("active"));
        btn.classList.add("active");
        const tab = $(`#tab-${btn.dataset.tab}`);
        if (tab) tab.classList.add("active");
        $("#page-title").textContent = btn.textContent.trim();
        refresh(tab.id.replace("tab-", ""));
      });
    });
  }

  // ---------------- Health / KPIs ----------------
  async function refresh() {
    await Promise.all([
      loadHealth(),
      loadUsers(),
      loadProducts(),
      loadStreams(),
    ]);
    if ($("#tab-overview").classList.contains("active")) loadOverview();
  }

  async function loadHealth() {
    const health = await guard(() => api("/health"), "Health check failed");
    const badgeEl = $("#health-badge");
    const dbEl = $("#db-badge");
    if (health) {
      badgeEl.textContent = "● Online";
      badgeEl.className = "health-badge ok";
      dbEl.textContent = `DB: ${health.database || "—"}`;
    } else {
      badgeEl.textContent = "● Offline";
      badgeEl.className = "health-badge down";
    }
  }

  async function loadUsers() {
    users = (await guard(() => api("/api/users"), "Could not load users")) || [];
    buyers = users.filter((u) => u.role === "buyer");
    sellers = users.filter((u) => u.role === "seller");
    populateSelect("#mp-buyer", buyers, (u) => `#${u.id} ${u.username}`);
    populateSelect("#an-seller", sellers, (u) => `#${u.id} ${u.username}`);
    populateSelect("#inv-seller", sellers, (u) => `#${u.id} ${u.username}`);
  }

  function populateSelect(sel, items, labelFn) {
    const el = $(sel);
    if (!el) return;
    const current = el.value;
    el.innerHTML = items
      .map((i) => `<option value="${i.id}">${escapeHtml(labelFn(i))}</option>`)
      .join("");
    if (items.some((i) => String(i.id) === String(current))) el.value = current;
  }

  async function loadProducts() {
    const params = new URLSearchParams({ limit: "100", dynamic_price: "true" });
    products = (await guard(() => api(`/api/products?${params}`), "Could not load products")) || [];
  }

  async function loadStreams() {
    const all = (await guard(() => api("/api/streams"), "Could not load streams")) || [];
    activeStreams = all.filter((s) => s.is_active);
  }

  // ---------------- Overview ----------------
  async function loadOverview() {
    const orders = (await guard(() => api("/api/orders?limit=8"), "Could not load orders")) || [];

    $("#kpi-products").textContent = products.length;
    $("#kpi-orders").textContent = orders.length;
    $("#kpi-streams").textContent = activeStreams.length;
    $("#kpi-revenue").textContent = "—";

    // Category chart
    const counts = {};
    products.forEach((p) => {
      const c = p.category || "Other";
      counts[c] = (counts[c] || 0) + 1;
    });
    renderBarV("#chart-categories", counts, (v) => `${v} products`);

    // Recent orders
    const box = $("#recent-orders");
    if (!orders.length) {
      box.innerHTML = '<span class="muted">No orders yet.</span>';
    } else {
      box.innerHTML = `<ul style="list-style:none;padding:0">${orders
        .map(
          (o) => `<li style="display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);font-size:13px">
            <span>${escapeHtml(o.product_name || "—")}</span>
            <span class="muted">${escapeHtml(o.buyer_username || o.buyer_id || "—")}</span>
            <strong>${fmtMoney(o.total_price)}</strong>
          </li>`
        )
        .join("")}</ul>`;
    }
  }

  // ---------------- Marketplace ----------------
  async function loadMarketplace() {
    const cat = $("#mp-category").value;
    const q = $("#mp-search").value.trim();
    let list = products.slice();
    if (cat) list = list.filter((p) => p.category === cat);
    if (q) list = list.filter((p) => (p.name || "").toLowerCase().includes(q.toLowerCase()));

    const grid = $("#product-grid");
    if (!list.length) {
      grid.innerHTML = '<span class="muted">No products match your filters.</span>';
      return;
    }
    grid.innerHTML = list.map(renderProductCard).join("");
  }

  function renderProductCard(p) {
    const seller = p.seller || {};
    const dynamic = Number(p.dynamic_price || p.price || 0);
    const base = Number(p.price || 0);
    const change = Number(p.price_change_pct || 0) * 100;
    const stock = Number(p.stock_kg || 0);
    const stars = "★".repeat(Math.round(p.rating || 0)) + "☆".repeat(5 - Math.round(p.rating || 0));
    return `
      <div class="product-card" data-id="${p.id}">
        <div class="product-head">
          <span class="product-icon">${CATEGORY_ICONS[p.category] || "📦"}</span>
          <div>
            <div class="product-name">${escapeHtml(p.name)}</div>
            <div class="product-seller">🏪 ${escapeHtml(seller.username || "Seller")} · 📍 ${escapeHtml(seller.location || "—")}</div>
          </div>
        </div>
        <div class="product-desc">${escapeHtml((p.description || "").slice(0, 160))}</div>
        <div class="product-meta">
          <span class="price">${fmtMoney(dynamic)}/kg</span>
          ${base !== dynamic ? `<span class="price-cmp">${fmtMoney(base)}</span>` : ""}
          <span class="${change >= 0 ? "delta-up" : "delta-down"}">${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(1)}%</span>
          <span class="stars">${stars} ${Number(p.rating || 0).toFixed(1)}</span>
          <span class="stock">📦 ${stock.toFixed(1)} kg</span>
        </div>
        <div class="buy-row">
          <input class="input" type="number" min="0.5" max="${stock || 0.5}" step="0.5" value="1" data-qty="${p.id}">
          <button class="btn primary" data-buy="${p.id}">🛒 Buy</button>
        </div>
      </div>`;
  }

  async function buyProduct(productId) {
    const qtyInput = $(`input[data-qty="${productId}"]`);
    const qty = qtyInput ? Number(qtyInput.value) : 1;
    const buyerId = $("#mp-buyer").value;
    if (!buyerId) { toast("Select a buyer ID", true); return; }
    const product = products.find((p) => p.id === productId);
    const ok = await guard(
      () => apiPost("/api/orders", { buyer_id: Number(buyerId), product_id: productId, quantity_kg: qty }),
      "Order failed"
    );
    if (ok) {
      toast(`Order placed: ${qty}kg ${product ? product.name : ""} for ${fmtMoney(ok.order ? ok.order.total_price : 0)}`);
      setTimeout(refresh, 600);
    }
  }

  // ---------------- Streams ----------------
  let selectedStreamId = null;
  let chatPollTimer = null;

  async function loadStreamsTab() {
    const list = $("#streams-list");
    const allStreams = (await guard(() => api("/api/streams"), "Could not load streams")) || [];
    if (!allStreams.length) {
      list.innerHTML = '<span class="muted">No streams found.</span>';
      return;
    }
    list.innerHTML = allStreams
      .map(
        (s) => `<div class="stream-item ${s.id === selectedStreamId ? "selected" : ""}" data-stream="${s.id}">
          <span class="live-dot ${s.is_active ? "on" : "off"}"></span>
          <div>
            <strong>${escapeHtml(s.stream_title)}</strong>
            <div class="muted">Seller #${s.seller_id} · started ${fmtDate(s.started_at)}</div>
          </div>
          <span class="badge ${s.is_active ? "delivered" : "CANCELLED"}">${s.is_active ? "LIVE" : "Ended"}</span>
        </div>`
      )
      .join("");

    $$(".stream-item").forEach((item) => {
      item.addEventListener("click", () => {
        selectedStreamId = Number(item.dataset.stream);
        $$(".stream-item").forEach((i) => i.classList.toggle("selected", i.dataset.stream === String(selectedStreamId)));
        $("#chat-text").disabled = false;
        $("#chat-send").disabled = false;
        loadChat();
        startChatPoll();
      });
    });

    if (selectedStreamId && $$(`.stream-item[data-stream="${selectedStreamId}"]`).length) {
      $("#chat-text").disabled = false;
      $("#chat-send").disabled = false;
      loadChat();
      startChatPoll();
    }
  }

  async function loadChat() {
    if (!selectedStreamId) return;
    const msgs = (await guard(() => api(`/api/streams/${selectedStreamId}/messages?limit=50`), "Could not load chat")) || [];
    const box = $("#chat-messages");
    if (!msgs.length) {
      box.innerHTML = '<span class="muted">No messages yet.</span>';
      return;
    }
    box.innerHTML = msgs
      .slice(-30)
      .map((m) => {
        const sent = m.sentiment_label || "NEUTRAL";
        return `<div class="chat-msg">
          <span class="chat-avatar">${SENTIMENT_EMOJI[sent] || "🟡"}</span>
          <div class="chat-bubble">
            <strong>${escapeHtml(m.username || "User")}</strong>
            <span class="chat-sentiment">· ${fmtDate(m.timestamp)}</span>
            <div>${escapeHtml(m.message_text)}</div>
          </div>
          <span class="chat-intent">${INTENT_EMOJI[m.intent_tag] || ""} ${escapeHtml(m.intent_tag || "")}</span>
        </div>`;
      })
      .join("");
    box.scrollTop = box.scrollHeight;
  }

  function startChatPoll() {
    if (chatPollTimer) clearInterval(chatPollTimer);
    chatPollTimer = setInterval(() => {
      if ($("#tab-streams").classList.contains("active")) loadChat();
    }, 15000);
  }

  async function sendChat(e) {
    e.preventDefault();
    const text = $("#chat-text").value.trim();
    if (!text || !selectedStreamId) return;
    const buyerId = $("#mp-buyer").value;
    if (!buyerId) { toast("Select a buyer/seller user ID to chat", true); return; }
    const ok = await guard(
      () => apiPost("/api/streams/send_message", {
        stream_id: selectedStreamId, user_id: Number(buyerId), message_text: text,
      }),
      "Send failed"
    );
    if (ok) { $("#chat-text").value = ""; loadChat(); }
  }

  // ---------------- Orders ----------------
  async function loadOrders() {
    const status = $("#orders-status").value;
    const url = `/api/orders?limit=200${status ? `&status=${encodeURIComponent(status)}` : ""}`;
    const orders = (await guard(() => api(url), "Could not load orders")) || [];
    const tbody = $("#orders-table");
    if (!orders.length) {
      tbody.innerHTML = '<tr><td colspan="7"><span class="muted">No orders found.</span></td></tr>';
      return;
    }
    tbody.innerHTML = orders
      .map(
        (o) => `<tr>
          <td>#${o.id}</td>
          <td>${escapeHtml(o.buyer_username || o.buyer_id || "—")}</td>
          <td>${escapeHtml(o.product_name || "—")}</td>
          <td>${o.quantity_kg} kg</td>
          <td>${fmtMoney(o.total_price)}</td>
          <td>${badge(o.status)}</td>
          <td>${fmtDate(o.created_at)}</td>
        </tr>`
      )
      .join("");
    $("#kpi-orders").textContent = orders.length;
  }

  // ---------------- Analytics ----------------
  async function loadAnalytics() {
    const sellerId = $("#an-seller").value;
    const data = await guard(() => api(`/api/analytics/${sellerId}`), "Could not load analytics");
    const kpis = $("#an-kpis");
    if (!data) { kpis.innerHTML = ""; $("#chart-forecast").innerHTML = '<span class="muted">No data.</span>'; $("#chart-sentiment").innerHTML = ""; return; }

    const sales = data.sales_summary || {};
    kpis.innerHTML = `
      <div class="kpi-card"><div class="kpi-label">Total Revenue</div><div class="kpi-value">${fmtMoney(sales.total_revenue)}</div></div>
      <div class="kpi-card"><div class="kpi-label">Quantity Sold</div><div class="kpi-value">${Number(sales.total_quantity_kg || 0).toLocaleString("en-IN")} kg</div></div>
      <div class="kpi-card"><div class="kpi-label">Orders</div><div class="kpi-value">${sales.total_orders || 0}</div></div>
      <div class="kpi-card"><div class="kpi-label">Avg Order Value</div><div class="kpi-value">${fmtMoney(sales.avg_order_value)}</div></div>
    `;

    // Forecast: horizontal bars, avg predicted quantity per product
    const fcBox = $("#chart-forecast");
    const forecasts = data.demand_forecasts || [];
    if (!forecasts.length) {
      fcBox.innerHTML = '<span class="muted">No forecast data for this seller.</span>';
    } else {
      const rows = forecasts.map((fc) => {
        const preds = fc.predictions || [];
        const avg = preds.length
          ? preds.reduce((s, d) => s + Number(d.predicted_quantity || 0), 0) / preds.length
          : 0;
        return { name: fc.product_name || `#${fc.product_id}`, avg, slope: fc.trend_slope || 0 };
      });
      renderBarH(fcBox, rows, (v) => `${v.toFixed(1)} kg`);
    }

    // Sentiment donut
    const sent = data.sentiment_summary || {};
    renderDonut("#chart-sentiment", sent);
  }

  // ---------------- Inventory ----------------
  async function loadInventory() {
    const sellerId = $("#inv-seller").value;
    const items = (await guard(() => api(`/api/seller/${sellerId}/inventory`), "Could not load inventory")) || [];
    const box = $("#inventory-list");
    if (!items.length) {
      box.innerHTML = '<span class="muted">No inventory for this seller yet.</span>';
      return;
    }
    const maxStock = Math.max(...items.map((i) => Number((i.product || {}).stock_kg || 0)), 1);
    const critical = items.filter((i) => String(i.status).includes("Out of Stock") || String(i.status).includes("Critical"));
    const low = items.filter((i) => String(i.status).includes("Low"));

    box.innerHTML = items
      .map((it) => {
        const p = it.product || {};
        const stock = Number(p.stock_kg || 0);
        const width = Math.max((stock / maxStock) * 100, 2);
        const color = String(it.status).includes("🔴") ? "#e5533b" : String(it.status).includes("🟠") ? "#f0a500" : "#43a047";
        return `<div class="inv-item">
          <div class="inv-name">${escapeHtml(p.name)} <span class="muted">#${p.id}</span></div>
          <div class="inv-progress"><span style="width:${width}%;background:${color}"></span></div>
          <strong>${stock.toFixed(1)} kg</strong>
          <span class="muted">${fmtMoney(p.price)} → ${fmtMoney(it.dynamic_price)}</span>
          <span class="${Number(it.price_change_pct || 0) * 100 > 0 ? "delta-up" : "delta-down"}">${fmtPct(it.price_change_pct)}</span>
          <span class="badge ${String(it.status).includes("🔴") ? "cancelled" : String(it.status).includes("🟠") ? "pending" : "delivered"}">${escapeHtml(it.status)}</span>
        </div>`;
      })
      .join("");

    let alerts = "";
    if (critical.length) {
      alerts += `<div class="alerts">${critical.map((i) => `<div class="alert-item critical">🔴 <strong>${escapeHtml(i.product.name)}</strong>: ${escapeHtml(i.status)}</div>`).join("")}</div>`;
    }
    if (low.length) {
      alerts += `<div class="alerts">${low.map((i) => `<div class="alert-item low">🟠 <strong>${escapeHtml(i.product.name)}</strong>: ${escapeHtml(i.status)}</div>`).join("")}</div>`;
    }
    box.insertAdjacentHTML("beforeend", alerts);
  }

  // ---------------- Chart renderers ----------------
  function renderBarV(sel, counts, labelFn) {
    const el = $(sel);
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const max = Math.max(...entries.map(([, v]) => v), 1);
    el.innerHTML = entries.length
      ? entries
          .map(
            ([k, v]) => `<div class="bar-group">
            <div class="bar-val">${v}</div>
            <div class="bar" style="height:${(v / max) * 120}px"></div>
            <div class="bar-label" title="${escapeHtml(k)}">${escapeHtml(k)}</div>
          </div>`
          )
          .join("")
      : '<span class="muted">No data.</span>';
  }

  function renderBarH(box, rows, valFn) {
    const max = Math.max(...rows.map((r) => r.avg), 1);
    box.innerHTML = rows
      .map((r) => {
        const color = r.slope > 0.1 ? "#43a047" : r.slope < -0.1 ? "#e5533b" : "#3794ff";
        return `<div class="h-row">
          <span class="h-label" title="${escapeHtml(r.name)}">${escapeHtml(r.name.slice(0, 20))}</span>
          <div class="h-track"><div class="h-fill" style="width:${(r.avg / max) * 100}%;background:${color}"></div></div>
          <span class="h-val">${valFn(r.avg)}</span>
        </div>`;
      })
      .join("");
  }

  function renderDonut(box, sent) {
    const counts = sent.counts || {};
    const total = (counts.POSITIVE || 0) + (counts.NEUTRAL || 0) + (counts.NEGATIVE || 0);
    if (!total) {
      $(box).innerHTML = '<span class="muted">No reviews yet.</span>';
      return;
    }
    const colors = { POSITIVE: "#43a047", NEUTRAL: "#f0a500", NEGATIVE: "#e5533b" };
    const segments = ["POSITIVE", "NEUTRAL", "NEGATIVE"]
      .filter((k) => counts[k])
      .map((k) => `${colors[k]} ${(counts[k] / total) * 360}deg`)
      .join(", ");
    $(box).innerHTML = `
      <div class="donut-wrap">
        <div class="donut" style="background: conic-gradient(${segments})"></div>
        <div class="donut-legend">
          ${["POSITIVE", "NEUTRAL", "NEGATIVE"]
            .filter((k) => counts[k])
            .map((k) => `<div class="legend-item"><span class="legend-swatch" style="background:${colors[k]}"></span>${k} — ${counts[k]} (${Math.round((counts[k] / total) * 100)}%)</div>`)
            .join("")}
          <div class="legend-item">Average score: <strong>${Number(sent.average_score || 0).toFixed(3)}</strong></div>
          <div class="legend-item">Total reviews: <strong>${sent.total_reviews || 0}</strong></div>
        </div>
      </div>`;
  }

  // ---------------- Wire up ----------------
  const TAB_LOADERS = {
    overview: loadOverview,
    marketplace: loadMarketplace,
    streams: loadStreamsTab,
    orders: loadOrders,
    analytics: loadAnalytics,
    inventory: loadInventory,
  };

  function refresh(tabName) {
    if (tabName && TAB_LOADERS[tabName]) TAB_LOADERS[tabName]();
    else if (!tabName) {
      const active = $(".tab.active");
      const key = active ? active.id.replace("tab-", "") : "overview";
      if (TAB_LOADERS[key]) TAB_LOADERS[key]();
    }
  }

  function init() {
    initNav();
    $("#refresh-btn").addEventListener("click", () => refresh());

    $("#mp-apply").addEventListener("click", refresh.bind(null, "marketplace"));
    $("#orders-apply").addEventListener("click", refresh.bind(null, "orders"));
    $("#an-seller").addEventListener("change", refresh.bind(null, "analytics"));
    $("#inv-seller").addEventListener("change", refresh.bind(null, "inventory"));

    $("#product-grid").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-buy]");
      if (btn) buyProduct(Number(btn.dataset.buy));
    });

    $("#chat-send").addEventListener("click", sendChat);
    $("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(e); });

    setInterval(() => {
      const el = $("#clock");
      el.textContent = new Date().toLocaleTimeString();
    }, 1000);

    // Auto-refresh live areas (no dimming, silent fetch)
    setInterval(() => {
      if (selectedStreamId && $("#tab-streams").classList.contains("active")) loadChat();
      if ($("#tab-overview").classList.contains("active")) {
        loadProducts().then(() => { $("#kpi-products").textContent = products.length; });
      }
    }, 30000);

    refresh();
  }

  document.addEventListener("DOMContentLoaded", init);
})();