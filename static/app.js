/* ── State ─────────────────────────────────────────────────────── */
let allAlbums = [];
let activeAlbumId = null;
let sourcesAbort = null;  // AbortController for in-flight source requests

/* ── DOM refs ─────────────────────────────────────────────────── */
const grid         = document.getElementById("album-grid");
const searchInput  = document.getElementById("search-input");
const filterSelect = document.getElementById("filter-select");
const rescanBtn    = document.getElementById("rescan-btn");
const drawer       = document.getElementById("drawer");
const overlay      = document.getElementById("drawer-overlay");
const drawerTitle  = document.getElementById("drawer-title");
const drawerClose  = document.getElementById("drawer-close");
const currentCover = document.getElementById("current-cover");
const currentMeta  = document.getElementById("current-meta");
const noCoverMsg   = document.getElementById("no-cover-msg");
const sourcesLoad  = document.getElementById("sources-loading");
const sourcesList  = document.getElementById("sources-list");
const noSourcesMsg = document.getElementById("no-sources-msg");
const albumCount   = document.getElementById("album-count");

/* ── Helpers ──────────────────────────────────────────────────── */
function qualityClass(sizeKb) {
  if (sizeKb === 0) return "";
  if (sizeKb < 500) return "low";
  if (sizeKb < 1024) return "medium";
  return "high";
}

