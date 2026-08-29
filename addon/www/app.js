(() => {
  const $ = (id) => document.getElementById(id);

  // API base: works under HA Ingress subpath
  const API = (() => {
    const p = location.pathname;
    if (p.endsWith("/")) return p + "api";
    if (p.endsWith("/index.html")) return p.replace(/index\.html$/, "api");
    // ingress often ends without trailing file
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

  function badgeFor(battery) {
    if (battery && battery.error) return { cls: "sleep", text: "Unreachable / sleeping" };
    if (battery && (battery.phone || battery.watch)) return { cls: "ok", text: "OK" };
    return { cls: "sleep", text: "Waiting" };
  }

  function renderList() {
    const devices = state.store.devices || [];
    const empty = $("listEmpty");
    const list = $("deviceList");
    const batt = state.battery || {};
    empty.classList.toggle("hidden", devices.length > 0);
    list.innerHTML = "";
    devices.forEach((d) => {
      const isPrimary =
        !batt.phone_udid || batt.phone_udid === d.udid || devices.length === 1;
      const phone = isPrimary ? batt.phone : null;
      const watch = isPrimary ? batt.watch : null;
      const b = badgeFor(isPrimary ? batt : null);
      const card = document.createElement("article");
      card.className = "card";
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
          <div>
            <h3>${escapeHtml(d.name || "iDevice")}</h3>
            <div class="meta">${escapeHtml(d.product_type || "—")} · ${escapeHtml(d.host || "")}</div>
          </div>
          <span class="badge ${b.cls}">${b.text}</span>
        </div>
        <div class="rows">
          <div class="row"><span>Hub battery</span><strong>${fmtBatt(phone)}</strong></div>
          <div class="row"><span>Watch</span><strong>${fmtBatt(watch, true)}</strong></div>
          <div class="row"><span>Last update</span><strong>${fmtTs(batt.ts)}</strong></div>
        </div>
        <div class="card-actions">
          <button class="btn" data-remove="${escapeHtml(d.udid)}" type="button">Remove</button>
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
    const poll = state.store.poll_seconds || 120;
    const err = batt.error ? ` · Last poll: ${batt.error}` : " · Last poll OK";
    $("footerStatus").textContent = `Poll every ${poll}s${devices.length ? err : ""}`;
  }

  function fmtBatt(obj, isWatch) {
    if (!obj || obj.battery_level == null) return isWatch ? "—" : "—";
    const name = isWatch && obj.name ? `${obj.name} · ` : "";
    return `${name}${obj.battery_level}% · ${obj.battery_state || "—"}`;
  }
  function fmtTs(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
        <p class="hint">Apple Watch and accessories paired to this hub may appear after setup if the hub exposes their battery.</p>`;
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
          ${job.device.product_type ? "(" + escapeHtml(job.device.product_type) + ")" : ""}</p>` : ""}
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
        <p>Confirm how Home Assistant reaches this hub on Wi‑Fi.</p>
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
          <li><span>Hub battery</span><strong>${hub ? escapeHtml(hub.battery_level + "% · " + hub.battery_state) : "Fail"}</strong></li>
          <li><span>Apple Watch</span><strong>${watch ? escapeHtml((watch.name || "Watch") + " · " + watch.battery_level + "% · " + watch.battery_state) : (v.error && v.error.includes("watch") ? "Not available" : "—")}</strong></li>
        </ul>
        ${v.error ? `<p class="hint">${escapeHtml(v.error)}</p>` : ""}
        <p class="hint">Unlock the hub and keep Wi‑Fi on if something failed, then retry.</p>`;
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
        <p>Pairing is saved. Battery values will refresh on the next poll.</p>
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
