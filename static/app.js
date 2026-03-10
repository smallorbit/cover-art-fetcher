/* ── State ─────────────────────────────────────────────────────── */
let allAlbums = [];
let activeAlbumId = null;
let sourcesAbort = null;  // AbortController for in-flight source requests
let lightboxItems = [];
let lightboxIndex = -1;

/* ── DOM refs ─────────────────────────────────────────────────── */
const grid            = document.getElementById("album-grid");
const searchInput     = document.getElementById("search-input");
const filterSelect    = document.getElementById("filter-select");
const rescanBtn       = document.getElementById("rescan-btn");
const drawer          = document.getElementById("drawer");
const overlay         = document.getElementById("drawer-overlay");
const drawerTitle     = document.getElementById("drawer-title");
const drawerClose     = document.getElementById("drawer-close");
const currentCover    = document.getElementById("current-cover");
const currentMeta     = document.getElementById("current-meta");
const noCoverMsg      = document.getElementById("no-cover-msg");
const sourcesLoad     = document.getElementById("sources-loading");
const sourcesList     = document.getElementById("sources-list");
const noSourcesMsg    = document.getElementById("no-sources-msg");
const albumCount      = document.getElementById("album-count");
const mediaList       = document.getElementById("media-list");
const noMediaMsg      = document.getElementById("no-media-msg");
const mediaCount      = document.getElementById("media-count");
const mbidInput       = document.getElementById("mbid-input");
const mbidSearchBtn   = document.getElementById("mbid-search-btn");
const mbidReleaseInfo = document.getElementById("mbid-release-info");
const currentMbid     = document.getElementById("current-mbid");
const lightbox        = document.getElementById("lightbox");
const lightboxImg     = document.getElementById("lightbox-img");
const lightboxPrev    = document.getElementById("lightbox-prev");
const lightboxNext    = document.getElementById("lightbox-next");
const lightboxCounter = document.getElementById("lightbox-counter");
const confirmModal    = document.getElementById("confirm-modal");
const confirmMessage  = document.getElementById("confirm-message");
const confirmCancel   = document.getElementById("confirm-cancel");
const confirmOk       = document.getElementById("confirm-ok");

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

  // Show MBID from tags
  if (album.mbid) {
    const label = document.createElement("span");
    label.className = "mbid-label";
    label.textContent = "MB ID: ";
    const value = document.createElement("span");
    value.className = "mbid-value";
    value.textContent = album.mbid;
    currentMbid.replaceChildren(label, value);
    currentMbid.className = "mbid-found";
  } else {
    currentMbid.textContent = "No MB ID found in music files";
    currentMbid.className = "mbid-missing";
  }

  // Pre-fill MBID search with tag value if available
  mbidInput.value = album.mbid || "";
  mbidInput.setCustomValidity("");
  mbidReleaseInfo.classList.add("hidden");
  mbidReleaseInfo.textContent = "";

  // Reset sources
  sourcesList.innerHTML = "";
  noSourcesMsg.classList.add("hidden");
  sourcesLoad.classList.remove("hidden");

  // Reset media
  mediaList.innerHTML = "";
  noMediaMsg.classList.add("hidden");
  mediaCount.textContent = "";

  // Open the drawer
  drawer.classList.add("open");
  overlay.classList.add("visible");
  document.body.classList.add("drawer-open");

  // Fetch sources and media async
  loadSources(albumId);
  loadMedia(albumId);
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

