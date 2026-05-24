"use strict";

/* ===== ユーティリティ ===================================================== */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* noop */ }
  if (!res.ok) throw new Error(data.error || `エラー (${res.status})`);
  return data;
}
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

function fmtBytes(b) {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(b) / Math.log(1024));
  return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + " " + u[i];
}

function fmtAgo(ts) {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 60)      return "たった今";
  if (s < 3600)    return Math.floor(s / 60) + "分前";
  if (s < 86400)   return Math.floor(s / 3600) + "時間前";
  if (s < 2592000) return Math.floor(s / 86400) + "日前";
  return Math.floor(s / 2592000) + "ヶ月前";
}

const VIDEO_EXT = ["mp4", "mkv", "webm", "mov"];
const isVideo = (name) =>
  VIDEO_EXT.includes((name.split(".").pop() || "").toLowerCase());

const STATUS_LABEL = {
  queued: "待機中", running: "取得中", stopping: "停止中",
  done: "完了", error: "エラー", stopped: "中断",
};

/* ===== 状態 =============================================================== */
const cards = new Map();   // folder -> card element
const jobEls = new Map();  // job id -> job element
let accountsByFolder = {};

/* ===== アカウントカード ==================================================== */
function buildCard(acct) {
  const el = document.createElement("article");
  el.className = "card";
  el.dataset.folder = acct.folder;
  el.innerHTML = `
    <div class="card-top">
      <div>
        <span class="platform-pill"></span>
        <div class="acct-name"></div>
        <div class="acct-handle"></div>
      </div>
      <div class="card-menu">
        <button class="icon-btn" data-act="open"   title="ギャラリーを開く">▦</button>
        <button class="icon-btn" data-act="remove" title="登録解除">✕</button>
      </div>
    </div>
    <div class="stats">
      <div class="stat v"><div class="num" data-f="videos">0</div><div class="lbl">Videos</div></div>
      <div class="stat"><div class="num" data-f="images">0</div><div class="lbl">Images</div></div>
      <div class="stat"><div class="num" data-f="total">0</div><div class="lbl">Total</div></div>
    </div>
    <div class="filmstrip"></div>
    <div class="card-meta">
      <span>最終更新 <b data-f="last">—</b></span>
      <span data-f="size">—</span>
    </div>
    <div class="progress" hidden>
      <div class="progress-row">
        <span class="live"><span class="dot"></span><span data-f="pstatus">取得中</span></span>
        <span><span class="got" data-f="got">0</span> 件取得</span>
      </div>
      <div class="bar"></div>
      <div class="now" data-f="now"></div>
    </div>
    <div class="actions"></div>`;
  el.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const folder = el.dataset.folder;
    if (act === "update") doUpdate(folder);
    else if (act === "open") openGallery(folder);
    else if (act === "remove") doRemove(folder);
    else if (act === "stop") doStop(el.dataset.jobId);
  });
  return el;
}

function renderFilmstrip(strip, acct) {
  const key = acct.preview.join("|") + ":" + acct.stats.total;
  if (strip.dataset.key === key) return;
  strip.dataset.key = key;
  strip.innerHTML = "";
  const total = acct.stats.total;
  const shown = total > 6 ? acct.preview.slice(0, 5) : acct.preview.slice(0, 6);
  shown.forEach((name) => {
    const f = document.createElement("div");
    f.className = "frame" + (isVideo(name) ? " vid" : "");
    const img = new Image();
    img.loading = "lazy";
    img.onload = () => img.classList.add("loaded");
    img.src = `/thumb?acct=${encodeURIComponent(acct.folder)}` +
              `&name=${encodeURIComponent(name)}`;
    f.appendChild(img);
    strip.appendChild(f);
  });
  if (total > 6) {
    const more = document.createElement("div");
    more.className = "frame more";
    more.textContent = "+" + (total - 5);
    strip.appendChild(more);
  }
  for (let i = shown.length + (total > 6 ? 1 : 0); i < 6; i++) {
    strip.appendChild(Object.assign(document.createElement("div"),
      { className: "frame" }));
  }
}

