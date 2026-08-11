// Point this at your backend. Change to your deployed API URL before shipping.
const API_BASE = "http://localhost:8000";

const STAGES = [
  { key: "downloading", label: "download" },
  { key: "transcribing", label: "transcribe" },
  { key: "detecting_highlights", label: "detect" },
  { key: "cutting_clips", label: "cut" },
  { key: "burning_captions", label: "caption" },
];

const el = (id) => document.getElementById(id);

const urlInput = el("urlInput");
const fileInput = el("fileInput");
const generateBtn = el("generateBtn");
const slateHint = el("slateHint");
const progressSection = el("progressSection");
const stageFrames = el("stageFrames");
const progressLabel = el("progressLabel");
const resultsSection = el("resultsSection");
const clipGrid = el("clipGrid");
const errorSection = el("errorSection");
const errorDetail = el("errorDetail");
const apiStatus = el("apiStatus");

let pollTimer = null;

// ---------------------------------------------------------------
// backend health check (just for the topbar status dot)
// ---------------------------------------------------------------
async function checkApiHealth() {
  try {
    const res = await fetch(`${API_BASE}/`, { method: "GET" });
    if (res.ok) {
      apiStatus.innerHTML = `<span class="dot live"></span> backend connected`;
    } else {
      throw new Error("bad status");
    }
  } catch {
    apiStatus.innerHTML = `<span class="dot down"></span> backend offline — start it at ${API_BASE}`;
  }
}
checkApiHealth();

// ---------------------------------------------------------------
// filmstrip progress rendering
// ---------------------------------------------------------------
function renderStageFrames() {
  stageFrames.innerHTML = STAGES.map(
    (s) => `<div class="stage-frame" data-stage="${s.key}">${s.label}</div>`
  ).join("");
}

function setActiveStage(statusKey) {
  const idx = STAGES.findIndex((s) => s.key === statusKey);
  document.querySelectorAll(".stage-frame").forEach((frame, i) => {
    frame.classList.remove("active", "done");
    if (idx === -1) return;
    if (i < idx) frame.classList.add("done");
    if (i === idx) frame.classList.add("active");
  });
}

const STATUS_TEXT = {
  queued: "queued…",
  downloading: "downloading video…",
  transcribing: "transcribing audio…",
  detecting_highlights: "finding the best moments…",
  cutting_clips: "cutting clips…",
  burning_captions: "burning in captions…",
  done: "done",
  failed: "failed",
};

// ---------------------------------------------------------------
// submit: url or upload
// ---------------------------------------------------------------
generateBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  const file = fileInput.files[0];

  if (!url && !file) {
    slateHint.textContent = "Paste a link or choose a file first.";
    slateHint.style.color = "var(--coral)";
    return;
  }

  resetUI();
  generateBtn.disabled = true;
  generateBtn.querySelector("span").textContent = "Starting…";

  try {
    let jobId;
    if (file) {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/api/jobs/upload`, { method: "POST", body: formData });
      if (!res.ok) throw new Error(await res.text());
      jobId = (await res.json()).job_id;
    } else {
      const res = await fetch(`${API_BASE}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) throw new Error(await res.text());
      jobId = (await res.json()).job_id;
    }

    progressSection.hidden = false;
    renderStageFrames();
    progressSection.scrollIntoView({ behavior: "smooth", block: "center" });
    pollJob(jobId);
  } catch (err) {
    showError(err.message || "Could not start the job. Is the backend running?");
    generateBtn.disabled = false;
    generateBtn.querySelector("span").textContent = "Generate clips";
  }
});

// ---------------------------------------------------------------
// poll job status
// ---------------------------------------------------------------
function pollJob(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
      if (!res.ok) throw new Error("Lost track of the job.");
      const data = await res.json();

      setActiveStage(data.status);
      progressLabel.textContent = STATUS_TEXT[data.status] || data.status;

      if (data.status === "done") {
        clearInterval(pollTimer);
        renderClips(data.clips);
        generateBtn.disabled = false;
        generateBtn.querySelector("span").textContent = "Generate clips";
      }

      if (data.status === "failed") {
        clearInterval(pollTimer);
        showError(data.error || "Something went wrong while processing your video.");
        generateBtn.disabled = false;
        generateBtn.querySelector("span").textContent = "Generate clips";
      }
    } catch (err) {
      clearInterval(pollTimer);
      showError(err.message);
      generateBtn.disabled = false;
      generateBtn.querySelector("span").textContent = "Generate clips";
    }
  }, 2500);
}

// ---------------------------------------------------------------
// render results
// ---------------------------------------------------------------
function renderClips(clips) {
  if (!clips || clips.length === 0) {
    showError("No clip-worthy moments were found in this video.");
    return;
  }

  currentClips = clips;
  resultsSection.hidden = false;
  clipGrid.innerHTML = clips
    .map((clip, i) => {
      const start = formatTime(clip.start);
      const end = formatTime(clip.end);
      const previewUrl = `${API_BASE}${clip.preview_url}`;
      return `
        <div class="clip-card">
          <div class="clip-thumb">
            <video class="clip-video" controls preload="metadata" playsinline>
              <source src="${previewUrl}" type="video/mp4">
              Your browser can't play this video inline — use the download button below.
            </video>
            <span class="clip-score">${clip.score}</span>
            <span class="clip-time">${start}–${end}</span>
          </div>
          <div class="clip-body">
            <p class="clip-title">${escapeHtml(clip.title)}</p>
            <p class="clip-reason">${escapeHtml(clip.reason || "")}</p>
            <div class="clip-actions">
              <button class="clip-edit" data-clip-index="${i}">Edit clip</button>
              <button class="clip-download" data-url="${API_BASE}${clip.download_url}">Download</button>
            </div>
          </div>
        </div>
      `;
    })
    .join("");

  clipGrid.querySelectorAll(".clip-download").forEach((btn) => {
    btn.addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = btn.dataset.url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    });
  });

  clipGrid.querySelectorAll(".clip-edit").forEach((btn) => {
    btn.addEventListener("click", () => {
      openEditor(currentClips[Number(btn.dataset.clipIndex)]);
    });
  });

  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------
