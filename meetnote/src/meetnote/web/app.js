"use strict";

// The page talks to Python through window.pywebview.api; Python sends events
// with window.meetnote.onEvents([[kind, data], ...]).

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const state = {
  current: null,        // id of the meeting shown
  recording: false,
  recordingId: null,
  since: null,          // Unix time the recording started
  mic: true,
  rows: new Map(),      // "seg:s0" -> transcript row
  showEcho: false,
  follow: true,         // keep the transcript scrolled to the newest line
  notesTimer: null,
  preview: false,
  tab: "notes",         // "notes" or "summary"
  summaryRaw: "",       // Markdown of the summary shown
  cards: new Map(),     // question id -> {el, raw}
};

const ASK_VIEW = window.MEETNOTE_VIEW === "ask";
if (ASK_VIEW) document.body.classList.add("view-ask");

// --- small helpers -------------------------------------------------------------

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function clock(w) {
  return new Date(w * 1000).toTimeString().slice(0, 8);
}

function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = String(m).padStart(2, "0"), ss = String(r).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function dateLabel(iso) {
  const d = new Date(iso);
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  return same ? `Today ${d.toTimeString().slice(0, 5)}`
              : `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${d.toTimeString().slice(0, 5)}`;
}

async function call(fn, ...args) {
  try {
    return await api()[fn](...args);
  } catch (err) {
    showBanner(`Something went wrong: ${err && err.message ? err.message : err}`);
    throw err;
  }
}

// --- Markdown, safely: HTML is escaped first; links must be http(s) ----------------

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2">$1</a>');
}