function formatSize(kb) {
  if (kb === 0) return "—";
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`;
}

function formatRes(w, h) {
  if (!w || !h) return "";
  return `${w}\u00d7${h}`;
}

/* ── Load albums ──────────────────────────────────────────────── */
async function loadAlbums() {
  grid.innerHTML = `<div class="state-message">Loading...</div>`;
  try {
    const resp = await fetch("/api/albums");
    const data = await resp.json();
    if (data.scanning) {
      grid.innerHTML = `<div class="state-message">Scanning music library... Refresh in a moment.</div>`;
      setTimeout(loadAlbums, 2000);
      return;
    }
    allAlbums = data.albums;
    albumCount.textContent = `${allAlbums.length} albums`;
    renderGrid();
  } catch (e) {
    grid.innerHTML = `<div class="state-message">Failed to load albums.</div>`;
  }
}

/* ── Render grid ──────────────────────────────────────────────── */
function renderGrid() {
  const query  = searchInput.value.toLowerCase().trim();
  const filter = filterSelect.value;

  const filtered = allAlbums.filter(a => {
    if (query && !a.name.toLowerCase().includes(query)) return false;
    if (filter === "low")     return a.has_cover && a.cover_size_kb < 500;
    if (filter === "medium")  return a.has_cover && a.cover_size_kb >= 500 && a.cover_size_kb < 1024;
    if (filter === "high")    return a.has_cover && a.cover_size_kb >= 1024;
    if (filter === "missing") return !a.has_cover;
    return true;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `<div class="state-message">No albums match your filters.</div>`;
    return;
  }

  grid.innerHTML = filtered.map(a => {
    const q = qualityClass(a.cover_size_kb);
    const isActive = a.id === activeAlbumId ? " active" : "";
    const meta = a.has_cover
      ? `${formatRes(a.cover_width, a.cover_height)} &middot; ${formatSize(a.cover_size_kb)}`
      : "No cover";
    return `
      <div class="album-card${isActive}" data-id="${a.id}">
        <div class="cover-wrap">
          ${a.has_cover
            ? `<img src="/api/albums/${a.id}/cover" alt="${esc(a.name)}" loading="lazy">`
            : `<div class="placeholder-icon">&#9835;</div>`}
          ${q ? `<span class="quality-dot ${q}"></span>` : ""}
        </div>
        <div class="card-info">
          <span class="album-name" title="${esc(a.name)}">${esc(a.name)}</span>
          <span class="album-meta">${meta}</span>
        </div>
      </div>`;
  }).join("");
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ── Drawer ───────────────────────────────────────────────────── */
function openDrawer(albumId) {
  const album = allAlbums.find(a => a.id === albumId);
  if (!album) return;

  activeAlbumId = albumId;
  renderGrid();  // update active highlight

  drawerTitle.textContent = album.name;

  // Current cover
  if (album.has_cover) {
    currentCover.src = `/api/albums/${albumId}/cover?t=${Date.now()}`;
    currentCover.classList.remove("hidden");
    noCoverMsg.classList.add("hidden");

    const q = qualityClass(album.cover_size_kb);
    currentMeta.innerHTML = `
      <span class="meta-badge">${formatRes(album.cover_width, album.cover_height)}</span>
      <span class="meta-badge ${q ? 'quality-' + q : ''}">${formatSize(album.cover_size_kb)}</span>
    `;
    currentMeta.classList.remove("hidden");
  } else {
    currentCover.classList.add("hidden");
    currentMeta.classList.add("hidden");
    noCoverMsg.classList.remove("hidden");
  }

  // Reset sources
  sourcesList.innerHTML = "";
  noSourcesMsg.classList.add("hidden");
  sourcesLoad.classList.remove("hidden");

  // Open the drawer
  drawer.classList.add("open");
  overlay.classList.add("visible");
  document.body.classList.add("drawer-open");

  // Fetch sources async
  loadSources(albumId);
}

function closeDrawer() {
  drawer.classList.remove("open");
  overlay.classList.remove("visible");
  document.body.classList.remove("drawer-open");
  activeAlbumId = null;

  // Cancel in-flight source request
  if (sourcesAbort) {
    sourcesAbort.abort();
    sourcesAbort = null;
  }

  renderGrid();
}

async function loadSources(albumId) {
  // Cancel any previous request
  if (sourcesAbort) sourcesAbort.abort();
  sourcesAbort = new AbortController();

  try {
    const resp = await fetch(`/api/albums/${albumId}/sources`, { signal: sourcesAbort.signal });
    const data = await resp.json();
    sourcesLoad.classList.add("hidden");

    if (!data.sources || data.sources.length === 0) {
      noSourcesMsg.classList.remove("hidden");
      return;
    }

    sourcesList.innerHTML = data.sources.map(src => {
      const images = src.images.map(img => `
        <div class="source-image">
          <img src="${esc(img.thumbnail_url)}" alt="thumbnail" loading="lazy">
          <div class="source-image-info">
            <div class="detail">${esc(img.source_detail || img.label)}</div>
            <div class="sub-detail">${esc(img.type)} &middot; ${esc(img.label)}</div>
          </div>
          <button class="use-btn" data-album="${albumId}" data-url="${esc(img.url)}">Use</button>
        </div>
      `).join("");

      return `
        <div class="source-group">
          <div class="source-group-header">${esc(src.source)}</div>
          ${images}
        </div>
      `;
    }).join("");

  } catch (e) {
    if (e.name !== "AbortError") {
      sourcesLoad.classList.add("hidden");
      noSourcesMsg.textContent = "Failed to load sources.";
      noSourcesMsg.classList.remove("hidden");
    }
  }
}

async function replaceCover(btn, albumId, url) {
  btn.disabled = true;
  btn.classList.add("replacing");
  btn.textContent = "Downloading...";

  try {
    const resp = await fetch(`/api/albums/${albumId}/replace`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url}),
    });
    const data = await resp.json();

    if (data.ok) {
      btn.classList.remove("replacing");
      btn.classList.add("done");
      btn.textContent = "Done!";

      // Update local state
      const album = allAlbums.find(a => a.id === albumId);
      if (album) {
        album.has_cover = true;
        album.cover_size_kb = data.cover_size_kb;
        album.cover_width = data.cover_width;
        album.cover_height = data.cover_height;
      }

      // Refresh current cover in drawer
      currentCover.src = `/api/albums/${albumId}/cover?t=${Date.now()}`;
      currentCover.classList.remove("hidden");
      noCoverMsg.classList.add("hidden");
      const q = qualityClass(data.cover_size_kb);
      currentMeta.innerHTML = `
        <span class="meta-badge">${formatRes(data.cover_width, data.cover_height)}</span>
        <span class="meta-badge ${q ? 'quality-' + q : ''}">${formatSize(data.cover_size_kb)}</span>
      `;
      currentMeta.classList.remove("hidden");

      renderGrid();

      setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove("done");
        btn.textContent = "Use";
      }, 3000);
    } else {
      btn.textContent = "Failed";
      btn.classList.remove("replacing");
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "Use";
      }, 2000);
    }
  } catch (e) {
    btn.textContent = "Error";
    btn.classList.remove("replacing");
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Use";
    }, 2000);
  }
}

/* ── Event listeners ──────────────────────────────────────────── */
grid.addEventListener("click", (e) => {
  const card = e.target.closest(".album-card");
  if (card) openDrawer(card.dataset.id);
});

drawerClose.addEventListener("click", closeDrawer);
overlay.addEventListener("click", closeDrawer);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

sourcesList.addEventListener("click", (e) => {
  const btn = e.target.closest(".use-btn");
  if (btn && !btn.disabled) {
    replaceCover(btn, btn.dataset.album, btn.dataset.url);
  }
});

searchInput.addEventListener("input", renderGrid);
filterSelect.addEventListener("change", renderGrid);

rescanBtn.addEventListener("click", async () => {
  rescanBtn.disabled = true;
  rescanBtn.textContent = "Scanning...";
  try {
    await fetch("/api/rescan", {method: "POST"});
    await loadAlbums();
  } finally {
    rescanBtn.disabled = false;
    rescanBtn.textContent = "Rescan";
  }
});

/* ── Init ─────────────────────────────────────────────────────── */
loadAlbums();