// trim editor
// ---------------------------------------------------------------
let currentClips = [];
let editingClip = null;
let clipDurationSec = 0;
let trimStartPct = 0; // 0-100
let trimEndPct = 100;
let draggingHandle = null; // 'left' | 'right' | null

const editorSection = el("editorSection");
const editorVideo = el("editorVideo");
const editorFrames = el("editorFrames");
const trimRegion = el("trimRegion");
const trimLeft = el("trimLeft");
const trimRight = el("trimRight");
const trimReadout = el("trimReadout");
const captionToggle = el("captionToggle");
const exportBtn = el("exportBtn");
const editorStatus = el("editorStatus");
const editorClose = el("editorClose");

async function openEditor(clip) {
  editingClip = clip;
  clipDurationSec = clip.end - clip.start;
  trimStartPct = 0;
  trimEndPct = 100;
  captionToggle.dataset.on = "true";
  editorStatus.textContent = "";

  editorVideo.src = `${API_BASE}${clip.preview_url}`;
  editorSection.hidden = false;
  updateTrimUI();
  editorSection.scrollIntoView({ behavior: "smooth", block: "start" });

  editorFrames.innerHTML = `<p class="frames-loading">loading timeline…</p>`;
  try {
    const res = await fetch(`${API_BASE}${clip.thumbnails_url}`);
    if (!res.ok) throw new Error("Could not load timeline frames.");
    const data = await res.json();
    editorFrames.innerHTML = data.thumbnails
      .map((url) => `<img src="${API_BASE}${url}" class="editor-frame" alt="" />`)
      .join("");
  } catch (err) {
    editorFrames.innerHTML = `<p class="frames-loading">Couldn't load timeline preview — trimming still works.</p>`;
  }
}

function closeEditor() {
  editorSection.hidden = true;
  editorVideo.pause();
  editorVideo.src = "";
  editingClip = null;
}
editorClose.addEventListener("click", closeEditor);

function updateTrimUI() {
  trimRegion.style.left = `${trimStartPct}%`;
  trimRegion.style.right = `${100 - trimEndPct}%`;
  const startSec = (trimStartPct / 100) * clipDurationSec;
  const endSec = (trimEndPct / 100) * clipDurationSec;
  trimReadout.textContent = `${formatTime(startSec)} – ${formatTime(endSec)}`;
}

function pctFromEvent(e) {
  const rect = el("editorTimeline").getBoundingClientRect();
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const pct = ((clientX - rect.left) / rect.width) * 100;
  return Math.max(0, Math.min(100, pct));
}

[trimLeft, trimRight].forEach((handle) => {
  const start = (e) => {
    draggingHandle = handle === trimLeft ? "left" : "right";
    e.preventDefault();
  };
  handle.addEventListener("mousedown", start);
  handle.addEventListener("touchstart", start, { passive: false });
});

window.addEventListener("mousemove", handleDrag);
window.addEventListener("touchmove", handleDrag, { passive: false });
window.addEventListener("mouseup", () => (draggingHandle = null));
window.addEventListener("touchend", () => (draggingHandle = null));

function handleDrag(e) {
  if (!draggingHandle) return;
  const pct = pctFromEvent(e);
  if (draggingHandle === "left") {
    trimStartPct = Math.min(pct, trimEndPct - 2);
  } else {
    trimEndPct = Math.max(pct, trimStartPct + 2);
  }
  updateTrimUI();
}

captionToggle.addEventListener("click", () => {
  const isOn = captionToggle.dataset.on === "true";
  captionToggle.dataset.on = isOn ? "false" : "true";
});

exportBtn.addEventListener("click", async () => {
  if (!editingClip) return;
  exportBtn.disabled = true;
  exportBtn.textContent = "Exporting…";
  editorStatus.textContent = "";

  const startSec = (trimStartPct / 100) * clipDurationSec;
  const endSec = (trimEndPct / 100) * clipDurationSec;
  const captionsOn = captionToggle.dataset.on === "true";

  try {
    const res = await fetch(`${API_BASE}${editingClip.render_url}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: startSec, end: endSec, captions: captionsOn }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || "Export failed.");
    }
    const data = await res.json();
    editorVideo.src = `${API_BASE}${data.preview_url}`;
    editorStatus.innerHTML = `Exported. <a href="${API_BASE}${data.download_url}" download>Download this edit</a>`;
  } catch (err) {
    editorStatus.textContent = err.message;
    editorStatus.style.color = "var(--coral)";
  } finally {
    exportBtn.disabled = false;
    exportBtn.textContent = "Export edit";
  }
});

// caption style pills were replaced by a real per-clip caption toggle
// inside the trim editor (see openEditor / captionToggle below).

// ---------------------------------------------------------------
// helpers
// ---------------------------------------------------------------
function resetUI() {
  errorSection.hidden = true;
  resultsSection.hidden = true;
  progressSection.hidden = true;
  slateHint.style.color = "var(--fog-dim)";
  slateHint.textContent = "Works best with videos under 15 minutes.";
}

function showError(message) {
  errorSection.hidden = false;
  errorDetail.textContent = message;
  errorSection.scrollIntoView({ behavior: "smooth", block: "center" });
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// keep url input / file input mutually exclusive so the hint makes sense
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) {
    urlInput.value = "";
    urlInput.disabled = true;
    urlInput.placeholder = fileInput.files[0].name;
  }
});
