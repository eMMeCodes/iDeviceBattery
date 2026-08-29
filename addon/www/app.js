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

  let state = { store: { devices: [], poll_seconds: 120 }, battery: {}, job: {} };
  let wiz = { step: 1, host: "", device: null, verify: null, pollTimer: null };
  let checking = new Set();

  /** Human model label from Apple ProductType when possible */
  function modelLabel(productType, fallbackName) {
    const map = {
      "iPhone15,4": "iPhone 15",
      "iPhone15,5": "iPhone 15 Plus",
      "iPhone16,1": "iPhone 15 Pro",
      "iPhone16,2": "iPhone 15 Pro Max",
      "iPhone17,1": "iPhone 16 Pro",
      "iPhone17,2": "iPhone 16 Pro Max",
      "iPhone17,3": "iPhone 16",
      "iPhone17,4": "iPhone 16 Plus",
      "iPad14,1": "iPad",
      "Watch7,18": "Apple Watch",
    };
    if (productType && map[productType]) return map[productType];
    if (productType) return productType;
    return fallbackName || "iDevice";
  }

  function statusBadge(entry, batt) {
    // Prefer per-device entry from battery.devices[]
    const err = (entry && entry.error) || (batt && batt.error);
    const hasHub = entry && entry.hub && entry.hub.battery_level != null;
    const hasPhone = batt && batt.phone && batt.phone.battery_level != null;
    if (err && (String(err).includes("Timeout") || String(err).includes("Bonjour") || String(err).includes("RemotePairing"))) {
      return {
        cls: "sleep",
        text: "Asleep / offline",
        title: "Device is unreachable over Wi‑Fi. Unlock it and keep Wi‑Fi on, then tap Check now.",
      };
    }
    if (err) {
      return {
        cls: "err",
        text: "Check failed",
        title: String(err),
      };
    }
    if (hasHub || hasPhone) {
      return {
        cls: "ok",
        text: "Online",
        title: "Last poll reached the device successfully.",
      };
    }
    return {
      cls: "sleep",
      text: "No data yet",
      title: "Waiting for the first successful poll.",
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
      accessories.push({
        name: watch.name || "Apple Watch",
        detail: `${watch.battery_level}% · ${watch.battery_state || "—"}`,
      });
    }
    const extras = (entry && entry.accessories) || [];
    extras.forEach((a) => {
      accessories.push({
        name: a.name || a.product_type || "Accessory",
        detail:
          a.battery_level != null
            ? `${a.battery_level}% · ${a.battery_state || "—"}`
            : "Not exposed by this device",
      });
    });
    return accessories;
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
      const b = statusBadge(entry || (isPrimary ? { error: batt.error, hub } : null), batt);
      const model = modelLabel(
        (hub && hub.product_type) || d.product_type,
        d.name
      );
      const titleName = (hub && hub.name) || d.name || model;
      const accessories = accessoryRows(entry, watchFallback);
      const accHtml =
        accessories.length === 0
          ? `<div class="tree-item muted">No accessories reported</div>`
          : accessories
              .map(
                (a) => `
            <div class="tree-item">
              <span class="tree-branch">└</span>
              <span class="tree-name">${escapeHtml(a.name)}</span>
              <strong class="tree-val">${escapeHtml(a.detail)}</strong>
            </div>`
              )
              .join("");

      const busy = checking.has(d.udid);
      const card = document.createElement("article");
      card.className = "card";
      card.innerHTML = `
        <div class="card-head">
          <div>
            <h3>${escapeHtml(titleName)}</h3>
            <div class="meta">${escapeHtml(model)}${d.host ? " · " + escapeHtml(d.host) : ""}</div>
          </div>
          <span class="badge ${b.cls}" title="${escapeHtml(b.title)}">${escapeHtml(b.text)}</span>
        </div>

        <div class="tree">
          <div class="tree-device">
            <div class="tree-item main">
              <span class="tree-name">Battery</span>
              <strong class="tree-val">${hub && hub.battery_level != null ? escapeHtml(hub.battery_level + "% · " + (hub.battery_state || "—")) : "—"}</strong>
            </div>
            <div class="tree-section">Accessories</div>
            ${accHtml}
          </div>
          <div class="tree-item meta-row">
            <span class="tree-name">Last check</span>
            <strong class="tree-val">${fmtTs(batt.ts)}</strong>
          </div>
        </div>

        <div class="card-actions">
          <button class="btn primary" data-check="${escapeHtml(d.udid)}" type="button" ${busy ? "disabled" : ""}>
            ${busy ? "Checking…" : "Check now"}
          </button>
          <button class="btn danger" data-remove="${escapeHtml(d.udid)}" type="button">Remove</button>
        </div>`;
      list.appendChild(card);
    });

    list.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remove this paired device from the list?")) return;
        await api(`/devices/${encodeURIComponent(btn.dataset.remove)}`, { method: "DELETE" });
        await refresh();
      });
    });
    list.querySelectorAll("[data-check]").forEach((btn) => {
      btn.addEventListener("click", async () => {
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

    const poll = state.store.poll_seconds || 120;
    $("footerStatus").textContent = `Automatic check every ${poll}s · Use Check now to refresh immediately`;
  }

  function fmtTs(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString([], {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "short",
    });
  }
  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refresh() {
    state = await api("/status");
    renderList();
  }

  /* —— Wizard —— */
  function openWizard() {
    wiz = { step: 1, host: "", device: null, verify: null, pollTimer: null };
    $("wizard").classList.remove("hidden");
    renderWizard();
  }
  function closeWizard() {
    if (wiz.pollTimer) clearInterval(wiz.pollTimer);
    $("wizard").classList.add("hidden");
  }

  function renderWizard() {
    const title = $("wizTitle");
    const body = $("wizBody");
    const back = $("wizBack");
    const next = $("wizNext");
    back.disabled = false;
    next.disabled = false;
    next.textContent = "Continue";

    if (wiz.step === 1) {
      title.textContent = "Add an iDevice";
      body.innerHTML = `
        <p><strong>Unlock the device you are pairing.</strong></p>
        <p>Plug it into this Home Assistant machine via USB.</p>
        <p>A notification about Trust will appear on the device:<br/>
        <strong>You have to tap Trust.</strong></p>
        <p class="hint">Apple Watch and accessories paired to this device may appear after setup if the device exposes their battery.</p>`;
      back.textContent = "Cancel";
      next.onclick = () => { wiz.step = 2; startPairAndWatch(); };
      back.onclick = closeWizard;
      return;
    }

    if (wiz.step === 2) {
      title.textContent = "Connect & Trust";
      const job = state.job || {};
      body.innerHTML = `
        <div class="spin"></div>
        <p>${escapeHtml(job.message || "Looking for USB device…")}</p>
        <p class="hint">Keep the device unlocked. If a Trust prompt appears, tap Trust.</p>
        ${job.device ? `<p class="hint">Found: <strong>${escapeHtml(job.device.name || job.device.udid || "")}</strong>
          ${job.device.product_type ? "(" + escapeHtml(modelLabel(job.device.product_type)) + ")" : ""}</p>` : ""}
        ${job.state === "error" ? `<p style="color:var(--err)">${escapeHtml(job.message)}</p>` : ""}`;
      back.onclick = () => { wiz.step = 1; if (wiz.pollTimer) clearInterval(wiz.pollTimer); renderWizard(); };
      if (job.state === "error") {
        next.textContent = "Retry";
        next.onclick = () => startPairAndWatch();
      } else if (job.state === "ok") {
        wiz.device = job.device;
        wiz.host = job.device.host_guess || wiz.host || "";
        next.textContent = "Continue";
        next.onclick = () => { wiz.step = 3; renderWizard(); };
      } else {
        next.disabled = true;
        next.textContent = "Waiting…";
        next.onclick = null;
      }
      return;
    }

    if (wiz.step === 3) {
      title.textContent = "Wi‑Fi address";
      body.innerHTML = `
        <p>Confirm how Home Assistant reaches this device on Wi‑Fi.</p>
        <div class="field">
          <label for="hostIp">IP address</label>
          <input id="hostIp" value="${escapeHtml(wiz.host)}" placeholder="192.168.x.x" autocomplete="off" />
        </div>
        <p class="hint">A DHCP reservation is recommended but not required.</p>`;
      back.onclick = () => { wiz.step = 2; renderWizard(); };
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
      const hub = v.hub;
      const watch = v.watch;
      body.innerHTML = `
        <ul class="check">
          <li><span>Device battery</span><strong>${hub ? escapeHtml(hub.battery_level + "% · " + hub.battery_state) : "Fail"}</strong></li>
          <li><span>Accessories</span><strong></strong></li>
          <li class="indent"><span>└ Apple Watch</span><strong>${watch ? escapeHtml((watch.name || "Watch") + " · " + watch.battery_level + "% · " + watch.battery_state) : "Not available"}</strong></li>
        </ul>
        ${v.error ? `<p class="hint">${escapeHtml(v.error)}</p>` : ""}
        <p class="hint">Unlock the device and keep Wi‑Fi on if something failed, then retry.</p>`;
      back.onclick = () => { wiz.step = 3; renderWizard(); };
      next.textContent = hub ? "Finish" : "Retry";
      next.onclick = async () => {
        if (!hub) { runVerify(); return; }
        await api("/pair/finish", {
          method: "POST",
          body: JSON.stringify({
            host: wiz.host,
            name: wiz.device?.name,
          }),
        });
        wiz.step = 5;
        renderWizard();
        await refresh();
      };
      return;
    }

    if (wiz.step === 5) {
      title.textContent = "Device added";
      body.innerHTML = `
        <p>Pairing is saved. Battery values will refresh on the next check.</p>
        <p class="hint">${escapeHtml(wiz.device?.name || "Device")} · ${escapeHtml(wiz.host)}</p>`;
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

  async function startPairAndWatch() {
    wiz.step = 2;
    renderWizard();
    try {
      state.job = await api("/pair/start", { method: "POST", body: "{}" });
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

  refresh();
  setInterval(refresh, 15000);
})();
