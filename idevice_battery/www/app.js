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
  /** Collapsible sections inside a card: `${udid}:device` | `${udid}:acc` */
  let sections = new Set();
  /** entity_id cache: udid → { battery, battery_state, title } */
  let entityByUdid = {};

  function tipAttrs(text) {
    return `title="${escapeHtml(text || "")}"`;
  }

  function sectionKey(udid, name) {
    return `${udid}:${name}`;
  }

  function isSectionOpen(udid, name) {
    return sections.has(sectionKey(udid, name));
  }

  function ensureDefaultSections(udid, hasAccessories) {
    if (sections.has(`${udid}:_init`)) return;
    sections.add(`${udid}:_init`);
    // Device closed by default. Open Accessories only when empty → Discover visible.
    if (!hasAccessories) {
      sections.add(sectionKey(udid, "acc"));
    }
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

  const NO_ACC_PRIMARY =
    "No accessories on the last scan. Use Discover if you expect one.";
  const ACC_UNLOCK_HINT =
    "To get data from accessories, the device must be unlocked and on Wi‑Fi. Unlock, then tap refresh.";

  function friendlyError(raw, { hasAcc = false } = {}) {
    const s = String(raw || "").replace(/^accessories:\s*/i, "");
    if (/companion registry|none report battery|no accessories/i.test(s) && !hasAcc) {
      return { primary: NO_ACC_PRIMARY, secondary: ACC_UNLOCK_HINT };
    }
    if (/RemotePairing|Bonjour/i.test(s)) {
      if (hasAcc) {
        return {
          primary: "Accessory scan skipped this round — showing the last known values.",
          secondary: ACC_UNLOCK_HINT,
        };
      }
      return { primary: NO_ACC_PRIMARY, secondary: ACC_UNLOCK_HINT };
    }
    if (/Timeout/i.test(s)) {
      return {
        primary: "No response on the last check. Tap ↻ when the device is on Wi‑Fi.",
        secondary: "",
      };
    }
    if (/RuntimeError|Exception|Traceback/i.test(s)) {
      if (hasAcc) {
        return {
          primary: "Couldn't refresh accessories — last known values are still shown.",
          secondary: ACC_UNLOCK_HINT,
        };
      }
      return { primary: NO_ACC_PRIMARY, secondary: ACC_UNLOCK_HINT };
    }
    if (!s) return { primary: "", secondary: "" };
    return {
      primary: s.length > 120 ? `${s.slice(0, 117)}…` : s,
      secondary: "",
    };
  }

  function noteHtml(noteObj, muted) {
    if (!noteObj || !noteObj.primary) return "";
    const cls = `status-note${muted ? " muted" : ""}`;
    const sec = noteObj.secondary
      ? `<span class="status-note-sec">${escapeHtml(noteObj.secondary)}</span>`
      : "";
    return `<p class="${cls}">${escapeHtml(noteObj.primary)}${sec}</p>`;
  }

  /** Level colors: ≤20 red, ≤30 orange, >30 green. Charging → green (not "Not Charging"). */
  function isChargingState(chargeState) {
    const s = String(chargeState || "").toLowerCase().replace(/_/g, " ").trim();
    if (!s || /not\s*charg/.test(s)) return false;
    return /\bcharg/.test(s) || s === "full";
  }

  function battTone(level, chargeState) {
    if (level == null || Number.isNaN(Number(level))) return "unk";
    if (isChargingState(chargeState)) return "charging";
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
    const charge = (chargeState || "").replace(/_/g, " ");
    const charging = isChargingState(chargeState);
    const tone = battTone(pct, chargeState);
    const plug = charging
      ? `<span class="batt-plug" aria-hidden="true" ${tipAttrs("Charging")}>${ICON_PLUG}</span>`
      : "";
    return `
      <div class="batt-block" ${tipAttrs(charge ? `${label} · ${charge}` : label)}>
        <div class="batt-bar"><div class="batt-fill tone-${tone}" style="width:${pct != null ? pct : 0}%"></div></div>
        <div class="head-batt-row">${plug}<div class="head-batt">${escapeHtml(label)}</div></div>
      </div>`;
  }

  function accessoryFromDevice(dev) {
    const productType = dev.product_type || "";
    const name = dev.name || modelLabel(productType, "Accessory");
    const model = modelLabel(productType, name);
    const title = name !== model ? `${name} · ${model}` : name;
    const meta = productType || "—";
    return {
      udid: dev.udid || "",
      title,
      meta,
      level: dev.battery_level,
      state: dev.battery_state || "",
    };
  }

  function statusBadge(entry, hub, accessories) {
    const hasHub = hub && hub.battery_level != null;
    const err = entry && entry.error;
    const accNote = entry && entry.accessory_note;
    const hasAcc = (accessories && accessories.length > 0);
    if (hasHub) {
      const softNote = accNote ? friendlyError(accNote, { hasAcc }) : { primary: "", secondary: "" };
      return {
        cls: "ok",
        text: "Online",
        title: softNote.primary || "Reached on the last check.",
        note: softNote,
        noteMuted: !!softNote.primary,
      };
    }
    if (err && (/Timeout|Bonjour|RemotePairing/i.test(String(err)))) {
      return {
        cls: "idle",
        text: "Not reachable",
        title: "Couldn't reach this device on the last check.",
        note: friendlyError(err, { hasAcc }),
        noteMuted: true,
      };
    }
    if (err) {
      const n = friendlyError(err, { hasAcc });
      return {
        cls: "idle",
        text: "Not reachable",
        title: n.primary,
        note: n,
        noteMuted: true,
      };
    }
    return {
      cls: "idle",
      text: "No data yet",
      title: "Waiting for the first successful check.",
      note: { primary: "", secondary: "" },
      noteMuted: false,
    };
  }

  function deviceEntry(udid) {
    const list = (state.battery && state.battery.devices) || [];
    return list.find((x) => x.udid === udid) || null;
  }

  function accessoryRows(entry, watchFallback) {
    const accessories = [];
    const watch = (entry && entry.watch) || watchFallback || null;
    if (watch && watch.battery_level != null) {
      accessories.push(accessoryFromDevice(watch));
    }
    const extras = (entry && entry.accessories) || [];
    extras.forEach((a) => {
      accessories.push(accessoryFromDevice(a));
    });
    return accessories;
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
        <button type="button" class="btn btn-sm btn-copy" data-copy="${escapeHtml(it.eid)}" ${tipAttrs("Copy entity_id")}>Copy</button>
      </div>`
      )
      .join("");
  }

  function foldSection(udid, name, title, bodyHtml, open) {
    return `
      <div class="fold${open ? " open" : ""}">
        <button type="button" class="fold-head" data-section="${escapeHtml(udid)}" data-section-name="${escapeHtml(name)}" aria-expanded="${open}">
          <span class="chev" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span class="fold-title">${escapeHtml(title)}</span>
        </button>
        <div class="fold-body${open ? "" : " hidden"}">${bodyHtml}</div>
      </div>`;
  }

  function renderList() {
    const devices = state.store.devices || [];
    const empty = $("listEmpty");
    const list = $("deviceList");
    const batt = state.battery || {};
    empty.classList.toggle("hidden", devices.length > 0);
    list.innerHTML = "";

    devices.forEach((d) => {
      const entry = deviceEntry(d.udid);
      const isPrimary =
        !batt.phone_udid || batt.phone_udid === d.udid || devices.length === 1;
      const hub =
        (entry && entry.hub) ||
        (isPrimary ? batt.phone : null);
      const watchFallback = isPrimary ? batt.watch : null;
      const accessories = accessoryRows(entry, watchFallback);
      const b = statusBadge(entry, hub, accessories);
      const productType = (hub && hub.product_type) || d.product_type || "";
      const titleName = (hub && hub.name) || d.name || modelLabel(productType);
      const discoverBusy = discovering.has(d.udid);
      const hubUdid = (hub && hub.udid) || d.udid;

      const chargeLabel =
        hub && isChargingState(hub.battery_state)
          ? "Charging"
          : hub && hub.battery_state
            ? String(hub.battery_state).replace(/_/g, " ")
            : "";
      const busy = checking.has(d.udid);
      const isExpanded = expanded.has(d.udid);
      if (isExpanded) ensureDefaultSections(d.udid, accessories.length > 0);
      const deviceOpen = isSectionOpen(d.udid, "device");
      const accOpen = isSectionOpen(d.udid, "acc");

      const deviceBody = `
        <div class="tree-item meta-row">
          <span class="tree-name">Model / IP</span>
          <strong class="tree-val">${escapeHtml(deviceMeta(productType, d.host))}</strong>
        </div>
        <div class="tree-item meta-row">
          <span class="tree-name">Last check</span>
          <strong class="tree-val" ${tipAttrs(fmtTsAbsolute(batt.ts))}>${escapeHtml(fmtAgo(batt.ts))}</strong>
        </div>
        <div class="ent-block nested">
          <div class="ent-title">Entities</div>
          ${renderEntityRows(entityIdsFor(hubUdid))}
        </div>`;

      const accBody =
        accessories.length === 0
          ? `<div class="tree-item muted tree-empty">
              <span class="tree-name">None found</span>
              <button class="btn btn-sm" data-discover="${escapeHtml(d.udid)}" type="button" ${tipAttrs("Scan again for accessories paired to this device")} ${discoverBusy ? "disabled" : ""}>
                ${discoverBusy ? "…" : "Discover"}
              </button>
            </div>`
          : accessories
              .map((a) => {
                const ids = a.udid ? entityIdsFor(a.udid) : null;
                return `
                <div class="acc-card">
                  <div class="tree-item acc-row">
                    <div class="acc-lines">
                      <div class="acc-title">${escapeHtml(a.title)}</div>
                      <div class="acc-meta">${escapeHtml(a.meta)}${a.state ? ` · ${escapeHtml(String(a.state).replace(/_/g, " "))}` : ""}</div>
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
      card.className = `card${isExpanded ? " expanded" : " collapsed"}`;
      card.innerHTML = `
        <div class="card-row">
          <button type="button" class="card-main card-toggle" data-toggle="${escapeHtml(d.udid)}" aria-expanded="${isExpanded}" ${tipAttrs(isExpanded ? "Collapse details" : "Show details")}>
            <span class="chev" aria-hidden="true">${isExpanded ? "▾" : "▸"}</span>
            <div class="head-left">
              <h3>${escapeHtml(deviceTitle(titleName, productType))}</h3>
              <div class="status-line">
                <span class="badge ${b.cls}" ${tipAttrs(b.title)}>${escapeHtml(b.text)}</span>
                ${chargeLabel ? `<span class="charge-state">${escapeHtml(chargeLabel)}</span>` : ""}
              </div>
            </div>
            ${battBarHtml(hub && hub.battery_level, hub && hub.battery_state)}
          </button>
          <button class="btn btn-icon" data-check="${escapeHtml(d.udid)}" type="button" ${tipAttrs("Refresh now")} ${busy ? "disabled" : ""} aria-label="Refresh">
            ${busy ? "…" : "↻"}
          </button>
        </div>

        <div class="card-body${isExpanded ? "" : " hidden"}">
          ${noteHtml(b.note, b.noteMuted)}
          ${foldSection(d.udid, "device", "Device", deviceBody, deviceOpen)}
          ${foldSection(
            d.udid,
            "acc",
            accessories.length ? `Accessories (${accessories.length})` : "Accessories",
            accBody,
            accOpen
          )}
          <div class="card-actions">
            <button class="btn danger" data-remove="${escapeHtml(d.udid)}" type="button" ${tipAttrs("Remove this paired device from the list")}>Remove</button>
          </div>
        </div>`;
      list.appendChild(card);
    });

    list.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const udid = btn.dataset.toggle;
        if (expanded.has(udid)) expanded.delete(udid);
        else {
          expanded.add(udid);
          const entry = deviceEntry(udid);
          const isPrimary =
            !batt.phone_udid || batt.phone_udid === udid || devices.length === 1;
          const watchFallback = isPrimary ? batt.watch : null;
          ensureDefaultSections(udid, accessoryRows(entry, watchFallback).length > 0);
        }
        renderList();
      });
    });

    list.querySelectorAll("[data-section]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const udid = btn.dataset.section;
        const name = btn.dataset.sectionName;
        const key = sectionKey(udid, name);
        if (sections.has(key)) sections.delete(key);
        else sections.add(key);
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
          await api(`/devices/${encodeURIComponent(udid)}/check`, { method: "POST", body: "{}" });
          await refresh();
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
          await api(`/devices/${encodeURIComponent(udid)}/discover`, {
            method: "POST",
            body: "{}",
          });
          await refresh();
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

    const pollSec = state.store.poll_seconds || 180;
    const pollMin = Math.max(1, Math.round(pollSec / 60));
    $("footerStatus").textContent =
      `Auto refresh every ${pollMin} min · Expand a device for details · ↻ to check now`;
  }

  function fmtTsAbsolute(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
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
    const batt = state.battery || {};
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
      const isPrimary =
        !batt.phone_udid || batt.phone_udid === d.udid || devices.length === 1;
      const hub =
        (entry && entry.hub) || (isPrimary ? batt.phone : null);
      pushDev(
        (hub && hub.udid) || d.udid,
        (hub && hub.name) || d.name,
        (hub && hub.product_type) || d.product_type
      );
      const watch = (entry && entry.watch) || (isPrimary ? batt.watch : null);
      if (watch && watch.udid) {
        pushDev(watch.udid, watch.name, watch.product_type);
      }
      ((entry && entry.accessories) || []).forEach((a) => {
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
  }

  function collectAccessories(v) {
    const found = [];
    if (v && v.watch && v.watch.battery_level != null) found.push(v.watch);
    ((v && v.accessories) || []).forEach((a) => {
      if (a && a.battery_level != null) found.push(a);
    });
    return found;
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
    const hub = v && v.hub;
    const rows = [];
    if (hub) {
      const name = hub.name || deviceMeta?.name || "Device";
      const pt = hub.product_type || deviceMeta?.product_type || "";
      const udid = deviceMeta?.udid || hub.udid;
      rows.push(
        ensureUniqueIds({
          kind: "device",
          udid,
          name,
          title: deviceTitle(name, pt),
          battery: null,
          battery_state: null,
        })
      );
    }
    collectAccessories(v).forEach((a) => {
      rows.push(
        ensureUniqueIds({
          kind: "accessory",
          udid: a.udid,
          name: a.name || a.product_type || "Accessory",
          title: deviceTitle(a.name, a.product_type, "Accessory"),
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
        wiz.host = job.device.wifi_host || job.device.host_guess || wiz.host || "";
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
      const device = v.hub;
      const found = collectAccessories(v);
      const deviceTitleText = device
        ? deviceTitle(device.name || wiz.device?.name, device.product_type || wiz.device?.product_type)
        : "Device";
      const deviceVal = device
        ? `${device.battery_level}% · ${device.battery_state || "—"}`
        : "Device not found";
      const accRows = found
        .map((a) => {
          const t = deviceTitle(a.name, a.product_type, "Accessory");
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
      const device = wiz.verify?.hub;
      const found = collectAccessories(wiz.verify);
      const deviceName = device?.name || wiz.device?.name || "Device";
      const deviceTitleText = device
        ? deviceTitle(deviceName, device.product_type || wiz.device?.product_type)
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
        ${foundBlock(deviceName, device?.product_type || wiz.device?.product_type)}
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
      wiz.verify = { hub: null, watch: null, error: String(e.message || e) };
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
