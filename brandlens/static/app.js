"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { token: sessionStorage.getItem("bl_token"), user: null, view: "queue", sub: null, ver: null, verData: null, sel: null, msg: null, err: null, filter: "all", editing: false, imgUrl: null, draft: null };
const STATUS = { checked: "Checked", changes_requested: "Changes requested", pending_manager: "With manager", pending_reviewer: "With reviewer", approved: "Approved", rejected: "Rejected" };
const CAT = { tone: "Tone", terminology: "Terminology", claim: "Claim", fact: "Fact", messaging: "Messaging", visual: "Visual", accessibility: "Accessibility", confidentiality: "Confidential" };

async function api(method, path, body) {
  const res = await fetch("/api" + path, { method, headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: "Bearer " + S.token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && path !== "/login") { logout(); throw new Error("Sign in again."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed.");
  return data;
}
function logout() { S.token = null; S.user = null; sessionStorage.removeItem("bl_token"); render(); }
function flash(msg, err) { S.msg = err ? null : msg; S.err = err ? msg : null; }
async function guard(fn) { try { S.err = null; await fn(); } catch (e) { flash(e.message, true); } render(); }
const when = (iso) => { try { return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; } };
const sevLabel = { high: "High", medium: "Medium", low: "Low" };

/* ---------------------------------------------------------------- render */
function render() {
  const root = $("#root");
  if (!S.token) { root.innerHTML = loginView(); bindLogin(); return; }
  if (!S.user) { api("GET", "/me").then((u) => { S.user = u; render(); }).catch(() => logout()); root.innerHTML = '<main class="muted">Loading…</main>'; return; }
  const u = S.user, staff = u.role !== "creator";
  const nav = [["queue", staff ? "Review queue" : "My pieces"], ["new", "New check"], ["insights", "Insights"], ...(staff ? [["audit", "Audit log"]] : []), ["kit", "Brand kit"]];
  root.innerHTML = `<div class="app"><nav class="side" aria-label="Main"><div class="brand">Brand<i>Lens</i></div>
    ${nav.map(([k, l]) => `<button class="nav" data-view="${k}" ${S.view === k || (k === "queue" && S.view === "detail") ? 'aria-current="page"' : ""}>${l}</button>`).join("")}
    <div class="who"><b>${esc(u.name)}</b>${esc(u.role)}<br><button class="btn ghost sm" id="logout">Sign out</button></div></nav>
    <main id="main" tabindex="-1">${S.err ? `<div class="err" role="alert">${esc(S.err)}</div>` : ""}${S.msg ? `<div class="note-ok" role="status">${esc(S.msg)}</div>` : ""}<div id="view"></div></main></div>`;
  root.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
  $("#logout").addEventListener("click", logout);
  S.msg = null;
  const v = $("#view");
  ({ queue: queueView, new: newView, detail: detailView, insights: insightsView, audit: auditView, kit: kitView }[S.view] || queueView)(v);
}
function go(view, keep) { S.view = view; S.err = null; if (!keep) { S.editing = false; S.sel = null; } render(); }

/* ----------------------------------------------------------------- login */
function loginView() {
  return `<div class="login card"><h1>BrandLens</h1><p class="muted">Check content against approved brand guidelines before it goes out.</p>
  <form id="lf"><label for="u">Username</label><input type="text" id="u" autocomplete="username" required>
  <label for="p">Password</label><input type="password" id="p" autocomplete="current-password" required>
  ${S.err ? `<div class="err" role="alert">${esc(S.err)}</div>` : ""}
  <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">Sign in</button></div></form>
  <h3 style="margin-top:22px">Demo accounts</h3><p class="small muted">Password for all: demo123. Sample data only.</p>
  <div class="accounts">${[["maya", "Maya, creator"], ["ravi", "Ravi, creator"], ["meera", "Meera, manager"], ["lena", "Lena, legal reviewer"]].map(([u, l]) => `<button class="btn" type="button" data-u="${u}">${l}</button>`).join("")}</div></div>`;
}
function bindLogin() {
  $("#lf").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await api("POST", "/login", { username: $("#u").value.trim(), password: $("#p").value }); S.token = r.token; S.user = r.user; sessionStorage.setItem("bl_token", r.token); S.err = null; S.view = "queue"; render(); }
    catch (er) { S.err = er.message; render(); }
  });
  document.querySelectorAll("[data-u]").forEach((b) => b.addEventListener("click", () => { $("#u").value = b.dataset.u; $("#p").value = "demo123"; $("#lf").requestSubmit(); }));
}

