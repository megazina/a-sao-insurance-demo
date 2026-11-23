(() => {
  const $ = (id) => document.getElementById(id);
  const money = (n) => (n == null ? "-" : "$" + Number(n).toLocaleString("en-AU", { maximumFractionDigits: 2 }));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };

  let me = null, cfg = null, busy = false;
  let mode = store.get("ac_mode", "normal");
  let provider = store.get("ac_provider", "");
  let dests = null; // selected trace destinations (array of ids)

  async function api(path, opts) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch {}
      const e = new Error(detail); e.status = r.status; throw e;
    }
    return r.json();
  }

  const badgeClass = (s) => (s === "approved" || s === "active" ? "" : s === "declined" ? "red" : "amber");

  function renderPolicy(p) {
    const rows = [
      ["Policy", p.policy_id], ["Cover", money(p.cover_aud)], ["Excess", money(p.excess_aud)],
      ["Premium / yr", money(p.premium_aud)], ["Renews", p.renews],
    ];
    if (p.ncb_percent != null) rows.push(["No Claim Bonus", p.ncb_percent + "%"]);
    return `<article class="card policy">
      <div class="kind">${esc(p.type)} <span class="badge ${badgeClass(p.status)}">${esc(p.status)}</span></div>
      <h3>${esc(p.product)}</h3><div class="sub">${esc(p.subtitle)}</div>
      <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></article>`;
  }

  function renderClaims(claims) {
    if (!claims.length) return `<div class="card table-card"><div class="empty">No claims yet.</div></div>`;
    const rows = claims.map((c) => `<tr><td>${esc(c.claim_id)}</td><td>${esc(c.type)}</td><td>${esc(c.loss_date)}</td>
      <td>${money(c.claimed_aud)}</td><td><span class="badge ${badgeClass(c.status)}">${esc(c.status)}</span></td><td>${esc(c.note)}</td></tr>`).join("");
    return `<div class="card table-card"><table><thead><tr><th>Claim</th><th>Type</th><th>Loss date</th><th>Claimed</th><th>Status</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderPage() {
    $("page").innerHTML = `
      <div class="hello"><div><h1>G'day, ${esc(me.first_name)}</h1><p>${esc(me.tier)} member · ${esc(me.state)}</p></div>
        <button class="btn" id="lodgeBtn">Lodge a claim</button></div>
      <h2>Your policies</h2><div class="cards">${me.policies.map(renderPolicy).join("")}</div>
      <h2>Your claims</h2><div id="claimsBox">${renderClaims(me.claims)}</div>
      <h2>Your details</h2><div class="card details"><div class="name">${esc(me.name)}</div>
        <p>${esc(me.address)}</p><p>${esc(me.email)} · ${esc(me.phone)}</p></div>`;
    $("lodgeBtn").onclick = () => { openDrawer(); $("input").value = "I'd like to lodge a claim. "; $("input").focus(); };
  }

  function renderTopbar() {
    $("topbarRight").innerHTML = `<button class="user-btn" id="userBtn">${esc(me.name)} ▾</button><button class="signout" id="signoutBtn">Sign out</button>`;
    $("userBtn").onclick = async (e) => {
      e.stopPropagation();
      const existing = document.querySelector(".user-menu");
      if (existing) return existing.remove();
      const users = await api("/api/users");
      const menu = document.createElement("div");
      menu.className = "user-menu";
      menu.innerHTML = `<div class="hdr">Switch customer</div>` + users.map((u) =>
        `<button data-id="${esc(u.id)}">${esc(u.name)}<span class="sub">${esc(u.tier)} · ${esc(u.summary)}</span></button>`).join("");
      menu.onclick = (ev) => { const b = ev.target.closest("button"); if (b) signIn(b.dataset.id); };
      $("topbarRight").appendChild(menu);
    };
    $("signoutBtn").onclick = async () => { await api("/api/logout", { method: "POST" }); showLogin(); };
  }
  document.addEventListener("click", () => document.querySelector(".user-menu")?.remove());

  async function showLogin() {
    me = null; closeDrawer(true);
    $("fab").hidden = true; $("topbarRight").innerHTML = "";
    const users = await api("/api/users");
    $("page").innerHTML = `<div class="login"><h1>Sign in to AussieCare</h1><p>Pick a demo customer.</p>` +
      users.map((u) => `<button class="u" data-id="${esc(u.id)}"><b>${esc(u.name)}</b><span>${esc(u.tier)} member · ${esc(u.state)} · ${esc(u.summary)}</span></button>`).join("") + `</div>`;
    $("page").querySelectorAll(".u").forEach((b) => (b.onclick = () => signIn(b.dataset.id)));
  }

  async function signIn(id) {
    await api("/api/login", { method: "POST", body: JSON.stringify({ customer_id: id }) });
    await loadMe(true);
  }

  async function loadMe(fresh) {
    me = await api("/api/me");
    $("fab").hidden = false;
    renderTopbar(); renderPage();
    $("drawerSub").textContent = "session " + me.session;
    if (fresh) { $("messages").innerHTML = ""; greet(); }
  }

  function greet() {
    addMsg("bot", `G'day ${me.first_name}! I can help with your policies, claims, excess and renewals. What can I do for you?`);
  }

  function renderMd(text) {
    if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text, { breaks: true }));
    return "<p>" + esc(text).replace(/\n/g, "<br>") + "</p>";
  }

  function addMsg(kind, text, html) {
    const d = document.createElement("div");
    d.className = "msg " + kind;
    if (html) d.innerHTML = html; else if (kind === "bot") d.innerHTML = renderMd(text); else d.textContent = text;
    $("messages").appendChild(d);
    $("messages").scrollTop = $("messages").scrollHeight;
    return d;
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("send").disabled = true; $("input").value = "";
    openDrawer();
    addMsg("user", text);
    const wait = addMsg("bot", "", `<span class="typing">Looking that up…</span>`);
    try {
      const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text, mode, provider: provider || null, destinations: dests }) });
      wait.innerHTML = renderMd(res.answer || "(no answer)");
      me = { ...(await api("/api/me")) };
      $("claimsBox").innerHTML = renderClaims(me.claims);
    } catch (e) {
      if (e.status === 401) return showLogin();
      wait.className = "msg error"; wait.textContent = e.message;
    } finally {
      busy = false; $("send").disabled = false; $("messages").scrollTop = $("messages").scrollHeight;
    }
  }

  function openDrawer() { $("drawer").hidden = false; document.body.classList.add("drawer-open"); store.set("ac_drawer", "1"); }
  function closeDrawer(silent) { $("drawer").hidden = true; document.body.classList.remove("drawer-open"); if (!silent) store.set("ac_drawer", "0"); }

  function applyMode() {
    $("demoPill").hidden = mode !== "demo";
    $("modeLabel").textContent = "Mode: " + (mode === "demo" ? "Demo" : "Normal");
    $("modeDot").className = "dot" + (mode === "demo" ? " demo" : "");
    document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    if (cfg) {
      $("triggersTitle").textContent = mode === "demo" ? "Demo mode triggers" : "Normal mode triggers";
      $("triggers").innerHTML = cfg.demo_triggers
        .map((t, i) => `<button data-i="${i}">${esc(mode === "demo" ? t.label : t.normal_label || t.label)}</button>`).join("");
    }
    $("modeNote").textContent = mode === "demo"
      ? "Demo: scripted failures fire when a question matches a trigger below (other questions answer normally)."
      : "Normal: guardrails on, answers are grounded in the policy documents.";
  }

  async function init() {
    cfg = await api("/api/config");
    $("providerSelect").innerHTML = cfg.providers.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
    if (!cfg.providers.length) $("providerSelect").hidden = true;
    provider = cfg.providers.some((p) => p.id === provider) ? provider : cfg.default_provider || "";
    $("providerSelect").value = provider;
    $("providerSelect").onchange = (e) => { provider = e.target.value; store.set("ac_provider", provider); };
    $("triggers").onclick = (e) => { const b = e.target.closest("button"); if (b) { $("presenterPanel").hidden = true; send(cfg.demo_triggers[b.dataset.i].prompt.replace("{policy_id}", (me.policies.find((p) => p.type === "motor") || me.policies[0]).policy_id)); } };
    let saved = null;
    try { saved = JSON.parse(store.get("ac_dests", "null")); } catch {}
    const ids = cfg.destinations.map((d) => d.id);
    dests = Array.isArray(saved) ? saved.filter((d) => ids.includes(d)) : cfg.destinations.filter((d) => d.default).map((d) => d.id);
    $("dests").innerHTML = cfg.destinations.length
      ? cfg.destinations.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" ${dests.includes(d.id) ? "checked" : ""}> ${esc(d.label)}</label>`).join("")
      : "<span>No trace destination configured</span>";
    const syncDests = () => {
      dests = [...$("dests").querySelectorAll("input:checked")].map((i) => i.value);
      store.set("ac_dests", JSON.stringify(dests));
      $("tracingInfo").textContent = dests.length ? "" : "Tracing off";
    };
    $("dests").onchange = syncDests;
    syncDests();
    $("presenterToggle").onclick = () => ($("presenterPanel").hidden = !$("presenterPanel").hidden);
    document.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; store.set("ac_mode", mode); applyMode(); }));
    $("resetBtn").onclick = async () => { await api("/api/reset", { method: "POST" }); if (me) await loadMe(false); };
    $("fab").onclick = openDrawer;
    $("closeDrawer").onclick = () => closeDrawer();
    $("composer").onsubmit = (e) => { e.preventDefault(); send($("input").value); };
    applyMode();
    try { await loadMe(true); if (store.get("ac_drawer", "0") === "1") openDrawer(); } catch (e) { if (e.status === 401) await showLogin(); else throw e; }
  }
  init();
})();