function markdown(src) {
  const out = [];
  let list = null;
  let para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  let listKey = null;  // kind plus class, so a task list never continues a plain list
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; listKey = null; } };
  const openList = (kind, cls) => {
    const k = `${kind}.${cls || ""}`;
    if (listKey !== k) { flushList(); out.push(cls ? `<${kind} class="${cls}">` : `<${kind}>`); list = kind; listKey = k; }
  };
  for (const raw of escapeHtml(src).split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); flushList();
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
    } else if ((m = line.match(/^\s*[-*]\s+\[( |x|X)\]\s+(.*)$/))) {
      flushPara(); openList("ul", "tasks");
      out.push(`<li><input type="checkbox" disabled${m[1].trim() ? " checked" : ""}> ${inline(m[2])}</li>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      flushPara(); openList("ul");
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara(); openList("ol");
      out.push(`<li>${inline(m[1])}</li>`);
    } else {
      flushList(); para.push(line);
    }
  }
  flushPara(); flushList();
  return out.join("\n");
}

document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[href]");
  if (a) { ev.preventDefault(); call("open_link", a.getAttribute("href")); }
});

// --- banner and status -----------------------------------------------------------

function showBanner(text, action, onAction) {
  $("banner-text").textContent = text;
  const button = $("banner-action");
  button.hidden = !action;
  button.textContent = action || "";
  button.onclick = onAction ? () => { hideBanner(); onAction(); } : null;
  $("banner").hidden = false;
}

function hideBanner() { $("banner").hidden = true; }
$("banner-close").onclick = hideBanner;

function setStatus(text) { $("status").textContent = text || ""; }

// --- top bar ---------------------------------------------------------------------

function updateBar() {
  $("start").hidden = state.recording;
  $("stop").hidden = !state.recording;
  $("timer").hidden = !state.recording;
  $("mic").textContent = state.mic ? "Mic on" : "Mic muted";
  $("mic").classList.toggle("off", !state.mic);
  $("mic").disabled = !state.recording;
  document.body.classList.toggle("recording", state.recording);
  if (!state.recording) { setLevel("me", 0); setLevel("others", 0); }
}

function setLevel(src, value) {
  $(`lvl-${src}`).style.width = `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;
}

setInterval(() => {
  if (state.recording && state.since) $("timer").textContent = duration(Date.now() / 1000 - state.since);
}, 500);

$("start").onclick = async () => {
  $("start").disabled = true;
  try { await call("start", $("title").value); } finally { $("start").disabled = false; }
};
$("stop").onclick = () => call("stop");
$("mic").onclick = () => call("set_mic", !state.mic);

$("title").addEventListener("change", async () => {
  if (state.current) {
    $("title").value = await call("rename", state.current, $("title").value);
    refreshSessions();
  }
});

// --- meetings list ---------------------------------------------------------------

async function refreshSessions() {
  const list = $("session-list");
  const sessions = await call("sessions");
  list.replaceChildren();
  if (!sessions.length) list.append(el("li", "empty", "No meetings yet."));
  for (const s of sessions) {
    const item = el("li", s.id === state.current ? "active" : "");
    item.append(el("span", "name", s.title), el("span", "when", dateLabel(s.created)));
    if (s.status !== "stopped") item.append(el("span", `badge ${s.status}`, s.status));
    item.onclick = () => openSession(s.id);
    list.append(item);
  }
}

// --- opening a meeting -----------------------------------------------------------

async function openSession(id) {
  await saveNotesNow();
  const s = await call("open", id);
  state.current = id;
  $("title").value = s.title;
  $("lines").replaceChildren();
  state.rows.clear();
  for (const r of s.lines) addLine(r, false);
  if (!s.lines.length) $("lines").append(el("p", "empty", s.recording ? "Listening… lines appear a second or two after each sentence." : "No transcript."));
  state.follow = true;
  scrollToEnd();
  $("notes").value = s.notes;
  $("notes").disabled = false;
  renderPreview();
  $("saved").textContent = "";
  renderQA(s.qa, s.title);
  setSummary(s.summary, s.summary_status === "pending" && !s.summary ? "pending" : null);
  $("tab-summary").classList.remove("new");
  $("transcript-note").textContent = s.transcribing && !s.recording ? "transcribing…" : "";
  updateEchoButton();
  hideBanner();
  if (s.status === "interrupted" && !state.recording) {
    showBanner("This meeting was interrupted before it was stopped. Everything up to then is saved.",
               "Resume recording", () => call("resume", id));
  }
  refreshSessions();
}

// --- transcript ------------------------------------------------------------------

const key = (r) => `${r.seg}:${r.s0}`;

function addLine(r, live = true) {
  if (state.rows.has(key(r))) return;
  const box = $("lines");
  const empty = box.querySelector(".empty");
  if (empty) empty.remove();
  const row = el("div", `line ${r.src}${r.echo ? " echo" : ""}`);
  row.dataset.w0 = r.w0;
  row.append(el("span", "time", clock(r.w0)), el("span", "who", r.src === "me" ? "Me" : "Others"), el("span", "text", r.text));
  let after = box.lastElementChild;
  while (after && parseFloat(after.dataset.w0) > r.w0) after = after.previousElementSibling;
  if (after && r.w0 - parseFloat(after.dataset.w0) > 120) {
    const gap = el("div", "gap", clock(r.w0));
    gap.dataset.w0 = r.w0 - 0.001;
    after.after(gap);
    after = gap;
  }
  if (after) after.after(row); else box.prepend(row);
  state.rows.set(key(r), row);
  if (live && state.follow) scrollToEnd();
  updateEchoButton();
}

function scrollToEnd() {
  const box = $("lines");
  box.scrollTop = box.scrollHeight;
  $("jump").hidden = true;
}

$("lines").addEventListener("scroll", () => {
  const box = $("lines");
  state.follow = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  $("jump").hidden = state.follow || !state.recording;
});
$("jump").onclick = () => { state.follow = true; scrollToEnd(); };

function updateEchoButton() {
  const n = $("lines").querySelectorAll(".line.echo").length;
  $("show-echo").hidden = n === 0;
  $("show-echo").textContent = state.showEcho ? `Hide ${n} echo line${n === 1 ? "" : "s"}` : `Show ${n} echo line${n === 1 ? "" : "s"}`;
  $("show-echo").title = "Lines your microphone picked up from the speakers";
}

$("show-echo").onclick = () => {
  state.showEcho = !state.showEcho;
  document.body.classList.toggle("show-echo", state.showEcho);
  updateEchoButton();
};

// --- notes -----------------------------------------------------------------------

$("notes").addEventListener("input", () => {
  $("saved").textContent = "editing";
  clearTimeout(state.notesTimer);
  state.notesTimer = setTimeout(saveNotesNow, 1000);
});

async function saveNotesNow() {
  if (!state.notesTimer || !state.current) return;
  clearTimeout(state.notesTimer);
  state.notesTimer = null;
  await call("save_notes", state.current, $("notes").value);
  $("saved").textContent = "saved";
}

function renderPreview() { $("notes-preview").innerHTML = markdown($("notes").value); }

function showNotesBody() {
  const notes = state.tab === "notes";
  $("notes").hidden = !notes || state.preview;
  $("notes-preview").hidden = !notes || !state.preview;
  $("summary-view").hidden = notes;
  $("preview-toggle").hidden = !notes;
  $("saved").hidden = !notes;
  $("preview-toggle").textContent = state.preview ? "Edit" : "Preview";
  $("tab-notes").classList.toggle("active", notes);
  $("tab-summary").classList.toggle("active", !notes);
}

$("preview-toggle").onclick = () => {
  state.preview = !state.preview;
  if (state.preview) renderPreview();
  showNotesBody();
};
$("tab-notes").onclick = () => { state.tab = "notes"; showNotesBody(); };
$("tab-summary").onclick = () => { state.tab = "summary"; $("tab-summary").classList.remove("new"); showNotesBody(); };

// --- summary ---------------------------------------------------------------------

const SUMMARY_STATE = {
  waiting: "Finishing the transcript; Claude writes the summary next.",
  writing: "Claude is writing the summary…",
  pending: "The summary is not written yet. It is retried when meetnote starts, or now with the button.",
  empty: "Nothing to summarize yet.",
};

function setSummary(text, status, reason) {
  state.summaryRaw = text || "";
  $("summary").innerHTML = text ? markdown(text) : '<p class="empty">The summary appears here after you press Stop.</p>';
  let note = status ? SUMMARY_STATE[status] || "" : (text ? "Written by Claude from the transcript and your notes." : "");
  if (status === "failed") note = `Not written: ${reason}`;
  $("summary-status").textContent = note;
  $("summary-copy").hidden = !text || status === "writing";
  $("summary-run").hidden = status === "waiting" || status === "writing";
  $("summary-run").textContent = text ? "Rewrite" : (status === "failed" ? "Try again" : "Write summary");
}

$("summary-run").onclick = () => { if (state.current) call("summarize", state.current); };

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

$("summary-copy").onclick = async () => {
  const ok = await copyText(state.summaryRaw);
  $("summary-status").textContent = ok ? "Copied as Markdown." : "Could not copy; select the text instead.";
};

// --- Ask Claude ------------------------------------------------------------------

function qaCard(qid, question) {
  let card = state.cards.get(qid);
  if (card) return card;
  const box = $("qa");
  const empty = box.querySelector(".empty");
  if (empty) empty.remove();
  const root = el("div", "qa-item");
  root.append(el("div", "q", question), el("div", "a markdown"), el("div", "qa-status"));
  box.append(root);
  card = { el: root, raw: "", frame: 0 };
  state.cards.set(qid, card);
  return card;
}

function renderAnswer(card) {
  card.frame = 0;
  card.el.querySelector(".a").innerHTML = markdown(card.raw);
  $("qa").scrollTop = $("qa").scrollHeight;
}

function setCardStatus(card, qid, text, failed) {
  const status = card.el.querySelector(".qa-status");
  status.replaceChildren(el("span", "", text));
  card.el.classList.toggle("failed", !!failed);
  if (failed) {
    const retry = el("button", "link", "Retry");
    retry.onclick = () => call("retry", state.current, qid);
    status.append(retry);
  }
}

function renderQA(items, title) {
  $("qa").replaceChildren();
  state.cards.clear();
  $("ask-meeting").textContent = title ? `· ${title}` : "";
  if (!items.length) {
    $("qa").append(el("p", "empty", "Ask about the meeting while it runs. Claude reads the last 10 minutes of transcript and your notes. Nothing is sent until you ask."));
  }
  for (const q of items) {
    const card = qaCard(q.id, q.question);
    card.raw = q.answer || "";
    renderAnswer(card);
    if (q.status === "answered") setCardStatus(card, q.id, q.seconds ? `${q.seconds} s` : "");
    else if (q.status === "failed") setCardStatus(card, q.id, q.reason, true);
    else setCardStatus(card, q.id, "No answer was received.", true);
  }
}

function sendQuestion(text) {
  const question = (text ?? $("question").value).trim();
  if (!question || !state.current) return;
  $("question").value = "";
  call("ask", state.current, question, $("whole").checked);
}

$("send").onclick = () => sendQuestion();
$("question").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendQuestion(); }
});
for (const chip of document.querySelectorAll(".chip")) chip.onclick = () => sendQuestion(chip.dataset.q);
$("popout").onclick = () => call("popout_ask");
document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey && ev.key.toLowerCase() === "k") { ev.preventDefault(); $("question").focus(); }
});