function updateCard(el, acct, job) {
  $(".platform-pill", el).textContent = acct.platform_label || acct.platform;
  $(".acct-name", el).textContent = acct.username;
  $(".acct-handle", el).textContent =
    (acct.url || "").replace(/^https?:\/\/(www\.)?/, "");
  for (const f of ["videos", "images", "total"]) {
    $(`[data-f="${f}"]`, el).textContent = acct.stats[f].toLocaleString();
  }
  $('[data-f="last"]', el).textContent =
    acct.stats.last_modified ? fmtAgo(acct.stats.last_modified) : "未取得";
  $('[data-f="size"]', el).textContent = fmtBytes(acct.stats.bytes);
  renderFilmstrip($(".filmstrip", el), acct);

  const running = job && ["queued", "running", "stopping"].includes(job.status);
  el.classList.toggle("running", !!running);
  $(".progress", el).hidden = !running;
  const actions = $(".actions", el);

  if (running) {
    el.dataset.jobId = job.id;
    $('[data-f="pstatus"]', el).textContent = STATUS_LABEL[job.status] || "取得中";
    $('[data-f="got"]', el).textContent = job.downloaded;
    $('[data-f="now"]', el).textContent =
      job.current ? "▸ " + job.current : "投稿一覧を取得中…";
    if (actions.dataset.mode !== "run") {
      actions.dataset.mode = "run";
      actions.innerHTML = `
        <button class="btn stop" data-act="stop">■ 停止</button>
        <button class="btn ghost" data-act="open">ギャラリー</button>`;
    }
  } else {
    const last = acct.last_added;
    const label = (last && last > 0)
      ? `⟳ 更新（前回 +${last}）` : "⟳ 更新（差分取得）";
    if (actions.dataset.mode !== "idle:" + label) {
      actions.dataset.mode = "idle:" + label;
      actions.innerHTML = `
        <button class="btn primary" data-act="update">${label}</button>
        <button class="btn ghost" data-act="open">ギャラリー</button>`;
    }
  }
}

/* ===== アクティビティ ===================================================== */
function renderJobs(jobs) {
  const box = $("#jobs");
  if (!jobs.length) {
    box.innerHTML = `<p class="side-empty mono">— 実行中のタスクはありません —</p>`;
    jobEls.clear();
    return;
  }
  if ($(".side-empty", box)) box.innerHTML = "";
  const seen = new Set();
  for (const j of jobs) {
    seen.add(j.id);
    let el = jobEls.get(j.id);
    if (!el) {
      el = document.createElement("div");
      el.className = "job";
      jobEls.set(j.id, el);
      box.prepend(el);
    }
    const dur = j.finished && j.started
      ? Math.round(j.finished - j.started) + "秒"
      : (j.started ? Math.round(Date.now() / 1000 - j.started) + "秒経過" : "");
    const msg = j.messages && j.messages.length
      ? `<div class="job-msg">${j.messages[j.messages.length - 1]}</div>` : "";
    el.innerHTML = `
      <div class="job-head">
        <span class="job-acct">@${j.username}</span>
        <span class="badge ${j.status}">${STATUS_LABEL[j.status] || j.status}</span>
      </div>
      <div class="job-body">
        新規取得 <span class="hl">${j.downloaded}</span> 件 ・ ${dur}<br>
        ${j.current ? "▸ " + j.current : "&nbsp;"}
        ${msg}
      </div>`;
  }
  for (const [id, el] of jobEls) {
    if (!seen.has(id)) { el.remove(); jobEls.delete(id); }
  }
}

/* ===== メイン描画 ========================================================= */
function render(state) {
  accountsByFolder = {};
  const jobByFolder = {};
  for (const j of state.jobs) {
    if (!jobByFolder[j.folder]) jobByFolder[j.folder] = j;
  }
  const box = $("#accounts");
  const seen = new Set();

  state.accounts.forEach((acct, i) => {
    accountsByFolder[acct.folder] = acct;
    seen.add(acct.folder);
    let el = cards.get(acct.folder);
    if (!el) {
      el = buildCard(acct);
      el.style.animationDelay = (i * 0.05) + "s";
      cards.set(acct.folder, el);
      box.appendChild(el);
    }
    updateCard(el, acct, jobByFolder[acct.folder]);
  });
  for (const [folder, el] of cards) {
    if (!seen.has(folder)) { el.remove(); cards.delete(folder); }
  }

  $("#accountCount").textContent = state.accounts.length;
  $("#emptyState").hidden = state.accounts.length > 0;
  renderJobs(state.jobs);
}

async function refresh() {
  try { render(await api("/api/state")); }
  catch (e) { /* ポーリング失敗は無視 */ }
}