/* ----------------------------------------------------------------- queue */
async function queueView(v) {
  v.innerHTML = '<p class="muted">Loading…</p>';
  const list = await api("GET", "/submissions").catch((e) => { flash(e.message, true); return []; });
  const staff = S.user.role !== "creator";
  const mineStage = S.user.role === "manager" ? "pending_manager" : "pending_reviewer";
  const shown = list.filter((s) => S.filter === "all" || (S.filter === "mine" && (staff ? s.status === mineStage : ["checked", "changes_requested"].includes(s.status))) || S.filter === s.status);
  const filters = [["all", "All"], ["mine", staff ? "Waiting for me" : "Needs my action"], ["approved", "Approved"]];
  v.innerHTML = `<div class="top"><div><h1>${staff ? "Review queue" : "My pieces"}</h1><p class="muted">${list.length} piece${list.length === 1 ? "" : "s"} in your workspace</p></div><button class="btn primary" data-view2="new">New check</button></div>
  <div class="tabs">${filters.map(([k, l]) => `<button data-f="${k}" aria-pressed="${S.filter === k}">${l}</button>`).join("")}</div>
  ${shown.length ? `<table><thead><tr><th>Title</th><th>Channel</th><th>Status</th>${staff ? "<th>Creator</th>" : ""}<th>Open findings</th><th>Version</th><th>Updated</th></tr></thead><tbody>
  ${shown.map((s) => `<tr class="click" tabindex="0" data-id="${s.id}"><td><b>${esc(s.title)}</b><br><span class="small muted">${esc(s.fmt)}</span></td><td>${esc(s.channel)}</td><td><span class="pill ${s.status}">${STATUS[s.status]}</span>${s.requires_reviewer && !["approved", "rejected"].includes(s.status) ? '<br><span class="small muted">needs legal reviewer</span>' : ""}</td>${staff ? `<td>${esc(s.creator)}</td>` : ""}<td>${s.open_findings}${s.high_open ? ` <span class="chip pen">${s.high_open} high</span>` : ""}</td><td>v${s.version}</td><td class="small">${when(s.updated)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty card"><p><b>Nothing here yet.</b></p><p>${staff ? "Pieces appear here when creators send them for review." : "Start a new check to see what BrandLens finds in your draft."}</p></div>`}`;
  v.querySelector("[data-view2]").addEventListener("click", () => go("new"));
  v.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { S.filter = b.dataset.f; render(); }));
  v.querySelectorAll("tr.click").forEach((r) => { const open = () => openSub(+r.dataset.id); r.addEventListener("click", open); r.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); }); });
}
async function openSub(id) { await guard(async () => { S.sub = await api("GET", "/submissions/" + id); S.ver = S.sub.version; S.verData = null; S.sel = null; S.editing = false; await loadImage(); S.view = "detail"; }); }
async function loadImage() {
  if (S.imgUrl) { URL.revokeObjectURL(S.imgUrl); S.imgUrl = null; }
  if (S.sub && S.sub.fmt === "image") {
    const r = await fetch(`/api/submissions/${S.sub.id}/image?v=${S.ver}`, { headers: { Authorization: "Bearer " + S.token } });
    if (r.ok) S.imgUrl = URL.createObjectURL(await r.blob());
  }
}

/* ------------------------------------------------------------------- new */
const SAMPLE = "Meet AuroraSync, our REVOLUTIONARY file platform! It is the fastest way to share work, with 99.99% uptime, and it is trusted by 10,000 customers. Start your free trial today! Questions? Email maya@aurora-example.com.";
function newView(v) {
  const d = S.draft || (S.draft = { title: "", channel: "web", fmt: "text", content: "", image: null, imageName: "" });
  v.innerHTML = `<div class="top"><div><h1>New check</h1><p class="muted">Paste a draft or upload an image. BrandLens compares it with the approved brand kit and explains every finding.</p></div></div>
  <div class="card" style="max-width:860px"><label for="t" style="margin-top:0">Title</label><input type="text" id="t" maxlength="120" value="${esc(d.title)}">
  <div class="row" style="align-items:flex-end"><div style="flex:1;min-width:180px"><label for="ch">Where will it be published?</label><select id="ch">${["web", "social", "email", "press"].map((c) => `<option ${d.channel === c ? "selected" : ""}>${c}</option>`).join("")}</select></div>
  <div><label>Format</label><div class="seg" role="group" aria-label="Format">${[["text", "Text"], ["html", "HTML"], ["image", "Image"]].map(([k, l]) => `<button type="button" data-fmt="${k}" aria-pressed="${d.fmt === k}">${l}</button>`).join("")}</div></div></div>
  ${d.fmt === "image" ? `<label for="img">Image (PNG or JPEG, up to 6 MB)</label><input type="file" id="img" accept="image/png,image/jpeg">${d.imageName ? `<p class="small muted">Selected: ${esc(d.imageName)}</p>` : ""}` : ""}
  <label for="c">${d.fmt === "image" ? "Alt text or caption" : d.fmt === "html" ? "HTML source" : "Content"}</label><textarea id="c" ${d.fmt === "html" ? 'style="font-family:ui-monospace,Menlo,monospace;font-size:.92rem"' : ""}>${esc(d.content)}</textarea>
  <div class="row" style="margin-top:14px"><button class="btn primary" id="go">Check content</button><button class="btn" id="sample" type="button">Fill with a sample draft</button></div></div>`;
  const sync = () => { d.title = $("#t").value; d.channel = $("#ch").value; d.content = $("#c").value; };
  v.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", () => { sync(); d.fmt = b.dataset.fmt; render(); }));
  const img = $("#img");
  if (img) img.addEventListener("change", () => { const f = img.files[0]; if (!f) return; if (f.size > 6 * 1024 * 1024) { flash("Image is larger than 6 MB.", true); render(); return; } const r = new FileReader(); r.onload = () => { d.image = r.result; d.imageName = f.name; sync(); render(); }; r.readAsDataURL(f); });
  $("#sample").addEventListener("click", () => { d.title = d.title || "Product launch post"; d.fmt = d.fmt === "image" ? "text" : d.fmt; d.content = d.fmt === "html" ? `<h1>Aurora Sync launch</h1><h3>${SAMPLE}</h3><img src="launch.png"><p style="color:#aaaaaa">Read the details.</p><a href="/learn">click here</a>` : SAMPLE; render(); });
  $("#go").addEventListener("click", () => guard(async () => {
    sync();
    const s = await api("POST", "/submissions", { title: d.title, channel: d.channel, fmt: d.fmt, content: d.content, image: d.fmt === "image" ? d.image : null });
    S.draft = null; S.sub = s; S.ver = s.version; S.verData = null; S.sel = null; await loadImage(); S.view = "detail";
    S.msg = s.findings.length ? `Found ${s.findings.length} thing${s.findings.length === 1 ? "" : "s"} to look at.` : "No findings. This draft matches the brand kit.";
  }));
}

/* ---------------------------------------------------------------- detail */
function segments(content, findings) {
  const spans = findings.filter((f) => f.start != null).sort((a, b) => a.start - b.start);
  let out = "", pos = 0;
  for (const f of spans) {
    if (f.start < pos) continue;
    out += esc(content.slice(pos, f.start));
    const cls = f.decision === "accept" || f.decision === "edit" ? "done" : f.severity;
    out += `<mark class="${cls}${S.sel === f.fid ? " sel" : ""}" tabindex="0" data-fid="${f.fid}" aria-label="${esc(sevLabel[f.severity])} severity ${esc(CAT[f.category])} finding">${esc(content.slice(f.start, f.end)) || "(end)"}</mark>`;
    pos = f.end;
  }
  return out + esc(content.slice(pos));
}
function detailView(v) {
  const s = S.sub, u = S.user;
  if (!s) return go("queue");
  const isOwner = u.id === s.creator_id;
  const viewingOld = S.ver !== s.version;
  const data = viewingOld ? S.verData : { content: s.content, findings: s.findings };
  if (viewingOld && !data) { api("GET", `/submissions/${s.id}/versions/${S.ver}`).then((d) => { S.verData = d; render(); }); v.innerHTML = '<p class="muted">Loading version…</p>'; return; }
  const editable = isOwner && ["checked", "changes_requested"].includes(s.status) && !viewingOld;
  const fl = data.findings;
  const wholeDoc = fl.filter((f) => f.start == null);
  const accepted = fl.filter((f) => ["accept", "edit"].includes(f.decision)).length;
  const steps = [["checked", "Creator checks"], ["pending_manager", "Manager"], ...(s.requires_reviewer || s.status === "pending_reviewer" ? [["pending_reviewer", "Legal reviewer"]] : []), ["approved", "Approved"]];
  const order = steps.map((x) => x[0]);
  const cur = s.status === "changes_requested" ? "checked" : s.status;
  const flow = `<div class="flow" aria-label="Progress">${steps.map(([k, l], i) => `<div class="${k === cur ? "on" : order.indexOf(cur) > i ? "past" : ""}">${l}</div>`).join("")}</div>`;
  v.innerHTML = `<div class="top"><div><button class="btn sm" id="back">Back to ${u.role === "creator" ? "my pieces" : "queue"}</button>
    <h1 style="margin-top:10px">${esc(s.title)}</h1><p class="muted">${esc(s.fmt)} for ${esc(s.channel)} by ${esc(s.creator)} <span class="pill ${s.status}">${STATUS[s.status]}</span></p></div>
    <div class="vtabs" role="group" aria-label="Versions">${s.versions.map((x) => `<button data-v="${x.n}" aria-pressed="${S.ver === x.n}" title="${esc(x.note || "")}">v${x.n}</button>`).join("")}</div></div>
    ${flow}
    ${viewingOld ? `<div class="err" role="status">You are viewing version ${S.ver}, which is read-only. <button class="btn sm" data-v="${s.version}">Go to latest</button></div>` : ""}
    <div class="proof"><div><div class="legend" aria-hidden="true"><span class="l-high">High</span><span class="l-medium">Medium</span><span class="l-low">Low</span></div>
    <div class="sheet">${S.imgUrl && s.fmt === "image" ? `<img class="preview" src="${S.imgUrl}" alt="Uploaded image for review">` : ""}
    ${S.editing ? `<label for="ed" style="margin-top:0">Edit and re-check</label><textarea id="ed">${esc(data.content)}</textarea><div class="row" style="margin-top:10px"><button class="btn primary" id="saved">Save as v${s.version + 1} and re-check</button><button class="btn" id="cancel">Cancel</button></div>`
      : `<${s.fmt === "html" ? 'pre class="code"' : 'div class="body"'} id="doc"></${s.fmt === "html" ? "pre" : "div"}>`}
    </div>
    ${editable && !S.editing ? `<div class="row" style="margin-top:12px"><button class="btn" id="edit">Edit text</button>${accepted ? `<button class="btn primary" id="apply">Apply ${accepted} accepted fix${accepted === 1 ? "" : "es"}</button>` : ""}</div>` : ""}
    ${workflowPanel(s, isOwner, viewingOld)}
    ${historyPanel(s)}</div>
    <aside class="notes" aria-label="Findings"><h2>${fl.length ? `${fl.length} finding${fl.length === 1 ? "" : "s"}` : "No findings"}</h2>
    ${fl.length ? "" : '<div class="card muted">This version matches the approved brand kit.</div>'}
    ${wholeDoc.map((f) => noteHtml(f, editable)).join("")}${fl.filter((f) => f.start != null).map((f) => noteHtml(f, editable)).join("")}</aside></div>`;
  const doc = $("#doc", v);
  if (doc) doc.innerHTML = data.content.trim() ? segments(data.content, fl) : '<span class="muted">(no text)</span>';
  bindDetail(v, s, fl);
}
function noteHtml(f, editable) {
  const dec = f.decision;
  const fix = f.replacement != null
    ? `<div class="fix">${f.text && f.text.length < 160 ? `<del><code>${esc(f.text)}</code></del> → ` : ""}<ins><code>${esc(f.replacement)}</code></ins></div>`
    : f.append ? `<div class="fix">Add to the end: <ins>${esc(f.append)}</ins></div>` : "";
  return `<div class="fnote ${f.severity}${S.sel === f.fid ? " sel" : ""}${dec ? " decided" : ""}" data-note="${f.fid}">
  <div class="fhead"><span><b>${esc(CAT[f.category])}</b> · ${esc(sevLabel[f.severity])}</span><span>${Math.round(f.confidence * 100)}% sure</span></div>
  <div class="fmsg">${esc(f.message)}</div>
  ${f.suggestion ? `<div class="small">${esc(f.suggestion)}</div>` : ""}${fix}
  <div class="fsrc">Rule ${esc(f.rule_id)} · ${esc(f.source)}${f.needs_reviewer ? " · goes to legal review" : ""}</div>
  ${dec ? `<div style="margin-top:8px"><span class="chip ${dec === "dismiss" ? "warn" : "ok"}">${dec === "accept" ? "Fix accepted" : dec === "edit" ? "Edited" : "Dismissed"}${f.carried ? " (carried over)" : ""}</span>${f.reason ? `<div class="small" style="margin-top:4px">Reason: ${esc(f.reason)}</div>` : ""}${f.edited ? `<div class="small">Your text: <code>${esc(f.edited)}</code></div>` : ""}${editable ? ` <button class="btn sm" data-undo="${f.fid}">Undo</button>` : ""}</div>`
    : editable ? `<div class="acts">${f.replacement != null || f.append ? `<button class="btn sm primary" data-accept="${f.fid}">Accept fix</button>` : ""}${f.start != null ? `<button class="btn sm" data-editopen="${f.fid}">Edit</button>` : ""}<button class="btn sm" data-dismissopen="${f.fid}">Dismiss</button></div>
    <div class="subform" data-edit="${f.fid}"><label class="small" for="e-${f.fid}">Replace with</label><input type="text" id="e-${f.fid}" value="${esc(f.replacement ?? f.text ?? "")}"><button class="btn sm primary" data-editsave="${f.fid}">Use my text</button></div>
    <div class="subform" data-dismiss="${f.fid}"><label class="small" for="r-${f.fid}">Why does this stay as written? A reviewer will read this.</label><textarea id="r-${f.fid}"></textarea><button class="btn sm primary" data-dismisssave="${f.fid}">Dismiss with reason</button></div>` : ""}</div>`;
}
function workflowPanel(s, isOwner, old) {
  if (old) return "";
  const u = S.user;
  let html = "";
  if (isOwner && ["checked", "changes_requested"].includes(s.status)) {
    html += `<div class="card panel"><h3>Send for review</h3>${s.blockers.length ? `<ul class="small">${s.blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : `<p class="small muted">${s.requires_reviewer ? "This piece has claims or sensitive items, so it goes to a manager and then a legal reviewer." : "No sensitive items are open. A manager will approve it."}</p>`}
    <button class="btn primary" id="send" ${s.blockers.length ? "disabled" : ""}>Send for review</button></div>`;
  }
  const stage = { pending_manager: "manager", pending_reviewer: "reviewer" }[s.status];
  if (stage && u.role === stage && !isOwner) {
    html += `<div class="card panel"><h3>${stage === "manager" ? "Manager decision" : "Legal review decision"}</h3>
    ${s.requires_reviewer && stage === "manager" ? '<p class="small muted">Approving passes this to a legal reviewer because it has claims, facts or sensitive items.</p>' : ""}
    <label for="rc" style="margin-top:0">Comment (required unless approving)</label><textarea id="rc" style="min-height:70px;font-family:var(--ui)"></textarea>
    <div class="acts"><button class="btn primary" data-rv="approve">${stage === "manager" && s.requires_reviewer ? "Approve and pass to reviewer" : "Approve"}</button><button class="btn" data-rv="request_changes">Request changes</button>${stage === "manager" ? '<button class="btn" data-rv="escalate">Escalate to reviewer</button>' : ""}<button class="btn danger" data-rv="reject">Reject</button></div></div>`;
  } else if (["pending_manager", "pending_reviewer"].includes(s.status)) {
    html += `<div class="card panel muted">Waiting for the ${stage}.</div>`;
  }
  return html;
}
function historyPanel(s) {
  const ev = [...s.versions.map((x) => ({ t: x.created, h: `<b>v${x.n}</b> · ${esc(x.note || "")} · ${esc(x.author)}` })),
    ...s.reviews.map((r) => ({ t: r.created, h: `<b>${esc(r.user)}</b> (${esc(r.role)}) ${esc(r.action.replace("_", " "))} on v${r.version}${r.comment ? `: ${esc(r.comment)}` : ""}` })),
    ...s.comments.map((c) => ({ t: c.created, h: `<b>${esc(c.user)}</b> commented: ${esc(c.body)}` }))].sort((a, b) => a.t.localeCompare(b.t));
  return `<div class="card panel"><h3>History and comments</h3><ul class="timeline">${ev.map((e) => `<li><span class="small muted">${when(e.t)}</span><br>${e.h}</li>`).join("")}</ul>
  <div class="row" style="margin-top:8px"><input type="text" id="cm" placeholder="Add a comment" aria-label="Add a comment" maxlength="2000" style="flex:1"><button class="btn" id="addc">Comment</button></div></div>`;
}
function bindDetail(v, s, fl) {
  const act = (fn) => guard(async () => { await fn(); S.verData = null; });
  const refresh = async (p) => { S.sub = p; S.ver = p.version; S.verData = null; await loadImage(); };
  $("#back").addEventListener("click", () => go("queue"));
  v.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", async () => { S.ver = +b.dataset.v; S.verData = null; S.editing = false; S.sel = null; await loadImage(); render(); }));
  const select = (fid) => { S.sel = fid; v.querySelectorAll("mark").forEach((m) => m.classList.toggle("sel", m.dataset.fid === fid)); v.querySelectorAll(".fnote").forEach((n) => { n.classList.toggle("sel", n.dataset.note === fid); if (n.dataset.note === fid) n.scrollIntoView({ block: "nearest" }); }); };
  v.querySelectorAll("mark").forEach((m) => { m.addEventListener("click", () => select(m.dataset.fid)); m.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(m.dataset.fid); } }); });
  v.querySelectorAll(".fnote").forEach((n) => n.addEventListener("click", (e) => { if (e.target.closest("button,input,textarea")) return; select(n.dataset.note); const m = v.querySelector(`mark[data-fid="${n.dataset.note}"]`); if (m) m.scrollIntoView({ block: "center" }); }));
  const decide = (fid, body) => act(async () => { await refresh(await api("POST", `/submissions/${s.id}/findings/${fid}`, body)); });
  v.querySelectorAll("[data-accept]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.accept, { action: "accept" })));
  v.querySelectorAll("[data-undo]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.undo, { action: "undo" })));
  v.querySelectorAll("[data-editopen]").forEach((b) => b.addEventListener("click", () => { v.querySelector(`[data-edit="${b.dataset.editopen}"]`).classList.toggle("open"); }));
  v.querySelectorAll("[data-dismissopen]").forEach((b) => b.addEventListener("click", () => { v.querySelector(`[data-dismiss="${b.dataset.dismissopen}"]`).classList.toggle("open"); }));
  v.querySelectorAll("[data-editsave]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.editsave, { action: "edit", edited: $("#e-" + b.dataset.editsave).value })));
  v.querySelectorAll("[data-dismisssave]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.dismisssave, { action: "dismiss", reason: $("#r-" + b.dataset.dismisssave).value })));
  const apply = $("#apply"); if (apply) apply.addEventListener("click", () => act(async () => { await refresh(await api("POST", `/submissions/${s.id}/apply`, {})); flash("Fixes applied. Version " + S.sub.version + " was re-checked."); }));
  const edit = $("#edit"); if (edit) edit.addEventListener("click", () => { S.editing = true; render(); });
  const cancel = $("#cancel"); if (cancel) cancel.addEventListener("click", () => { S.editing = false; render(); });
  const saved = $("#saved"); if (saved) saved.addEventListener("click", () => act(async () => { await refresh(await api("POST", `/submissions/${s.id}/versions`, { content: $("#ed").value })); S.editing = false; flash("Saved as version " + S.sub.version + " and re-checked."); }));
  const send = $("#send"); if (send) send.addEventListener("click", () => act(async () => { await refresh(await api("POST", `/submissions/${s.id}/send`, {})); flash("Sent for review."); }));
  v.querySelectorAll("[data-rv]").forEach((b) => b.addEventListener("click", () => act(async () => { await refresh(await api("POST", `/submissions/${s.id}/review`, { action: b.dataset.rv, comment: $("#rc").value })); flash("Decision recorded: " + STATUS[S.sub.status] + "."); })));
  const addc = $("#addc"); if (addc) addc.addEventListener("click", () => act(async () => { const c = $("#cm").value; await refresh(await api("POST", `/submissions/${s.id}/comments`, { body: c })); }));
}

/* -------------------------------------------------------------- insights */
async function insightsView(v) {
  v.innerHTML = '<p class="muted">Loading…</p>';
  const d = await api("GET", "/insights").catch((e) => { flash(e.message, true); return null; });
  if (!d) return;
  const t = d.totals, max = Math.max(1, ...Object.values(d.by_category));
  const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
  v.innerHTML = `<div class="top"><div><h1>Insights</h1><p class="muted">Showing ${esc(d.scope)}. Data in the demo is sample data.</p></div></div>
  <div class="tiles"><div class="tile"><b>${t.pieces}</b>pieces checked</div><div class="tile"><b>${t.findings}</b>findings raised</div><div class="tile"><b>${pct(t.first_pass_clean_rate)}</b>first draft free of high-severity findings</div><div class="tile"><b>${t.avg_versions_to_approval ?? "–"}</b>average versions to approval</div></div>
  <div class="two"><div class="card"><h2 style="margin-bottom:12px">Findings by type</h2><div class="bars">${Object.entries(d.by_category).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<div class="bar"><span>${esc(CAT[k] || k)}</span><span class="track"><span class="fill" style="display:block;width:${(n / max) * 100}%"></span></span><b>${n}</b></div>`).join("") || '<p class="muted">No findings yet.</p>'}</div></div>
  <div class="card"><h2 style="margin-bottom:12px">Guidelines to clarify</h2>${d.unclear_guidelines.length ? d.unclear_guidelines.map((g) => `<div class="gap-card"><b>${esc(g.rule_id)}</b> · ${esc(g.source)}<br><span class="small">${esc(g.advice)}</span></div>`).join("") : '<p class="muted">No rule is being dismissed or rewritten often enough to suggest a gap. This needs at least 3 decisions per rule.</p>'}</div></div>
  <div class="card" style="margin-bottom:18px"><h2 style="margin-bottom:10px">Repeated mistakes</h2>${d.repeated_mistakes.length ? `<table><thead><tr><th>Creator</th><th>Rule</th><th>Times</th><th>Pieces</th></tr></thead><tbody>${d.repeated_mistakes.map((r) => `<tr><td>${esc(r.creator)}</td><td>${esc(r.rule_id)} <span class="small muted">${esc(CAT[r.category] || "")}</span></td><td>${r.count}</td><td>${r.pieces}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No creator has hit the same rule in two or more pieces.</p>'}</div>
  <div class="card"><h2 style="margin-bottom:10px">Rules by how often they fire</h2>${d.rules.length ? `<table><thead><tr><th>Rule</th><th>Guideline</th><th>Raised</th><th>Fix accepted</th><th>Edited</th><th>Dismissed</th></tr></thead><tbody>${d.rules.map((r) => `<tr><td>${esc(r.rule_id)}</td><td class="small">${esc(r.source)}</td><td>${r.raised}</td><td>${r.accepted}</td><td>${r.edited}</td><td>${r.dismissed}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No data yet.</p>'}</div>`;
}

/* ----------------------------------------------------------------- audit */
async function auditView(v) {
  v.innerHTML = '<p class="muted">Loading…</p>';
  const rows = await api("GET", "/audit").catch((e) => { flash(e.message, true); return null; });
  if (!rows) return;
  v.innerHTML = `<div class="top"><div><h1>Audit log</h1><p class="muted">Every sign-in, decision and review in this workspace, newest first.</p></div></div>
  <table><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Piece</th><th>Detail</th></tr></thead><tbody>${rows.map((r) => `<tr><td class="small">${when(r.ts)}</td><td>${esc(r.user)}</td><td>${esc(r.action.replaceAll("_", " "))}</td><td>${r.submission_id ?? ""}</td><td class="small">${esc(Object.entries(r.detail).map(([k, x]) => k + ": " + x).join(", "))}</td></tr>`).join("")}</tbody></table>`;
}

/* -------------------------------------------------------------- brand kit */
async function kitView(v) {
  v.innerHTML = '<p class="muted">Loading…</p>';
  const k = await api("GET", "/kit").catch((e) => { flash(e.message, true); return null; });
  if (!k) return;
  v.innerHTML = `<div class="top"><div><h1>Brand kit</h1><p class="muted">${esc(k.brand)}, version ${esc(k.version)}. Every finding cites one of these approved sources.</p></div></div>
  <div class="card" style="margin-bottom:16px"><h2 style="margin-bottom:10px">Palette</h2><div class="swatches">${k.palette.map((p) => `<div class="sw"><i style="background:${esc(p.hex)}"></i><b>${esc(p.name)}</b><br><span class="small muted">${esc(p.hex)}</span></div>`).join("")}</div></div>
  <div class="two"><div class="card"><h2 style="margin-bottom:10px">Approved terms</h2><table><thead><tr><th>Write</th><th>Not</th></tr></thead><tbody>${k.glossary.map((g) => `<tr><td><b>${esc(g.preferred)}</b></td><td class="small">${g.avoid.map(esc).join(", ")}</td></tr>`).join("")}</tbody></table></div>
  <div class="card"><h2 style="margin-bottom:10px">Approved facts</h2><table><thead><tr><th>Fact</th><th>Value</th><th>Valid until</th></tr></thead><tbody>${k.facts.map((f) => `<tr><td>${esc(f.label)}<br><span class="small muted">${esc(f.source)}</span></td><td>${esc(f.value)}</td><td class="small">${esc(f.valid_until)}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="card"><h2 style="margin-bottom:10px">Claim rules</h2><table><thead><tr><th>Rule</th><th>Status</th><th>Why</th></tr></thead><tbody>${k.claims.map((c) => `<tr><td>${esc(c.id)}<br><span class="small muted">${esc(c.section)}</span></td><td>${esc(c.status.replace("_", " "))}</td><td class="small">${esc(c.note)}</td></tr>`).join("")}</tbody></table></div>`;
}

render();