/* ── Sources ──────────────────────────────────────────────────── */
function renderSourcesList(sources, albumId) {
  if (!sources || sources.length === 0) {
    noSourcesMsg.classList.remove("hidden");
    return;
  }
  sourcesList.innerHTML = sources.map(src => {
    const images = src.images.map(img => {
      const res = img.width && img.height ? `${img.width}\u00d7${img.height}` : "";
      const size = img.size_kb > 0 ? formatSize(img.size_kb) : "";
      const metaParts = [res, size].filter(Boolean).join(" \u00b7 ");
      const matchBadge = img.match === "current"
        ? `<span class="match-badge">Current</span>`
        : "";
      const isCurrent = img.match === "current";
      return `
      <div class="source-image${isCurrent ? ' is-current' : ''}">
        <img src="${esc(img.thumbnail_url)}" data-fullurl="${esc(img.url)}" alt="thumbnail" loading="lazy">
        <div class="source-image-info">
          <div class="detail">${esc(img.source_detail || img.label)}${matchBadge}</div>
          <div class="sub-detail">${esc(img.type)}${metaParts ? ' \u00b7 ' + esc(metaParts) : ''}</div>
        </div>
        <div class="btn-group">
          <button class="use-btn" data-album="${albumId}" data-url="${esc(img.url)}">Use</button>
          <button class="save-btn" data-album="${albumId}" data-url="${esc(img.url)}" data-type="${esc(img.type)}">Save</button>
        </div>
      </div>`;
    }).join("");

    return `
      <div class="source-group">
        <div class="source-group-header">${esc(src.source)}</div>
        ${images}
      </div>
    `;
  }).join("");
}

function renderReleaseInfo(release) {
  const parts = [release.artist, release.album, release.year, release.label].filter(Boolean);
  mbidReleaseInfo.textContent = parts.join(" \u00b7 ");
  mbidReleaseInfo.classList.remove("hidden");
}

async function loadSources(albumId) {
  if (sourcesAbort) sourcesAbort.abort();
  sourcesAbort = new AbortController();

  try {
    const resp = await fetch(`/api/albums/${albumId}/sources`, { signal: sourcesAbort.signal });
    const data = await resp.json();
    sourcesLoad.classList.add("hidden");
    renderSourcesList(data.sources, albumId);
  } catch (e) {
    if (e.name !== "AbortError") {
      sourcesLoad.classList.add("hidden");
      noSourcesMsg.textContent = "Failed to load sources.";
      noSourcesMsg.classList.remove("hidden");
    }
  }
}