/* ===== アクション ========================================================= */
async function doUpdate(folder) {
  try {
    await post("/api/download", { acct: folder });
    toast(`@${accountsByFolder[folder]?.username || folder} の取得を開始`);
    refresh();
  } catch (e) { toast(e.message, true); }
}

async function doStop(jobId) {
  if (!jobId) return;
  try { await post("/api/stop", { job_id: jobId }); toast("停止しました"); refresh(); }
  catch (e) { toast(e.message, true); }
}

let pendingRemove = null;

function doRemove(folder) {
  const acct = accountsByFolder[folder];
  if (!acct) return;
  pendingRemove = folder;
  $("#removeMsg").innerHTML =
    `<b>@${acct.username}</b> ・ ${acct.platform_label || acct.platform}<br>` +
    `保存済み ${acct.stats.total.toLocaleString()} 件 / ` +
    `${fmtBytes(acct.stats.bytes)}`;
  $("#removeModal").classList.remove("hidden");
}

function closeRemove() {
  pendingRemove = null;
  $("#removeModal").classList.add("hidden");
}

async function confirmRemove(purge) {
  const folder = pendingRemove;
  const name = accountsByFolder[folder]?.username || folder;
  closeRemove();
  if (!folder) return;
  try {
    const r = await post("/api/account/remove", { acct: folder, purge });
    if (purge) {
      toast(r.trashed
        ? `@${name} を解除し、データを trash/ へ移動しました`
        : `@${name} を解除しました（削除対象データなし）`);
    } else {
      toast(`@${name} を登録解除しました（ファイルは保持）`);
    }
    refresh();
  } catch (e) { toast(e.message, true); }
}

$("#removePurge").addEventListener("click", () => confirmRemove(true));
$("#removeUnreg").addEventListener("click", () => confirmRemove(false));
$("#removeCancel").addEventListener("click", closeRemove);
$("#removeModal").addEventListener("click", (e) => {
  if (e.target.id === "removeModal") closeRemove();
});

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#addInput");
  const val = input.value.trim();
  if (!val) return;
  try {
    await post("/api/account/add", { username: val });
    input.value = "";
    toast("アカウントを登録しました");
    refresh();
  } catch (e) { toast(e.message, true); }
});

/* ===== ギャラリー ========================================================= */
const GAL_BATCH = 60;
let galleryItems = [];
let galleryFolder = "";
let galleryFilter = "all";
let galleryFiltered = [];
let galleryShown = 0;

async function openGallery(folder) {
  galleryFolder = folder;
  galleryFilter = "all";
  const acct = accountsByFolder[folder];
  $("#galleryTitle").textContent = "@" + (acct ? acct.username : folder);
  $("#gallerySub").textContent = "読み込み中…";
  $("#galleryGrid").innerHTML = "";
  $$(".chip", $("#galleryFilters")).forEach(c =>
    c.classList.toggle("active", c.dataset.filter === "all"));
  $("#galleryModal").classList.remove("hidden");
  try {
    const r = await api(`/api/media?acct=${encodeURIComponent(folder)}`);
    galleryItems = r.items;
    applyGalleryFilter();
  } catch (e) {
    $("#gallerySub").textContent = "読み込み失敗";
    toast(e.message, true);
  }
}

function makeTile(it, index) {
  const tile = document.createElement("div");
  tile.className = "tile " + it.kind;
  tile.innerHTML =
    `<span class="tag">${it.kind === "video" ? "VIDEO" : "IMAGE"}</span>`;
  const im = new Image();
  im.loading = "lazy";
  im.decoding = "async";
  im.onload = () => im.classList.add("loaded");
  im.src = `/thumb?acct=${encodeURIComponent(galleryFolder)}` +
           `&name=${encodeURIComponent(it.name)}`;
  tile.appendChild(im);
  tile.addEventListener("click", () => openViewer(index));
  return tile;
}

function applyGalleryFilter() {
  galleryFiltered = galleryItems.filter(it =>
    galleryFilter === "all" || it.kind === galleryFilter);
  galleryShown = 0;
  const grid = $("#galleryGrid");
  grid.innerHTML = "";
  const vid = galleryItems.filter(i => i.kind === "video").length;
  const img = galleryItems.filter(i => i.kind === "image").length;
  $("#gallerySub").textContent =
    `動画 ${vid} ・ 画像 ${img} ・ 計 ${galleryItems.length} 件`;
  if (!galleryFiltered.length) {
    grid.innerHTML =
      `<p class="side-empty mono" style="grid-column:1/-1">— 該当なし —</p>`;
    return;
  }
  renderMoreTiles();
}