// --- closing during a recording and first-run setup ------------------------------

$("quit-keep").onclick = () => { $("confirm").hidden = true; call("minimize"); };
$("quit-stop").onclick = () => { $("confirm").hidden = true; call("quit", true); };

$("setup-download").onclick = () => {
  $("setup-download").disabled = true;
  $("setup-progress").textContent = "Starting the download…";
  call("download_models");
};

// --- events from Python ----------------------------------------------------------

const ASK_EVENTS = new Set(["answer_start", "answer_delta", "answer_done", "answer_failed", "viewing"]);

function handle(kind, d) {
  if (ASK_VIEW && !ASK_EVENTS.has(kind)) return;
  switch (kind) {
    case "viewing":
      if (ASK_VIEW) openQuestions(d.id);
      break;
    case "answer_start": {
      if (d.id !== state.current) break;
      const card = qaCard(d.qid, d.question);
      card.raw = "";
      renderAnswer(card);
      setCardStatus(card, d.qid, "Claude is answering…");
      break;
    }
    case "answer_delta": {
      const card = state.cards.get(d.qid);
      if (d.id !== state.current || !card) break;
      card.raw += d.text;
      if (!card.frame) card.frame = requestAnimationFrame(() => renderAnswer(card));
      break;
    }
    case "answer_done": {
      const card = state.cards.get(d.qid);
      if (d.id !== state.current || !card) break;
      card.raw = d.answer;
      renderAnswer(card);
      setCardStatus(card, d.qid, `${d.seconds} s`);
      break;
    }
    case "answer_failed": {
      const card = state.cards.get(d.qid);
      if (d.id !== state.current || !card) break;
      setCardStatus(card, d.qid, d.reason, true);
      break;
    }
    case "summary_state":
      if (d.id !== state.current) break;
      if (d.state === "writing") state.summaryBuffer = "";
      setSummary(d.state === "writing" ? "" : state.summaryRaw, d.state, d.reason);
      break;
    case "summary_delta":
      if (d.id !== state.current) break;
      state.summaryBuffer = (state.summaryBuffer || "") + d.text;
      $("summary").innerHTML = markdown(state.summaryBuffer);
      break;
    case "summary_done":
      if (d.id !== state.current) break;
      setSummary(d.summary, null);
      if (state.tab !== "summary") $("tab-summary").classList.add("new");
      break;
    case "levels":
      setLevel("me", d.me);
      setLevel("others", d.others);
      break;
    case "device":
      $(`dev-${d.src}`).textContent = d.name;
      $(`dev-${d.src}`).title = d.name;
      break;
    case "line":
      if (d.id === state.current) addLine(d);
      break;
    case "echo": {
      const row = state.rows.get(`${d.seg}:${d.s0}`);
      if (d.id === state.current && row) { row.classList.toggle("echo", d.echo); updateEchoButton(); }
      break;
    }
    case "recording":
      state.recording = true;
      state.recordingId = d.id;
      state.since = Date.now() / 1000;
      updateBar();
      openSession(d.id);
      break;
    case "adopted":
      showBanner("meetnote was restarted and continues the recording that kept running meanwhile.");
      init();
      break;
    case "stopped":
      state.recording = false;
      updateBar();
      setStatus("");
      refreshSessions();
      if (d.id === state.current) $("transcript-note").textContent = "finishing the transcript…";
      break;
    case "interrupted":
      refreshSessions();
      break;
    case "mic":
      state.mic = d.on;
      updateBar();
      break;
    case "lag":
      setStatus(d.seconds !== null && d.seconds > 8 ? `transcript ${Math.round(d.seconds)} s behind` : "");
      break;
    case "transcribed":
      if (d.id === state.current) $("transcript-note").textContent = "";
      refreshSessions();
      break;
    case "download": {
      const pct = d.total ? Math.floor((100 * d.have) / d.total) : 0;
      $("setup-progress").textContent = `${d.file}: ${(d.have / 1e6).toFixed(0)} of ${(d.total / 1e6).toFixed(0)} MB (${pct}%)`;
      break;
    }
    case "download_done":
      $("setup").hidden = true;
      $("start").disabled = false;
      break;
    case "download_failed":
      $("setup-progress").textContent = `The download failed: ${d.error}. Check the connection and try again; it resumes where it stopped.`;
      $("setup-download").disabled = false;
      break;
    case "confirm_quit":
      $("confirm").hidden = false;
      break;
  }
}

