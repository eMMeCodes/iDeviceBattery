(() => {
  const $ = (id) => document.getElementById(id);

  const API = (() => {
    const p = location.pathname;
    if (p.endsWith("/")) return p + "api";
    if (p.endsWith("/index.html")) return p.replace(/index\.html$/, "api");
    return p.replace(/\/?$/, "/") + "api";
  })();

  async function api(path, opts = {}) {
    const res = await fetch(`${API}${path}`, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  let state = { store: { devices: [], poll_seconds: 180 }, battery: {}, job: {} };
  let wiz = { step: 1, host: "", device: null, verify: null, pollTimer: null };
  let checking = new Set();
  let discovering = new Set();
  /** UDIDs with expanded card body (default: all collapsed) */
  let expanded = new Set();
  /** entity_id cache: udid → { battery, battery_state, title } */
  let entityByUdid = {};
  let flashUntil = 0;
  let flashText = "";
  let flashKind = "";

  function setFooterMsg(text, kind = "") {
    flashText = text || "";
    flashKind = kind || "";
    flashUntil = text ? Date.now() + 6000 : 0;
    renderFooter();
  }

  function renderFooter() {
    const el = $("footerStatus");
    if (!el) return;
    const pollSec = state.store?.poll_seconds || 180;
    const pollMin = Math.max(1, Math.round(pollSec / 60));
    const ver = state.version ? ` · v${state.version}` : "";
    const base = `Auto refresh every ${pollMin} min · ↻ checks one device now${ver}`;
    if (flashText && Date.now() < flashUntil) {
      el.className = `footer flash-${flashKind || "info"}`;
      el.textContent = flashText;
      return;
    }
    el.className = "footer";
    el.textContent = base;
  }

  /** Apple ProductType → friendly name (full map in product_map.js) */
  const PRODUCT_MAP = window.IDEVICE_PRODUCT_MAP || {};

  function modelLabel(productType, fallbackName) {
    if (productType && PRODUCT_MAP[productType]) return PRODUCT_MAP[productType];
    if (productType) return productType;
    return fallbackName || "iDevice";
  }

  /** e.g. Mal9000 · iPhone 15 */
  function deviceTitle(name, productType, fallback) {
    const model = modelLabel(productType, fallback);
    const n = (name || "").trim() || model;
    if (model && n !== model) return `${n} · ${model}`;
    return n;
  }

  /** e.g. iPhone15,4 · 192.168.1.35 */
  function deviceMeta(productType, host) {
    const code = productType || "—";
    return host ? `${code} · ${host}` : code;
  }

  const KIND_LABELS = {
    iphone: "iPhone",
    ipad: "iPad",
    ipod: "iPod",
    watch: "Watch",
    airpods: "AirPods",
    headphones: "Headphones",
    pencil: "Pencil",
    keyboard: "Keyboard",
    trackpad: "Trackpad",
    mac: "Mac",
    accessory: "Accessory",
    device: "Device",
  };

  function classifyKind(productType, udid) {
    const p = String(productType || "");
    const u = String(udid || "");
    if (p.startsWith("Watch") || u.startsWith("00008310")) return "watch";
    if (p.startsWith("iPhone")) return "iphone";
    if (p.startsWith("iPad")) return "ipad";
    if (p.startsWith("iPod")) return "ipod";
    if (p.startsWith("AirPods") || p.startsWith("iProd") || p.includes("AirPods")) return "airpods";
    if (p.startsWith("Beats") || p.includes("Headphone")) return "headphones";
    if (p.includes("Pencil")) return "pencil";
    if (p.includes("Keyboard")) return "keyboard";
    if (p.includes("Trackpad")) return "trackpad";
    if (p.startsWith("Mac") || p.startsWith("iMac")) return "mac";
    if (p) return "accessory";
    return "device";
  }

  function kindLabel(kind, fallback) {
    return KIND_LABELS[kind] || fallback || kind || "Device";
  }

  function deviceView(entry, storeDev) {
    const d = storeDev || {};
    const e = entry || {};
    const productType = e.product_type || d.product_type || "";
    return {
      udid: e.udid || d.udid,
      host: e.host || d.host,
      name: e.name || d.name,
      productType,
      kind: e.kind || classifyKind(productType, e.udid || d.udid),
      level: e.battery_level ?? null,
      state: e.battery_state || "",
      stale: !!e.stale,
      updatedAt: e.updated_at,
      error: e.error,
    };
  }

  function rawAccessories(entry) {
    const e = entry || {};
    const seen = new Set();
    const out = [];
    (e.accessories || []).forEach((raw) => {
      if (!raw || raw.battery_level == null) return;
      const udid = raw.udid || "";
      const key = udid || raw.name || String(out.length);
      if (seen.has(key)) return;
      seen.add(key);
      const kind = raw.kind || classifyKind(raw.product_type, udid);
      out.push({
        ...raw,
        kind,
        stale: !!raw.stale,
      });
    });
    return out;
  }

  function listAccessories(entry) {
    return rawAccessories(entry).map((a) => accessoryFromDevice(a, { stale: !!a.stale }));
  }

  /** True while actively charging (not "full" / "Not Charging"). */
  function isChargingState(chargeState) {
    const s = String(chargeState || "").toLowerCase().replace(/_/g, " ").trim();
    if (!s || /not\s*charg/.test(s) || s === "full") return false;
    return /\bcharg/.test(s);
  }

  function isFullState(chargeState) {
    return String(chargeState || "").toLowerCase().replace(/_/g, " ").trim() === "full";
  }

  function battTone(level, chargeState) {
    if (level == null || Number.isNaN(Number(level))) return "unk";
    // Green bar while charging or topped-off on power
    if (isChargingState(chargeState) || isFullState(chargeState)) return "charging";
    const n = Number(level);
    if (n <= 20) return "low";
    if (n <= 30) return "mid";
    return "ok";
  }

  /** Inline SVG ≈ mdi:power-plug (no external icon font needed). */
  const ICON_PLUG = `<svg class="batt-plug-svg" viewBox="0 0 24 24" width="16" height="16" focusable="false"><path fill="currentColor" d="M16 7V3h-2v4h-4V3H8v4C7.45 7 7 7.45 7 8v4.5c0 2.32 1.46 4.3 3.5 5.09V20h3v-2.41c2.04-.79 3.5-2.77 3.5-5.09V8c0-.55-.45-1-1-1z"/></svg>`;

  function battBarHtml(level, chargeState) {
    const pct = level != null && !Number.isNaN(Number(level)) ? Math.max(0, Math.min(100, Number(level))) : null;
    const label = pct != null ? `${Math.round(pct)}%` : "—";
    const charging = isChargingState(chargeState);
    const tone = battTone(pct, chargeState);
    const plug = charging
      ? `<span class="batt-plug" aria-hidden="true">${ICON_PLUG}</span>`
      : "";
    return `
      <div class="batt-block">
        <div class="batt-bar"><div class="batt-fill tone-${tone}" style="width:${pct != null ? pct : 0}%"></div></div>
        <div class="head-batt-row">${plug}<div class="head-batt">${escapeHtml(label)}</div></div>
      </div>`;
  }

  function accessoryFromDevice(dev, { stale = false } = {}) {
    const productType = dev.product_type || "";
    const kind = dev.kind || classifyKind(productType, dev.udid);
    const name = dev.name || modelLabel(productType, kindLabel(kind, "Accessory"));
    const model = modelLabel(productType, name);
    const title = name !== model ? `${name} · ${model}` : name;
    const meta = [kindLabel(kind), productType].filter(Boolean).join(" · ") || "—";
    return {
      udid: dev.udid || "",
      kind,
      title,
      meta,
      level: dev.battery_level,
      state: dev.battery_state || "",
      stale: !!stale,
    };
  }

  function statusBadge(view) {
    const hasBatt = view && view.level != null;
    if (hasBatt && view.stale) return { cls: "idle", text: "Stale" };
    if (hasBatt) return { cls: "ok", text: "Online" };
    if (view && view.error) return { cls: "idle", text: "Not reachable" };
    return { cls: "idle", text: "No data yet" };
  }

  function deviceEntry(udid) {
    const list = (state.battery && state.battery.devices) || [];
    return list.find((x) => x.udid === udid) || null;
  }

  function predictedEntityIds(udid) {
    const k = udidKey(udid);
    return {
      battery: `sensor.idevice_${k}_battery`,
      battery_state: `sensor.idevice_${k}_battery_state`,
    };
  }

  function entityIdsFor(udid) {
    const cached = entityByUdid[udid] || {};
    const pred = predictedEntityIds(udid);
    return {
      battery: cached.battery || pred.battery,
      battery_state: cached.battery_state || pred.battery_state,
    };
  }

  function renderEntityRows(ids) {
    const items = [
      { eid: ids.battery, kind: "Battery" },
      { eid: ids.battery_state, kind: "State" },
    ].filter((x) => x.eid);
    if (!items.length) {
      return `<p class="hint">No entities yet — run a successful check first.</p>`;
    }
    return items
      .map(
        (it) => `
      <div class="ent-row">
        <span class="ent-kind">${escapeHtml(it.kind)}</span>
        <code class="ent-id">${escapeHtml(it.eid)}</code>
        <button type="button" class="btn btn-sm btn-copy" data-copy="${escapeHtml(it.eid)}">Copy</button>
      </div>`
      )
      .join("");
  }

  function renderList() {
    const devices = state.store.devices || [];
    const empty = $("listEmpty");
    const list = $("deviceList");
    const batt = state.battery || {};
    empty.classList.toggle("hidden", devices.length > 0);
    $("btnAdd")?.classList.toggle("hidden", devices.length === 0);
    list.innerHTML = "";

    devices.forEach((d, idx) => {
      const entry = deviceEntry(d.udid);
      const view = deviceView(entry, d);
      const accessories = listAccessories(entry);
      const b = statusBadge(view);
      const productType = view.productType || "";
      const titleName = view.name || modelLabel(productType);
      const discoverBusy = discovering.has(d.udid);
      const deviceUdid = view.udid || d.udid;

      const chargeLabel =
        view.level == null
          ? ""
          : isFullState(view.state)
            ? "Full"
            : isChargingState(view.state)
              ? "Charging"
              : view.state
                ? String(view.state).replace(/_/g, " ")
                : "";
      const busy = checking.has(d.udid);
      const isExpanded = expanded.has(d.udid);

      const deviceTs = view.updatedAt || batt.ts;

      const accHtml =
        accessories.length === 0
          ? ""
          : accessories
              .map((a) => {
                const ids = a.udid ? entityIdsFor(a.udid) : null;
                return `
                <div class="acc-card">
                  <div class="tree-item acc-row">
                    <div class="acc-lines">
                      <div class="acc-title">${escapeHtml(a.title)}${a.stale ? ` <span class="acc-stale">stale</span>` : ""}</div>
                      <div class="acc-meta">${escapeHtml(a.meta)}${a.state ? ` · ${escapeHtml(String(a.state).replace(/_/g, " "))}` : ""}${a.stale ? " · last known" : ""}</div>
                    </div>
                    ${battBarHtml(a.level, a.state)}
                  </div>
                  ${
                    ids
                      ? `<div class="ent-block nested">
                          <div class="ent-title">Entities</div>
                          ${renderEntityRows(ids)}
                        </div>`
                      : ""
                  }
                </div>`;
              })
              .join("");

      const card = document.createElement("article");
      const tone = idx % 2 === 0 ? "a" : "b";
      const hasAccCls = accessories.length ? " card-has-acc" : "";
      card.className = `card card-tone-${tone}${hasAccCls}${isExpanded ? " expanded" : " collapsed"}`;
      card.innerHTML = `
        <div class="card-row">
          <button type="button" class="card-main card-toggle" data-toggle="${escapeHtml(d.udid)}" aria-expanded="${isExpanded}">
            <div class="head-left">
              <h3>${escapeHtml(deviceTitle(titleName, productType))}</h3>
              <div class="status-line">
                <span class="badge ${b.cls}">${escapeHtml(b.text)}</span>
                ${chargeLabel ? `<span class="charge-state">${escapeHtml(chargeLabel)}</span>` : ""}
              </div>
            </div>
            ${battBarHtml(view.level, view.state)}
          </button>
          <button class="btn btn-icon" data-check="${escapeHtml(d.udid)}" type="button" aria-label="Refresh" ${busy ? "disabled" : ""}>
            ${busy ? "…" : "↻"}
          </button>
        </div>

        <div class="card-body${isExpanded ? "" : " hidden"}">
          <div class="flat-block">
            <div class="tree-item meta-row">
              <span class="tree-name">Model / IP</span>
              <strong class="tree-val">${escapeHtml(deviceMeta(productType, d.host))}</strong>
            </div>
            <div class="tree-item meta-row">
              <span class="tree-name">Last updated</span>
              <strong class="tree-val">${escapeHtml(fmtAgo(deviceTs))}${view.stale ? " · stale" : ""}</strong>
            </div>
            <div class="ent-block nested">
              <div class="ent-title">Entities</div>
              ${renderEntityRows(entityIdsFor(deviceUdid))}
            </div>
          </div>
          ${accHtml}
          <div class="card-actions">
            <button class="btn btn-sm" data-discover="${escapeHtml(d.udid)}" type="button" ${discoverBusy ? "disabled" : ""}>${discoverBusy ? "…" : "Discover"}</button>
            <button class="btn danger" data-remove="${escapeHtml(d.udid)}" type="button">Remove</button>
          </div>
        </div>`;
      list.appendChild(card);

      if (!isExpanded) {
        card.addEventListener("click", (ev) => {
          if (ev.target.closest("[data-check], [data-remove], [data-discover], [data-copy]")) {
            return;
          }
          expanded.add(d.udid);
          renderList();
        });
      }
    });

    list.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const udid = btn.dataset.toggle;
        if (expanded.has(udid)) expanded.delete(udid);
        else expanded.add(udid);
        renderList();
      });
    });

    list.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm("Remove this paired device from the list?")) return;
        await api(`/devices/${encodeURIComponent(btn.dataset.remove)}`, { method: "DELETE" });
        await refresh();
      });
    });
    list.querySelectorAll("[data-check]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const udid = btn.dataset.check;
        checking.add(udid);
        renderList();
        try {
          const out = await api(`/devices/${encodeURIComponent(udid)}/check`, {
            method: "POST",
            body: "{}",
          });
          await refresh();
          const entry =
            out.result ||
            ((out.battery && out.battery.devices) || []).find((d) => d.udid === udid);
          const view = deviceView(entry, null);
          if (view.stale) {
            setFooterMsg("No response — device may be asleep or off Wi‑Fi.", "warn");
          } else if (view.level != null) {
            const st = String(view.state || "").replace(/_/g, " ");
            setFooterMsg(`Updated · ${view.level}%${st ? ` · ${st}` : ""}`, "ok");
          } else {
            setFooterMsg("Check finished — no battery data returned.", "warn");
          }
        } catch (e) {
          alert(e.message || String(e));
        } finally {
          checking.delete(udid);
          await refresh();
        }
      });
    });

    list.querySelectorAll("[data-discover]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const udid = btn.dataset.discover;
        discovering.add(udid);
        renderList();
        try {
          const out = await api(`/devices/${encodeURIComponent(udid)}/discover`, {
            method: "POST",
            body: "{}",
          });
          await refresh();
          const entry =
            ((out.battery && out.battery.devices) || []).find((d) => d.udid === udid);
          const found = (out.accessories || []).filter((a) => a && a.battery_level != null);
          const staleLeft = listAccessories(entry).some((a) => a.stale);
          if (found.length) {
            const kinds = [...new Set(found.map((a) => kindLabel(a.kind || classifyKind(a.product_type, a.udid))))];
            setFooterMsg(
              `Discover · ${found.length} ${kinds.join(", ") || "accessories"}`,
              "ok"
            );
          } else if (staleLeft) {
            setFooterMsg("No accessories this scan — last known kept.", "warn");
          } else {
            setFooterMsg("Discover finished — no accessories reported.", "info");
          }
        } catch (e) {
          alert(e.message || String(e));
        } finally {
          discovering.delete(udid);
          await refresh();
        }
      });
    });

    list.querySelectorAll("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const text = btn.dataset.copy || "";
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          } else {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.left = "-9999px";
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            ta.remove();
          }
          const prev = btn.textContent;
          btn.textContent = "Copied";
          btn.disabled = true;
          setTimeout(() => {
            btn.textContent = prev;
            btn.disabled = false;
          }, 1200);
        } catch (_) {
          alert(text);
        }
      });
    });

    renderFooter();
  }

  function fmtAgo(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "—";
    const sec = Math.round((Date.now() - d.getTime()) / 1000);
    if (sec < 45) return "just now";
    const rtf = new Intl.RelativeTimeFormat("en", { numeric: "always" });
    const steps = [
      ["year", 31536000],
      ["month", 2592000],
      ["week", 604800],
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
    ];
    for (const [unit, size] of steps) {
      if (Math.abs(sec) >= size) {
        return rtf.format(-Math.round(sec / size), unit);
      }
    }
    return "just now";
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function collectEntityLookupRows() {
    const devices = state.store.devices || [];
    const rows = [];
    const seen = new Set();
    const pushDev = (udid, name, productType) => {
      if (!udid || seen.has(udid)) return;
      seen.add(udid);
      rows.push(
        ensureUniqueIds({
          udid,
          name: name || "",
          title: deviceTitle(name, productType),
          battery: null,
          battery_state: null,
        })
      );
    };
    devices.forEach((d) => {
      const entry = deviceEntry(d.udid);
      const view = deviceView(entry, d);
      pushDev(view.udid, view.name, view.productType);
      rawAccessories(entry).forEach((a) => {
        if (a && a.udid) pushDev(a.udid, a.name, a.product_type);
      });
    });
    return rows;
  }

  async function refreshEntities() {
    const rows = collectEntityLookupRows();
    if (!rows.length) return;
    try {
      const resolved = await lookupEntitiesFromHa(rows, { attempts: 3, delayMs: 250 });
      const next = { ...entityByUdid };
      resolved.forEach((r) => {
        if (!r.udid) return;
        next[r.udid] = {
          battery: r.battery || null,
          battery_state: r.battery_state || null,
          title: r.title || "",
        };
      });
      entityByUdid = next;
    } catch (_) {
      /* keep predicted ids */
    }
  }

  async function refresh() {
    state = await api("/status");
    await refreshEntities();
    renderList();
    renderFooter();
  }

  function collectAccessories(v) {
    return rawAccessories(v);
  }

  function udidKey(udid) {
    let u = String(udid || "").trim();
    if (u.includes("-")) u = u.split("-").pop();
    return (u || "unknown").replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
  }

  function ensureUniqueIds(row) {
    const r = { ...row };
    if (!r.udid) return r;
    const k = udidKey(r.udid);
    r.unique_id_battery = r.unique_id_battery || `idevice_${k}_battery`;
    r.unique_id_battery_state =
      r.unique_id_battery_state || `idevice_${k}_battery_state`;
    return r;
  }

  function foundBlock(name, productType) {
    const n = (name || "").trim() || "Device";
    const model = productType ? modelLabel(productType) : "";
    return `<p class="wiz-found">${escapeHtml(n)}${
      model && model !== n
        ? ` <span class="wiz-found-model">${escapeHtml(model)}</span>`
        : ""
    }</p>`;
  }

  function entityBlockFromRow(row) {
    const batt = row.battery || "—";
    const st = row.battery_state || "—";
    return `
      <div class="ent-block">
        <div class="ent-title">${escapeHtml(row.title || row.name || "Device")}</div>
        <code>${escapeHtml(batt)}</code>
        <code>${escapeHtml(st)}</code>
      </div>`;
  }

  function getHass() {
    try {
      for (const win of [window.parent, window.top, window]) {
        const el = win?.document?.querySelector?.("home-assistant");
        if (el && el.hass) return el.hass;
      }
    } catch (_) {
      /* cross-origin */
    }
    return null;
  }

  /** Resolve real entity_id: hass.entities unique_id → states udid → API token. */
  async function lookupEntitiesFromHa(rows, { attempts = 12, delayMs = 400 } = {}) {
    const list = (rows || []).map((r) => ensureUniqueIds(r));
    if (!list.length) return list;

    for (let i = 0; i < attempts; i++) {
      const hass = getHass();
      let complete = true;

      // 1) Frontend entity registry (unique_id → entity_id) — most reliable in Ingress
      if (hass && hass.entities) {
        const byUid = {};
        Object.entries(hass.entities).forEach(([eid, meta]) => {
          if (meta && meta.unique_id) byUid[meta.unique_id] = eid;
        });
        list.forEach((row) => {
          if (row.unique_id_battery && byUid[row.unique_id_battery]) {
            row.battery = byUid[row.unique_id_battery];
          }
          if (row.unique_id_battery_state && byUid[row.unique_id_battery_state]) {
            row.battery_state = byUid[row.unique_id_battery_state];
          }
        });
      }

      // 2) States by attribute udid
      let states = [];
      if (hass && hass.states) {
        states = Object.values(hass.states);
      } else {
        try {
          const token = hass?.auth?.data?.access_token;
          const headers = token ? { Authorization: `Bearer ${token}` } : {};
          const res = await fetch("/api/states", {
            credentials: "same-origin",
            headers,
          });
          if (res.ok) states = await res.json();
        } catch (_) {
          /* ignore */
        }
      }
      if (states.length) {
        list.forEach((row) => {
          if (!row.udid) return;
          const want = String(row.udid).toUpperCase();
          states.forEach((st) => {
            const attrs = st.attributes || {};
            if (String(attrs.udid || "").toUpperCase() !== want) return;
            const eid = st.entity_id || "";
            if (!eid.startsWith("sensor.")) return;
            if (eid.endsWith("_battery_state")) row.battery_state = eid;
            else if (eid.endsWith("_battery")) row.battery = eid;
          });
        });
      }

      // 3) Add-on registry resolve (reads HA entity registry on disk when mapped)
      if (list.some((r) => !r.battery || !r.battery_state)) {
        try {
          const resolved = await api("/resolve-entities", {
            method: "POST",
            body: JSON.stringify({ rows: list }),
          });
          (resolved.rows || []).forEach((r, idx) => {
            if (r.battery) list[idx].battery = r.battery;
            if (r.battery_state) list[idx].battery_state = r.battery_state;
          });
        } catch (_) {
          /* ignore */
        }
      }

      list.forEach((row) => {
        if (!row.battery || !row.battery_state) complete = false;
      });
      if (complete) return list;
      await new Promise((r) => setTimeout(r, delayMs));
    }
    return list;
  }

  function rowsFromVerify(v, deviceMeta) {
    const view = deviceView(v, deviceMeta);
    const rows = [];
    if (view.level != null) {
      const name = view.name || deviceMeta?.name || kindLabel(view.kind, "Device");
      const pt = view.productType || deviceMeta?.product_type || "";
      const udid = deviceMeta?.udid || view.udid;
      rows.push(
        ensureUniqueIds({
          role: "device",
          kind: view.kind || "device",
          udid,
          name,
          title: deviceTitle(name, pt),
          battery: null,
          battery_state: null,
        })
      );
    }
    collectAccessories(v).forEach((a) => {
      const fallback = kindLabel(a.kind, "Accessory");
      rows.push(
        ensureUniqueIds({
          role: "accessory",
          kind: a.kind || "accessory",
          udid: a.udid,
          name: a.name || a.product_type || fallback,
          title: deviceTitle(a.name, a.product_type, fallback),
          battery: null,
          battery_state: null,
        })
      );
    });
    return rows;
  }

  /* —— Wizard —— */
  const RETRUST_DWELL_MS = 1800;

  function openWizard() {
    wiz = {
      step: 1,
      host: "",
      device: null,
      verify: null,
      pollTimer: null,
      reTrustFlow: false,
      reTrustSince: null,
      reTrustTimer: null,
      wifiDetectTried: false,
    };
    $("wizard").classList.remove("hidden");
    renderWizard();
  }
  function closeWizard() {
    if (wiz.pollTimer) clearInterval(wiz.pollTimer);
    if (wiz.reTrustTimer) clearTimeout(wiz.reTrustTimer);
    $("wizard").classList.add("hidden");
  }

  function markReTrustFlow() {
    if (!wiz.reTrustFlow) {
      wiz.reTrustFlow = true;
      wiz.reTrustSince = Date.now();
    }
  }

  function reTrustDwellDone() {
    if (!wiz.reTrustSince) return true;
    return Date.now() - wiz.reTrustSince >= RETRUST_DWELL_MS;
  }

  function scheduleReTrustDwell() {
    if (wiz.reTrustTimer) clearTimeout(wiz.reTrustTimer);
    if (!wiz.reTrustSince) return;
    const left = RETRUST_DWELL_MS - (Date.now() - wiz.reTrustSince);
    if (left <= 0) return;
    wiz.reTrustTimer = setTimeout(() => {
      wiz.reTrustTimer = null;
      if (wiz.step === 2) renderWizard();
    }, left + 30);
  }

  function renderWizard() {
    const title = $("wizTitle");
    const body = $("wizBody");
    const back = $("wizBack");
    const next = $("wizNext");
    back.classList.remove("hidden");
    back.disabled = false;
    next.disabled = false;
    next.textContent = "Continue";

    if (wiz.step === 1) {
      title.textContent = "Add an iDevice";
      body.innerHTML = `
        <p><strong>Unlock the device you are pairing.</strong></p>
        <p>Connect the device to the same LAN as Home Assistant.</p>
        <p>Plug it into this Home Assistant machine via USB.</p>
        <p>If this is the <strong>first</strong> time: tap <strong>Trust</strong> on the iDevice when asked.</p>
        <p class="hint">If you paired before and removed it here: <strong>no Trust prompt</strong>.</p>
        <p class="hint">The wizard will reconnect it automatically.</p>
        <p class="hint">Accessories paired to this device should appear after setup.</p>
        <p class="hint">(If the device exposes their battery.)</p>`;
      back.textContent = "Cancel";
      next.onclick = () => { wiz.step = 2; startPairAndWatch(); };
      back.onclick = closeWizard;
      return;
    }

    if (wiz.step === 2) {
      title.textContent = "Connect & Trust";
      const job = state.job || {};
      const phase = job.phase || "";
      const reTrustPhase =
        phase === "retrust" ||
        phase === "lockdown_probe" ||
        /already trusted/i.test(job.message || "");
      if (reTrustPhase) markReTrustFlow();

      const foundHtml = job.device
        ? foundBlock(job.device.name || job.device.udid, job.device.product_type)
        : "";

      // Known device: one stable screen until job OK + minimum dwell (avoids phase flash)
      const holdReTrust =
        wiz.reTrustFlow &&
        job.state !== "error" &&
        !(job.state === "ok" && reTrustDwellDone());

      if (job.state === "error") {
        body.innerHTML = `
          <p style="color:var(--err)">${escapeHtml(job.message)}</p>
          <p class="hint">Check the cable and that the device is unlocked.</p>`;
      } else if (holdReTrust) {
        scheduleReTrustDwell();
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Already trusted — reconnecting</strong></p>
          <p>No Trust tap needed on the device.</p>
          <p class="hint">Preparing Wi‑Fi pairing for battery and accessories…</p>
          ${foundHtml}`;
      } else if (phase === "wifi_lookup") {
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Looking up Wi‑Fi address…</strong></p>
          <p class="hint">Waiting for Bonjour / LAN advertisement (can take a few seconds).</p>
          ${foundHtml}`;
      } else if (job.state === "ok" || phase === "ready") {
        if (wiz.reTrustFlow) {
          body.innerHTML = `
            <p><strong>Reconnected.</strong></p>
            <p>No Trust tap was needed.</p>
            <p class="hint">Next: confirm the Wi‑Fi address.</p>
            ${foundHtml}`;
        } else {
          body.innerHTML = `
            <p>Pairing complete.</p>
            <p>Confirm Wi‑Fi address, then continue.</p>
            ${foundHtml}`;
        }
      } else if (phase === "remotepairing") {
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Creating RemotePairing record…</strong></p>
          <p class="hint">Needed for battery over Wi‑Fi and accessories.</p>
          <p class="hint">Please wait — this can take up to a minute.</p>
          ${foundHtml}`;
      } else if (phase === "lockdown_ok") {
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Lockdown paired.</strong></p>
          <p class="hint">Next: creating RemotePairing record…</p>
          ${foundHtml}`;
      } else if (
        phase === "usb" ||
        phase === "starting" ||
        (phase === "trust_prepare" && !job.device)
      ) {
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Looking for USB device…</strong></p>
          <p class="hint">Keep the device unlocked.</p>
          <p class="hint">Cable connected to this Home Assistant machine.</p>`;
      } else if (phase === "trust_prepare") {
        body.innerHTML = `
          <div class="spin"></div>
          <p><strong>Preparing pairing…</strong></p>
          <p class="hint">Keep the device unlocked.</p>
          ${foundHtml}`;
      } else {
        body.innerHTML = `
          <div class="spin"></div>
          <p>If a Trust prompt appears, tap Trust.</p>
          <p>If none appears, wait — or tap Retry.</p>
          <p class="hint">Keep the device unlocked.</p>
          ${foundHtml}`;
      }

      back.onclick = () => {
        wiz.step = 1;
        if (wiz.pollTimer) clearInterval(wiz.pollTimer);
        if (wiz.reTrustTimer) clearTimeout(wiz.reTrustTimer);
        renderWizard();
      };
      if (job.state === "error") {
        next.textContent = "Retry";
        next.onclick = () => startPairAndWatch(true);
      } else if (holdReTrust) {
        next.disabled = true;
        next.textContent = "Reconnecting…";
        next.onclick = null;
      } else if (job.state === "ok") {
        wiz.device = job.device;
        wiz.host = job.device.wifi_host || wiz.host || "";
        next.textContent = "Continue";
        next.onclick = () => { wiz.step = 3; renderWizard(); };
      } else if (
        job.state === "need_trust" &&
        wiz.trustWaitSince &&
        Date.now() - wiz.trustWaitSince > 25000
      ) {
        next.disabled = false;
        next.textContent = "Retry (no Trust?)";
        next.onclick = () => startPairAndWatch(true);
      } else {
        if (job.state === "need_trust" && !wiz.trustWaitSince) {
          wiz.trustWaitSince = Date.now();
        }
        if (job.state !== "need_trust") wiz.trustWaitSince = null;
        next.disabled = true;
        if (phase === "remotepairing" || phase === "lockdown_ok" || phase === "wifi_lookup") {
          next.textContent = "Working…";
        } else {
          next.textContent = "Waiting…";
        }
        next.onclick = null;
      }
      return;
    }

    if (wiz.step === 3) {
      title.textContent = "Wi‑Fi address";
      const hostVal = wiz.host || "";
      const linkLocal = /^fe80:/i.test(hostVal) || /%/.test(hostVal);
      const lead = wiz.reTrustFlow
        ? `<p><strong>Known device</strong> — confirm this IP still reaches it.</p>`
        : `<p>Confirm how Home Assistant reaches this device on Wi‑Fi.</p>`;
      const emptyHint = !hostVal
        ? `<p class="hint" style="color:var(--warn)">No LAN address found yet — device must be on Wi‑Fi. Tap Detect IP, or enter it manually.</p>`
        : "";
      body.innerHTML = `
        ${lead}
        <div class="field">
          <label for="hostIp">IP address</label>
          <input id="hostIp" value="${escapeHtml(hostVal)}" placeholder="192.168.x.x" autocomplete="off" />
        </div>
        ${emptyHint}
        ${
          linkLocal
            ? `<p class="hint" style="color:var(--warn)">Link-local / IPv6 detected — prefer a LAN IPv4 (e.g. 192.168.x.x) if the check fails.</p>`
            : ""
        }
        <p class="hint">A DHCP reservation is recommended but not required.</p>
        <p><button id="wizDetectIp" class="btn" type="button">Detect IP</button></p>`;
      back.onclick = () => { wiz.step = 2; renderWizard(); };
      const runDetect = async () => {
        const btn = $("wizDetectIp");
        if (btn) {
          btn.disabled = true;
          btn.textContent = "Detecting…";
        }
        next.disabled = true;
        try {
          const data = await api("/pair/wifi-host", {
            method: "POST",
            body: JSON.stringify({ udid: wiz.device?.udid }),
          });
          if (data.host) {
            wiz.host = data.host;
            renderWizard();
            return;
          }
          alert("No Wi‑Fi address found yet. Unlock the device, keep Wi‑Fi on, then try Detect IP again.");
        } catch (e) {
          alert(e.message || String(e));
        }
        if (btn) {
          btn.disabled = false;
          btn.textContent = "Detect IP";
        }
        next.disabled = false;
      };
      $("wizDetectIp").onclick = runDetect;
      // Auto-detect once when field is empty
      if (!hostVal && !wiz.wifiDetectTried) {
        wiz.wifiDetectTried = true;
        setTimeout(runDetect, 50);
      }
      next.onclick = () => {
        wiz.host = $("hostIp")?.value?.trim() || "";
        if (!wiz.host) { alert("Enter the Wi‑Fi IP address."); return; }
        wiz.step = 4;
        runVerify();
      };
      return;
    }

    if (wiz.step === 4) {
      title.textContent = "Verify";
      const v = wiz.verify;
      if (!v) {
        body.innerHTML = `<div class="spin"></div><p>Checking connection…</p>`;
        next.disabled = true;
        back.onclick = () => { wiz.step = 3; renderWizard(); };
        return;
      }
      const view = deviceView(v, wiz.device);
      const device = view.level != null ? view : null;
      const found = collectAccessories(v);
      const deviceTitleText = device
        ? deviceTitle(device.name || wiz.device?.name, device.productType || wiz.device?.product_type)
        : kindLabel(view.kind, "Device");
      const deviceVal = device
        ? `${device.level}% · ${device.state || "—"}`
        : "Device not found";
      const accRows = found
        .map((a) => {
          const t = deviceTitle(a.name, a.product_type, kindLabel(a.kind, "Accessory"));
          const val = `${a.battery_level}% · ${a.battery_state || "—"}`;
          return `<li><span class="wiz-found">${escapeHtml(t)}</span><strong>${escapeHtml(val)}</strong></li>`;
        })
        .join("");
      const accBlock = found.length
        ? `<li class="section"><span>Accessories found</span><strong>${found.length}</strong></li>${accRows}`
        : "";
      body.innerHTML = `
        <ul class="check">
          <li><span class="wiz-found">${escapeHtml(deviceTitleText)}</span><strong>${escapeHtml(deviceVal)}</strong></li>
          ${accBlock}
        </ul>
        ${
          !device
            ? `<p class="hint">${escapeHtml(v.error || "Device not found")}</p>
               <p class="hint">If something failed, then retry.</p>`
            : found.length
              ? ""
              : `<p class="hint">No accessories reported on this device.</p>
                 <p class="hint">Only the Device will be added.</p>`
        }`;
      back.onclick = () => { wiz.step = 3; renderWizard(); };
      // Success → next screen (entities); failure → retry verify
      next.textContent = device ? "Next" : "Retry";
      next.onclick = async () => {
        if (!device) { runVerify(); return; }
        next.disabled = true;
        next.textContent = "Publishing…";
        try {
          wiz.finish = await api("/pair/finish", {
            method: "POST",
            body: JSON.stringify({
              host: wiz.host,
              name: wiz.device?.name,
            }),
          });
          wiz.verify = v;
          let rows = ((wiz.finish && wiz.finish.entities) || []).map(ensureUniqueIds);
          const missing =
            !rows.length ||
            rows.some((r) => !r.battery || !r.battery_state);
          if (missing) {
            next.textContent = "Reading entities…";
            const seed =
              rows.length > 0
                ? rows
                : rowsFromVerify(v, {
                    udid: wiz.device?.udid || wiz.finish?.device?.udid,
                    name: wiz.device?.name,
                    product_type: wiz.device?.product_type,
                  });
            if (seed[0] && !seed[0].udid) {
              seed[0].udid = wiz.finish?.device?.udid || wiz.device?.udid;
            }
            rows = await lookupEntitiesFromHa(seed);
          }
          wiz.finish = { ...(wiz.finish || {}), entities: rows };
          wiz.step = 5;
          renderWizard();
          await refresh();
        } catch (e) {
          alert(e.message || String(e));
          next.disabled = false;
          next.textContent = "Next";
        }
      };
      return;
    }

    if (wiz.step === 5) {
      const view = deviceView(wiz.verify, wiz.device);
      const device = view.level != null ? view : null;
      const found = collectAccessories(wiz.verify);
      const deviceName = device?.name || wiz.device?.name || kindLabel(view.kind, "Device");
      const deviceTitleText = device
        ? deviceTitle(deviceName, device.productType || wiz.device?.product_type)
        : deviceName;
      const rows = (wiz.finish && wiz.finish.entities) || [];

      title.textContent = found.length ? "Devices added" : "Device paired";

      const summary = found.length
        ? `<p><strong>Devices paired.</strong></p>
           <p>${found.length} accessor${found.length === 1 ? "y" : "ies"} exposed.</p>`
        : `<p><strong>Device paired.</strong></p>
           <p>No accessories were exposed.</p>`;

      const blocks = rows.length
        ? rows.map(entityBlockFromRow).join("")
        : `<p class="hint">Entities not visible yet.</p>
           <p class="hint">Open Home Assistant → Devices → area iDevice.</p>`;

      body.innerHTML = `
        ${summary}
        <p class="hint">Battery entities published to Home Assistant via MQTT.</p>
        ${foundBlock(deviceName, device?.productType || wiz.device?.product_type)}
        <p class="hint">${escapeHtml(wiz.host || "")}</p>
        ${blocks}`;
      back.classList.add("hidden");
      back.disabled = true;
      next.textContent = "Done";
      next.onclick = () => {
        back.classList.remove("hidden");
        closeWizard();
        refresh();
      };
    }
  }

  async function startPairAndWatch(force = false) {
    wiz.step = 2;
    wiz.trustWaitSince = null;
    wiz.reTrustFlow = false;
    wiz.reTrustSince = null;
    if (wiz.reTrustTimer) clearTimeout(wiz.reTrustTimer);
    wiz.reTrustTimer = null;
    renderWizard();
    try {
      state.job = await api("/pair/start", {
        method: "POST",
        body: JSON.stringify({ force: !!force }),
      });
    } catch (e) {
      state.job = { state: "error", message: String(e.message || e) };
    }
    renderWizard();
    if (wiz.pollTimer) clearInterval(wiz.pollTimer);
    wiz.pollTimer = setInterval(async () => {
      try {
        state.job = await api("/pair/status");
        renderWizard();
        if (state.job.state === "ok" || state.job.state === "error") {
          clearInterval(wiz.pollTimer);
          wiz.pollTimer = null;
          // Re-render after dwell if re-trust finished early
          if (state.job.state === "ok" && wiz.reTrustFlow && !reTrustDwellDone()) {
            scheduleReTrustDwell();
          }
        }
      } catch (_) { /* ignore */ }
    }, 1500);
  }

  async function runVerify() {
    wiz.verify = null;
    renderWizard();
    try {
      wiz.verify = await api("/pair/verify", {
        method: "POST",
        body: JSON.stringify({
          udid: wiz.device?.udid,
          host: wiz.host,
        }),
      });
    } catch (e) {
      wiz.verify = { battery_level: null, accessories: [], error: String(e.message || e) };
    }
    renderWizard();
  }

  $("btnAdd").addEventListener("click", openWizard);
  document.querySelectorAll("[data-open-add]").forEach((el) =>
    el.addEventListener("click", openWizard)
  );
  $("wizClose").addEventListener("click", closeWizard);

  const ADDON_INFO = "/config/app/local_idevice_battery/info";
  $("btnBack").addEventListener("click", (ev) => {
    ev.preventDefault();
    try {
      if (window.top && window.top !== window) {
        if (window.top.history.length > 1) {
          window.top.history.back();
          return;
        }
        window.top.location.href = ADDON_INFO;
        return;
      }
    } catch (_) { /* cross-origin */ }
    if (history.length > 1) {
      history.back();
      return;
    }
    window.location.href = ADDON_INFO;
  });

  refresh();
  setInterval(refresh, 15000);
})();