async function loadSourcesByMbid(albumId, mbid) {
  if (sourcesAbort) sourcesAbort.abort();
  sourcesAbort = new AbortController();

  sourcesList.innerHTML = "";
  noSourcesMsg.classList.add("hidden");
  mbidReleaseInfo.classList.add("hidden");
  sourcesLoad.classList.remove("hidden");

  try {
    const resp = await fetch(`/api/mbid/${encodeURIComponent(mbid)}/sources`, { signal: sourcesAbort.signal });
    const data = await resp.json();
    sourcesLoad.classList.add("hidden");

    if (!resp.ok) {
      noSourcesMsg.textContent = data.error || "Release not found.";
      noSourcesMsg.classList.remove("hidden");
      return;
    }

    if (data.release) renderReleaseInfo(data.release);
    renderSourcesList(data.sources, albumId);
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

/* ── Media assets ─────────────────────────────────────────────── */
async function loadMedia(albumId) {
  try {
    const resp = await fetch(`/api/albums/${albumId}/media`);
    const data = await resp.json();
    const files = (data.files || []).sort((a, b) => b.size_kb - a.size_kb);
    if (files.length === 0) {
      noMediaMsg.classList.remove("hidden");
      mediaCount.textContent = "";
      return;
    }
    noMediaMsg.classList.add("hidden");
    mediaCount.textContent = files.length;

    mediaList.innerHTML = "";
    for (const f of files) {
      const res = f.width && f.height ? formatRes(f.width, f.height) : "";
      const size = f.size_kb > 0 ? formatSize(f.size_kb) : "";
      const meta = [res, size].filter(Boolean).join(" \u00b7 ");

      const row = document.createElement("div");
      row.className = "media-file";

      const img = document.createElement("img");
      img.src = `/api/albums/${albumId}/media/${encodeURIComponent(f.filename)}`;
      img.alt = f.filename;
      img.loading = "lazy";

      const info = document.createElement("div");
      info.className = "source-image-info";
      const detail = document.createElement("div");
      detail.className = "detail";
      detail.textContent = f.filename;
      const subDetail = document.createElement("div");
      subDetail.className = "sub-detail";
      subDetail.textContent = meta;
      info.append(detail, subDetail);

      const useBtn = document.createElement("button");
      useBtn.className = "use-btn";
      useBtn.textContent = "Use";
      useBtn.dataset.album = albumId;
      useBtn.dataset.filename = f.filename;

      const delBtn = document.createElement("button");
      delBtn.className = "delete-btn";
      delBtn.textContent = "Delete";
      delBtn.dataset.album = albumId;
      delBtn.dataset.filename = f.filename;

      const btnGroup = document.createElement("div");
      btnGroup.className = "btn-group";
      btnGroup.append(useBtn, delBtn);

      row.append(img, info, btnGroup);
      mediaList.appendChild(row);
    }
  } catch (e) {
    noMediaMsg.textContent = "Failed to load media.";
    noMediaMsg.classList.remove("hidden");
  }
}

/* ── Confirm modal ────────────────────────────────────────────── */
let _confirmResolve = null;

function showConfirm(message) {
  confirmMessage.textContent = message;
  confirmModal.classList.remove("hidden");
  return new Promise(resolve => { _confirmResolve = resolve; });
}

confirmCancel.addEventListener("click", () => {
  confirmModal.classList.add("hidden");
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
});

confirmOk.addEventListener("click", () => {
  confirmModal.classList.add("hidden");
  if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
});

async function useMediaAsCover(btn, albumId, filename) {
  btn.disabled = true;
  btn.classList.add("replacing");
  btn.textContent = "Applying...";

  try {
    const resp = await fetch(`/api/albums/${albumId}/use-media`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({filename}),
    });
    const data = await resp.json();

    if (data.ok) {
      btn.classList.remove("replacing");
      btn.classList.add("done");
      btn.textContent = "Done!";

      const album = allAlbums.find(a => a.id === albumId);
      if (album) {
        album.has_cover = true;
        album.cover_size_kb = data.cover_size_kb;
        album.cover_width = data.cover_width;
        album.cover_height = data.cover_height;
      }

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
      loadMedia(albumId);
    } else {
      btn.textContent = "Failed";
      btn.classList.remove("replacing");
      setTimeout(() => { btn.disabled = false; btn.textContent = "Use"; }, 2000);
    }
  } catch (e) {
    btn.textContent = "Error";
    btn.classList.remove("replacing");
    setTimeout(() => { btn.disabled = false; btn.textContent = "Use"; }, 2000);
  }
}

async function deleteMediaFile(btn, albumId, filename) {
  const confirmed = await showConfirm(`Delete "${filename}"? This cannot be undone.`);
  if (!confirmed) return;

  btn.disabled = true;
  try {
    const resp = await fetch(`/api/albums/${albumId}/media/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    });
    if (resp.ok) {
      loadMedia(albumId);
    } else {
      btn.disabled = false;
    }
  } catch (e) {
    btn.disabled = false;
  }
}

async function saveToMedia(btn, albumId, url, type) {
  btn.disabled = true;
  btn.classList.add("replacing");
  btn.textContent = "Saving...";

  try {
    const resp = await fetch(`/api/albums/${albumId}/save-media`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, type}),
    });
    const data = await resp.json();

    if (data.ok) {
      btn.classList.remove("replacing");
      btn.classList.add("done");
      btn.textContent = "Saved!";
      // Refresh the media list
      loadMedia(albumId);
      setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove("done");
        btn.textContent = "Save";
      }, 3000);
    } else {
      btn.textContent = "Failed";
      btn.classList.remove("replacing");
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "Save";
      }, 2000);
    }
  } catch (e) {
    btn.textContent = "Error";
    btn.classList.remove("replacing");
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Save";
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
  if (e.key === "Escape") {
    if (!lightbox.classList.contains("hidden")) closeLightbox();
    else closeDrawer();
  }
  if (!lightbox.classList.contains("hidden")) {
    if (e.key === "ArrowLeft")  navigateLightbox(-1);
    if (e.key === "ArrowRight") navigateLightbox(1);
  }
});

sourcesList.addEventListener("click", (e) => {
  const useBtn = e.target.closest(".use-btn");
  if (useBtn && !useBtn.disabled) {
    replaceCover(useBtn, useBtn.dataset.album, useBtn.dataset.url);
    return;
  }
  const saveBtn = e.target.closest(".save-btn");
  if (saveBtn && !saveBtn.disabled) {
    saveToMedia(saveBtn, saveBtn.dataset.album, saveBtn.dataset.url, saveBtn.dataset.type);
  }
});

searchInput.addEventListener("input", renderGrid);
filterSelect.addEventListener("change", renderGrid);

const _UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function triggerMbidSearch() {
  const mbid = mbidInput.value.trim();
  if (!_UUID_RE.test(mbid)) {
    mbidInput.setCustomValidity("Enter a valid MusicBrainz Release UUID");
    mbidInput.reportValidity();
    return;
  }
  mbidInput.setCustomValidity("");
  loadSourcesByMbid(activeAlbumId, mbid);
}

mbidSearchBtn.addEventListener("click", triggerMbidSearch);
mbidInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") triggerMbidSearch();
});
mbidInput.addEventListener("input", () => mbidInput.setCustomValidity(""));

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

/* ── Lightbox ─────────────────────────────────────────────────── */
function openLightbox(url, items = [], index = -1) {
  lightboxItems = items;
  lightboxIndex = index;
  lightboxImg.src = url;

  const hasNav = items.length > 1;
  lightboxPrev.classList.toggle("hidden", !hasNav);
  lightboxNext.classList.toggle("hidden", !hasNav);
  if (hasNav) {
    lightboxCounter.textContent = `${index + 1} / ${items.length}`;
    lightboxCounter.classList.remove("hidden");
  } else {
    lightboxCounter.classList.add("hidden");
  }

  lightbox.classList.remove("hidden");
}

function navigateLightbox(dir) {
  if (!lightboxItems.length) return;
  lightboxIndex = (lightboxIndex + dir + lightboxItems.length) % lightboxItems.length;
  lightboxImg.src = lightboxItems[lightboxIndex];
  lightboxCounter.textContent = `${lightboxIndex + 1} / ${lightboxItems.length}`;
}

function closeLightbox() {
  lightbox.classList.add("hidden");
  lightboxImg.src = "";
  lightboxItems = [];
  lightboxIndex = -1;
}

currentCover.addEventListener("click", () => {
  if (!currentCover.classList.contains("hidden")) openLightbox(currentCover.src);
});

lightbox.addEventListener("click", (e) => {
  if (!e.target.closest(".lightbox-nav")) closeLightbox();
});

lightboxPrev.addEventListener("click", (e) => { e.stopPropagation(); navigateLightbox(-1); });
lightboxNext.addEventListener("click", (e) => { e.stopPropagation(); navigateLightbox(1); });

mediaList.addEventListener("click", (e) => {
  const useBtn = e.target.closest(".use-btn");
  if (useBtn && !useBtn.disabled) {
    useMediaAsCover(useBtn, useBtn.dataset.album, useBtn.dataset.filename);
    return;
  }
  const delBtn = e.target.closest(".delete-btn");
  if (delBtn && !delBtn.disabled) {
    deleteMediaFile(delBtn, delBtn.dataset.album, delBtn.dataset.filename);
    return;
  }
  const img = e.target.closest(".media-file img");
  if (img) {
    const imgs = [...mediaList.querySelectorAll(".media-file img")];
    const index = imgs.indexOf(img);
    openLightbox(img.src, imgs.map(i => i.src), index);
  }
});

sourcesList.addEventListener("click", (e) => {
  const img = e.target.closest(".source-image img");
  if (img) {
    const group = img.closest(".source-group");
    const imgs = group ? [...group.querySelectorAll(".source-image img")] : [img];
    const index = imgs.indexOf(img);
    openLightbox(img.dataset.fullurl, imgs.map(i => i.dataset.fullurl), index);
  }
});

/* ── Init ─────────────────────────────────────────────────────── */
loadAlbums();