function renderMoreTiles() {
  const grid = $("#galleryGrid");
  const oldBtn = document.getElementById("galMore");
  if (oldBtn) oldBtn.remove();
  const start = galleryShown;
  const next = galleryFiltered.slice(start, start + GAL_BATCH);
  const frag = document.createDocumentFragment();
  next.forEach((it, i) => frag.appendChild(makeTile(it, start + i)));
  grid.appendChild(frag);
  galleryShown += next.length;
  const rest = galleryFiltered.length - galleryShown;
  if (rest > 0) {
    const btn = document.createElement("button");
    btn.id = "galMore";
    btn.className = "gal-more";
    btn.textContent = `▽ もっと表示（残り ${rest} 件）`;
    btn.onclick = renderMoreTiles;
    grid.appendChild(btn);
  }
}

$$(".chip", $("#galleryFilters")).forEach(chip => {
  chip.addEventListener("click", () => {
    galleryFilter = chip.dataset.filter;
    $$(".chip", $("#galleryFilters")).forEach(c =>
      c.classList.toggle("active", c === chip));
    applyGalleryFilter();
  });
});

$("#galleryClose").addEventListener("click", () =>
  $("#galleryModal").classList.add("hidden"));
$("#galleryModal").addEventListener("click", (e) => {
  if (e.target.id === "galleryModal") $("#galleryModal").classList.add("hidden");
});

/* ===== 単体ビューア（左右キー切替対応） =================================== */
let viewerIndex = -1;

function openViewer(index) {
  if (index < 0 || index >= galleryFiltered.length) return;
  viewerIndex = index;
  const item = galleryFiltered[index];
  const stage = $("#viewerStage");
  const url = `/file?acct=${encodeURIComponent(galleryFolder)}` +
              `&name=${encodeURIComponent(item.name)}`;
  stage.innerHTML = "";
  if (item.kind === "video") {
    const v = document.createElement("video");
    v.src = url; v.controls = true; v.autoplay = true;
    stage.appendChild(v);
  } else {
    const im = new Image();
    im.src = url;
    stage.appendChild(im);
  }
  const dl = document.createElement("a");
  dl.className = "viewer-dl";
  dl.href = url + "&download=1";
  dl.textContent = "⬇ ダウンロード";
  stage.appendChild(dl);

  $("#viewerCounter").textContent =
    `${index + 1} / ${galleryFiltered.length}`;
  $("#viewerPrev").disabled = index <= 0;
  $("#viewerNext").disabled = index >= galleryFiltered.length - 1;
  $("#viewer").classList.remove("hidden");
}

function viewerStep(delta) {
  const ni = viewerIndex + delta;
  if (ni < 0 || ni >= galleryFiltered.length) return;
  openViewer(ni);
  // 次バッチがまだ未描画なら追従して描画しておく
  if (ni >= galleryShown - 1) {
    while (galleryShown < galleryFiltered.length && galleryShown <= ni)
      renderMoreTiles();
  }
}

function closeViewer() {
  $("#viewer").classList.add("hidden");
  $("#viewerStage").innerHTML = "";
  viewerIndex = -1;
}

$("#viewerClose").addEventListener("click", closeViewer);
$("#viewerPrev").addEventListener("click", () => viewerStep(-1));
$("#viewerNext").addEventListener("click", () => viewerStep(1));
$("#viewer").addEventListener("click", (e) => {
  if (e.target.id === "viewer") closeViewer();
});

document.addEventListener("keydown", (e) => {
  const viewerOpen = !$("#viewer").classList.contains("hidden");
  if (e.key === "Escape") {
    if (!$("#removeModal").classList.contains("hidden")) closeRemove();
    else if (viewerOpen) closeViewer();
    else $("#galleryModal").classList.add("hidden");
  } else if (viewerOpen && e.key === "ArrowLeft") {
    e.preventDefault(); viewerStep(-1);
  } else if (viewerOpen && e.key === "ArrowRight") {
    e.preventDefault(); viewerStep(1);
  }
});

/* ===== 起動 =============================================================== */
refresh();
setInterval(refresh, 2000);