window.meetnote = {
  onEvents(batch) {
    for (const [kind, data] of batch) {
      try { handle(kind, data); } catch (err) { console.error(kind, err); }
    }
  },
};

// --- start-up ----------------------------------------------------------------------

async function openQuestions(id) {
  if (!id) return;
  const q = await call("questions", id);
  state.current = id;
  renderQA(q.qa, q.title);
}

async function init() {
  const st = await call("state");
  if (ASK_VIEW) {
    await openQuestions(st.viewing || st.session);
    $("question").focus();
    return;
  }
  state.recording = st.recording;
  state.recordingId = st.session;
  state.since = st.since;
  state.mic = st.mic;
  for (const src of ["me", "others"]) {
    $(`dev-${src}`).textContent = st.devices[src] || "";
    $(`dev-${src}`).title = st.devices[src] || "";
  }
  updateBar();
  const missing = st.models_missing.length > 0;
  $("setup").hidden = !missing;
  $("start").disabled = missing;
  if (st.downloading) { $("setup-download").disabled = true; }
  await refreshSessions();
  if (st.session) {
    await openSession(st.session);
  } else {
    const sessions = await call("sessions");
    const interrupted = sessions.find((s) => s.status === "interrupted");
    if (interrupted) await openSession(interrupted.id);
  }
}

window.addEventListener("pywebviewready", init);
