const LOCAL_API_HOST = ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ? window.location.hostname
  : "localhost";
const requestedApiPort = new URL(window.location.href).searchParams.get("api_port");
const API_PORT = /^\d{2,5}$/.test(requestedApiPort || "") ? requestedApiPort : "8000";
const API_BASE = `http://${LOCAL_API_HOST}:${API_PORT}`;

const STAGES = [
  { key: "downloading", label: "download" },
  { key: "transcribing", label: "transcribe" },
  { key: "detecting_highlights", label: "detect" },
  { key: "cutting_clips", label: "cut" },
  { key: "burning_captions", label: "caption" },
];
const STATUS_TEXT = {
  queued: "queued…",
  downloading: "downloading video…",
  transcribing: "transcribing audio…",
  detecting_highlights: "finding moments for your brief…",
  cutting_clips: "reframing clips…",
  burning_captions: "designing captions…",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
};

const el = (id) => document.getElementById(id);
const urlInput = el("urlInput");
const fileInput = el("fileInput");
const creativePrompt = el("creativePrompt");
const generateBtn = el("generateBtn");
const slateHint = el("slateHint");
const progressSection = el("progressSection");
const stageFrames = el("stageFrames");
const progressLabel = el("progressLabel");
const progressMeter = el("progressMeter");
const progressMeterFill = el("progressMeterFill");
const progressPercent = el("progressPercent");
const progressElapsed = el("progressElapsed");
const progressDetail = el("progressDetail");
const cancelJobBtn = el("cancelJobBtn");
const retryJobBtn = el("retryJobBtn");
const resultsSection = el("resultsSection");
const clipGrid = el("clipGrid");
const errorSection = el("errorSection");
const errorDetail = el("errorDetail");
const errorStage = el("errorStage");
const errorRetryBtn = el("errorRetryBtn");
const apiStatus = el("apiStatus");

let pollTimer = null;
let apiHealthy = false;
let currentClips = [];
let consecutivePollFailures = 0;
let currentJobId = null;
let currentJobCreatedAt = null;
let elapsedTimer = null;
let jobActionPending = false;
const LAST_JOB_KEY = "clipmaker:last-job-id";
const CLIP_LENGTHS = {
  quick: { min: 15, max: 30 },
  balanced: { min: 20, max: 60 },
  deep: { min: 45, max: 90 },
};

function selectedValue(containerId) {
  return el(containerId).querySelector(".active")?.dataset.value;
}

function setupChoices(containerId, onChange = () => {}) {
  const container = el(containerId);
  container.querySelectorAll("button[data-value]").forEach((button) => {
    button.addEventListener("click", () => {
      container.querySelectorAll("button[data-value]").forEach((item) => {
        item.classList.toggle("active", item === button);
        item.setAttribute("aria-pressed", item === button ? "true" : "false");
      });
      onChange(button.dataset.value);
    });
  });
}

setupChoices("initialFormatChoices");
setupChoices("initialCaptionChoices");
setupChoices("clipCountChoices");
setupChoices("clipLengthChoices");
document.querySelectorAll(".prompt-chips button").forEach((button) => {
  button.addEventListener("click", () => {
    creativePrompt.value = button.dataset.prompt;
    creativePrompt.focus();
  });
});

async function responseError(response, fallback) {
  const payload = await response.json().catch(() => null);
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item.msg).filter(Boolean).join(" · ") || fallback;
  }
  return fallback;
}

async function checkApiHealth() {
  try {
    const response = await fetch(`${API_BASE}/`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("bad status");
    apiHealthy = true;
    apiStatus.innerHTML = `<span class="dot live"></span> backend connected`;
  } catch {
    apiHealthy = false;
    apiStatus.innerHTML = `<span class="dot down"></span> backend offline — start it at ${API_BASE}`;
  }
  return apiHealthy;
}
checkApiHealth();
setInterval(checkApiHealth, 5000);

function renderStageFrames() {
  stageFrames.innerHTML = STAGES.map(
    (stage) => `<div class="stage-frame" data-stage="${stage.key}">${stage.label}</div>`
  ).join("");
}

function setActiveStage(statusKey) {
  const index = STAGES.findIndex((stage) => stage.key === statusKey);
  document.querySelectorAll(".stage-frame").forEach((frame, frameIndex) => {
    frame.classList.remove("active", "done");
    if (statusKey === "done") {
      frame.classList.add("done");
      return;
    }
    if (index === -1) return;
    if (frameIndex < index) frame.classList.add("done");
    if (frameIndex === index) frame.classList.add("active");
  });
}

function formatElapsed(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function startElapsedClock(createdAt) {
  if (createdAt) currentJobCreatedAt = Date.parse(createdAt) || Date.now();
  if (!currentJobCreatedAt) currentJobCreatedAt = Date.now();
  clearInterval(elapsedTimer);
  const update = () => {
    progressElapsed.textContent = `Elapsed ${formatElapsed((Date.now() - currentJobCreatedAt) / 1000)}`;
  };
  update();
  elapsedTimer = setInterval(update, 1000);
}

function stopElapsedClock() {
  clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function updateJobProgress(data) {
  const progress = data.progress || {};
  const percent = Math.max(0, Math.min(100, Math.round(Number(progress.percent) || 0)));
  const visibleStage = ["failed", "cancelled"].includes(data.status)
    ? progress.failed_stage || progress.current_stage
    : data.status;
  setActiveStage(visibleStage);
  progressMeter.setAttribute("aria-valuenow", String(percent));
  progressMeterFill.style.width = `${percent}%`;
  progressPercent.textContent = `${percent}%`;
  progressLabel.textContent = progress.message || STATUS_TEXT[data.status] || data.status;
  const processed = Number(progress.processed_clips) || 0;
  const total = Number(progress.total_clips) || 0;
  progressDetail.textContent = total ? `${processed} of ${total} clips finished` : "";
  cancelJobBtn.hidden = !data.can_cancel;
  cancelJobBtn.disabled = jobActionPending;
  retryJobBtn.hidden = !data.can_retry;
  retryJobBtn.disabled = jobActionPending;
  if (!elapsedTimer && !["done", "failed", "cancelled"].includes(data.status)) {
    startElapsedClock(progress.created_at);
  }
}

generateBtn.addEventListener("click", async () => {
  if (generateBtn.disabled) return;
  const url = urlInput.value.trim();
  const file = fileInput.files[0];
  if (!url && !file) {
    slateHint.textContent = "Paste a YouTube link or choose a video file first.";
    slateHint.style.color = "var(--coral)";
    return;
  }

  resetUI();
  generateBtn.disabled = true;
  generateBtn.querySelector("span").textContent = "Starting…";
  const settings = {
    creative_prompt: creativePrompt.value.trim() || null,
    output_format: selectedValue("initialFormatChoices") || "vertical",
    caption_style: selectedValue("initialCaptionChoices") || "bold",
    target_clip_count: Number(selectedValue("clipCountChoices") || 5),
    min_clip_duration: CLIP_LENGTHS[selectedValue("clipLengthChoices") || "balanced"].min,
    max_clip_duration: CLIP_LENGTHS[selectedValue("clipLengthChoices") || "balanced"].max,
  };

  try {
    if (!(await checkApiHealth())) {
      throw new Error(`The backend is not reachable at ${API_BASE}. Start it and try again.`);
    }
    let response;
    if (file) {
      const formData = new FormData();
      formData.append("file", file);
      if (settings.creative_prompt) formData.append("creative_prompt", settings.creative_prompt);
      formData.append("output_format", settings.output_format);
      formData.append("caption_style", settings.caption_style);
      formData.append("target_clip_count", String(settings.target_clip_count));
      formData.append("min_clip_duration", String(settings.min_clip_duration));
      formData.append("max_clip_duration", String(settings.max_clip_duration));
      response = await fetch(`${API_BASE}/api/jobs/upload`, { method: "POST", body: formData });
    } else {
      response = await fetch(`${API_BASE}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, ...settings }),
      });
    }
    if (!response.ok) throw new Error(await responseError(response, "Could not start this job."));
    const { job_id: jobId } = await response.json();
    currentJobId = jobId;
    currentJobCreatedAt = Date.now();
    rememberJob(jobId);
    progressSection.hidden = false;
    renderStageFrames();
    startElapsedClock();
    progressSection.scrollIntoView({ behavior: "smooth", block: "center" });
    pollJob(jobId);
  } catch (error) {
    showError(error.message || "Could not start the job.");
    finishGenerating();
  }
});

function pollJob(jobId) {
  currentJobId = jobId;
  clearTimeout(pollTimer);
  consecutivePollFailures = 0;
  const tick = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}`, { cache: "no-store" });
      if (!response.ok) {
        const message = await responseError(response, "The backend briefly lost connection.");
        const error = new Error(message);
        error.permanent = response.status === 404;
        throw error;
      }
      const data = await response.json();
      consecutivePollFailures = 0;
      updateJobProgress(data);
      if (data.status === "done") {
        stopElapsedClock();
        renderClips(data.clips);
        finishGenerating();
        return;
      }
      if (data.status === "failed") {
        stopElapsedClock();
        showError(
          data.error || "Something went wrong while processing your video.",
          data.progress?.failed_stage,
          data.can_retry,
        );
        finishGenerating();
        return;
      }
      if (data.status === "cancelled") {
        stopElapsedClock();
        finishGenerating();
        return;
      }
      pollTimer = setTimeout(tick, 2500);
    } catch (error) {
      consecutivePollFailures += 1;
      if (!error.permanent && consecutivePollFailures <= 8) {
        progressSection.hidden = false;
        progressLabel.textContent = `connection interrupted — retrying (${consecutivePollFailures}/8)…`;
        pollTimer = setTimeout(tick, 3000);
        return;
      }
      if (error.permanent) forgetJob();
      showError(error.message || "Could not reconnect to this job.", "connection", false);
      stopElapsedClock();
      finishGenerating();
    }
  };
  tick();
}

async function requestJobAction(action) {
  if (!currentJobId || jobActionPending) return;
  jobActionPending = true;
  cancelJobBtn.disabled = true;
  retryJobBtn.disabled = true;
  errorRetryBtn.disabled = true;
  const isRetry = action === "retry";
  if (isRetry) {
    retryJobBtn.textContent = "Retrying…";
    errorRetryBtn.textContent = "Retrying…";
  } else {
    cancelJobBtn.textContent = "Requesting cancellation…";
  }
  try {
    const response = await fetch(`${API_BASE}/api/jobs/${currentJobId}/${action}`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error(await responseError(response, `Could not ${action} this job.`));
    }
    const data = await response.json();
    progressSection.hidden = false;
    progressLabel.textContent = data.message || (isRetry ? "Queued for retry…" : "Cancellation requested…");
    if (isRetry) {
      errorSection.hidden = true;
      resultsSection.hidden = true;
      generateBtn.disabled = true;
      generateBtn.querySelector("span").textContent = "Processing…";
      currentJobCreatedAt = Date.now();
      startElapsedClock();
    } else {
      cancelJobBtn.hidden = true;
    }
    pollJob(currentJobId);
  } catch (error) {
    showError(error.message || `Could not ${action} this job.`, "job control", false);
  } finally {
    jobActionPending = false;
    cancelJobBtn.disabled = false;
    retryJobBtn.disabled = false;
    errorRetryBtn.disabled = false;
    cancelJobBtn.textContent = "Cancel processing";
    retryJobBtn.textContent = "Retry this job";
    errorRetryBtn.textContent = "Retry from saved work";
  }
}

cancelJobBtn.addEventListener("click", () => requestJobAction("cancel"));
retryJobBtn.addEventListener("click", () => requestJobAction("retry"));
errorRetryBtn.addEventListener("click", () => requestJobAction("retry"));

function rememberJob(jobId) {
  currentJobId = jobId;
  localStorage.setItem(LAST_JOB_KEY, jobId);
  const url = new URL(window.location.href);
  url.searchParams.set("job", jobId);
  window.history.replaceState({}, "", url);
}

function forgetJob() {
  localStorage.removeItem(LAST_JOB_KEY);
  currentJobId = null;
  const url = new URL(window.location.href);
  url.searchParams.delete("job");
  window.history.replaceState({}, "", url);
}

function resumeJobFromUrl() {
  const jobId = new URL(window.location.href).searchParams.get("job");
  // A clean URL means the user explicitly wants a fresh home screen. We keep
  // the last ID only for a future "resume last job" action; it must never
  // silently rewrite the address after the user removes ?job=...
  if (!jobId) return;
  if (!/^[0-9a-f-]{36}$/i.test(jobId)) {
    forgetJob();
    return;
  }
  localStorage.setItem(LAST_JOB_KEY, jobId);
  currentJobId = jobId;
  progressSection.hidden = false;
  renderStageFrames();
  progressLabel.textContent = "restoring your last job…";
  generateBtn.disabled = true;
  generateBtn.querySelector("span").textContent = "Processing…";
  pollJob(jobId);
}

function finishGenerating() {
  generateBtn.disabled = false;
  generateBtn.querySelector("span").textContent = "Generate clips";
}

function scoreBreakdownHtml(breakdown) {
  const metrics = [
    ["hook", "Hook"],
    ["relevance", "Match"],
    ["completeness", "Complete"],
    ["shareability", "Shareable"],
  ];
  if (!breakdown || !metrics.every(([key]) => Number.isFinite(Number(breakdown[key])))) return "";
  return `<div class="clip-metrics" aria-label="AI quality score breakdown">${metrics.map(([key, label]) => {
    const score = Math.max(0, Math.min(100, Math.round(Number(breakdown[key]))));
    return `<span title="${label} score ${score} out of 100"><small>${label}</small><b>${score}</b></span>`;
  }).join("")}</div>`;
}

function renderClips(clips) {
  if (!clips?.length) {
    showError("No strong, self-contained moments were found. Try adding a more specific creative direction.");
    return;
  }
  currentClips = clips;
  resultsSection.hidden = false;
  clipGrid.innerHTML = clips.map((clip, index) => {
    const format = ["landscape", "vertical", "original"].includes(clip.output_format)
      ? clip.output_format
      : "vertical";
    return `
      <article class="clip-card">
        <div class="clip-thumb ${format}">
          <video class="clip-video" controls preload="metadata" playsinline src="${API_BASE}${clip.preview_url}"></video>
          <span class="clip-score">quality ${clip.score}</span>
          <span class="clip-time">${formatTime(clip.start)}–${formatTime(clip.end)}</span>
        </div>
        <div class="clip-body">
          <p class="clip-title">${escapeHtml(clip.title)}</p>
          <p class="clip-reason">${escapeHtml(clip.reason || "")}</p>
          ${scoreBreakdownHtml(clip.score_breakdown)}
          <div class="clip-actions">
            <button class="clip-edit" data-clip-index="${index}">Open studio</button>
            <button class="clip-download" data-url="${API_BASE}${clip.download_url}">Download</button>
          </div>
        </div>
      </article>`;
  }).join("");

  clipGrid.querySelectorAll(".clip-download").forEach((button) => {
    button.addEventListener("click", () => downloadUrl(button.dataset.url));
  });
  clipGrid.querySelectorAll(".clip-edit").forEach((button) => {
    button.addEventListener("click", () => openEditor(currentClips[Number(button.dataset.clipIndex)]));
  });
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Studio state
const MIN_TRIM_DURATION = 1;
const CAPTION_STYLE_PRESETS = {
  bold: { scale: 1, outline: 5, shadow: 1 },
  clean: { scale: .78, outline: 0, shadow: 0 },
  karaoke: { scale: .96, outline: 3, shadow: 1 },
};
const CAPTION_SIZE_SCALE = { small: .84, medium: 1, large: 1.16 };
const CAPTION_LAYOUTS = {
  vertical: { width: 720, height: 1280, maxWords: 5, maxCharacters: 30, maxLineCharacters: 18, maxDuration: 2.6, baseSize: .075, safeX: 10, top: 13, bottom: 8 },
  landscape: { width: 1280, height: 720, maxWords: 8, maxCharacters: 52, maxLineCharacters: 34, maxDuration: 3.2, baseSize: .038, safeX: 7, top: 8, bottom: 6 },
};
let editingClip = null;
let clipDurationSec = 0;
let trimStartSec = 0;
let trimEndSec = 0;
let playheadSec = 0;
let draggingMode = null;
let editorCaptionWords = [];
let selectedCaptionGroupIndex = -1;
let undoStack = [];
let editorDirty = false;
let transcriptEditTimer = null;
let transcriptSnapshotTaken = false;
let pendingSeekSec = null;
let dragAnchorSec = 0;
let dragInitialStartSec = 0;
let dragInitialEndSec = 0;
let dragStartClientX = 0;
let cropKeyframes = [];
let cropDragState = null;
let previewAnimationFrame = null;
let smartReframeAnalysis = null;
let smartReframeRequest = 0;

const editorSection = el("editorSection");
const editorVideo = el("editorVideo");
const editorPreviewCanvas = el("editorPreviewCanvas");
const editorPreviewStage = el("editorPreviewStage");
const editorPreviewStatus = el("editorPreviewStatus");
const captionPreview = el("captionPreview");
const editorTimeline = el("editorTimeline");
const editorFrames = el("editorFrames");
const timelineRuler = el("timelineRuler");
const timelinePlayhead = el("timelinePlayhead");
const timelineScrubTooltip = el("timelineScrubTooltip");
const playheadReadout = el("playheadReadout");
const timelineDurationReadout = el("timelineDurationReadout");
const playPauseBtn = el("playPauseBtn");
const setInBtn = el("setInBtn");
const setOutBtn = el("setOutBtn");
const trimRegion = el("trimRegion");
const trimMaskLeft = el("trimMaskLeft");
const trimMaskRight = el("trimMaskRight");
const trimLeft = el("trimLeft");
const trimRight = el("trimRight");
const trimLeftLabel = el("trimLeftLabel");
const trimRightLabel = el("trimRightLabel");
const trimStartInput = el("trimStartInput");
const trimEndInput = el("trimEndInput");
const trimReadout = el("trimReadout");
const trimDurationReadout = el("trimDurationReadout");
const captionSegmentTrack = el("captionSegmentTrack");
const captionSegmentCount = el("captionSegmentCount");
const captionEditPanel = el("captionEditPanel");
const captionSegmentText = el("captionSegmentText");
const captionSegmentStart = el("captionSegmentStart");
const captionSegmentEnd = el("captionSegmentEnd");
const captionEditStatus = el("captionEditStatus");
const captionToggle = el("captionToggle");
const captionText = el("captionText");
const captionPosition = el("captionPosition");
const captionLayoutHint = el("captionLayoutHint");
const cropFocus = el("cropFocus");
const cropLabel = el("cropLabel");
const cropVertical = el("cropVertical");
const cropVerticalLabel = el("cropVerticalLabel");
const cropZoom = el("cropZoom");
const cropZoomLabel = el("cropZoomLabel");
const cropControl = el("cropControl");
const reframeHelp = el("reframeHelp");
const smartReframeInfo = el("smartReframeInfo");
const smartReframeDot = el("smartReframeDot");
const smartReframeStatus = el("smartReframeStatus");
const smartReframeMetrics = el("smartReframeMetrics");
const smartDetectionValue = el("smartDetectionValue");
const smartSceneValue = el("smartSceneValue");
const smartZoomValue = el("smartZoomValue");
const smartReframeMessage = el("smartReframeMessage");
const reframeDragHint = el("reframeDragHint");
const reframeTrackWrap = el("reframeTrackWrap");
const reframeKeyframeTrack = el("reframeKeyframeTrack");
const setCropKeyframeBtn = el("setCropKeyframeBtn");
const deleteCropKeyframeBtn = el("deleteCropKeyframeBtn");
const speechBoundaryStatus = el("speechBoundaryStatus");
const snapSpeechBoundariesBtn = el("snapSpeechBoundariesBtn");
const undoEditorBtn = el("undoEditorBtn");
const exportBtn = el("exportBtn");
const editorStatus = el("editorStatus");
const editorSaveState = el("editorSaveState");

setupChoices("editorFormatChoices", () => { markEditorDirty(); renderCaptionSegments(); updateEditorPreview(); });
setupChoices("editorCaptionChoices", () => { markEditorDirty(); updateCaptionPreview(); });
setupChoices("captionSizeChoices", () => { markEditorDirty(); updateCaptionPreview(); });
setupChoices("reframeModeChoices", () => {
  markEditorDirty();
  renderCropKeyframes();
  updateEditorPreview();
});

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function activateChoice(containerId, value) {
  const container = el(containerId);
  const wanted = container.querySelector(`[data-value="${value}"]`) || container.querySelector("button");
  container.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button === wanted);
    button.setAttribute("aria-pressed", button === wanted ? "true" : "false");
  });
}

function snapshotEditorState() {
  return {
    trimStartSec,
    trimEndSec,
    playheadSec,
    captionWords: editorCaptionWords.map((word) => ({ ...word })),
    captionsOn: captionToggle.dataset.on === "true",
    captionPosition: captionPosition.value,
    cropFocus: cropFocus.value,
    cropVertical: cropVertical.value,
    cropZoom: cropZoom.value,
    cropKeyframes: cropKeyframes.map((point) => ({ ...point })),
    outputFormat: selectedValue("editorFormatChoices"),
    captionStyle: selectedValue("editorCaptionChoices"),
    captionSize: selectedValue("captionSizeChoices"),
    reframeMode: selectedValue("reframeModeChoices"),
    selectedCaptionGroupIndex,
  };
}

function pushUndo() {
  if (!editingClip) return;
  undoStack.push(snapshotEditorState());
  if (undoStack.length > 30) undoStack.shift();
  undoEditorBtn.disabled = false;
}

function restoreEditorState(state) {
  trimStartSec = state.trimStartSec;
  trimEndSec = state.trimEndSec;
  editorCaptionWords = state.captionWords.map((word) => ({ ...word }));
  selectedCaptionGroupIndex = state.selectedCaptionGroupIndex;
  captionToggle.dataset.on = String(state.captionsOn);
  captionToggle.setAttribute("aria-pressed", String(state.captionsOn));
  captionText.disabled = !state.captionsOn;
  captionPosition.disabled = !state.captionsOn;
  captionPosition.value = state.captionPosition;
  cropFocus.value = state.cropFocus;
  cropVertical.value = state.cropVertical;
  cropZoom.value = state.cropZoom;
  cropKeyframes = (state.cropKeyframes || []).map((point) => ({ ...point }));
  activateChoice("editorFormatChoices", state.outputFormat);
  activateChoice("editorCaptionChoices", state.captionStyle);
  activateChoice("captionSizeChoices", state.captionSize);
  activateChoice("reframeModeChoices", state.reframeMode);
  syncCaptionTextFromWords();
  seekTo(state.playheadSec);
  updateTrimUI();
  renderCaptionSegments();
  renderCropKeyframes();
  updateEditorPreview();
}

function markEditorDirty() {
  if (!editingClip) return;
  editorDirty = true;
  editorSaveState.textContent = "Unsaved changes";
  editorSaveState.classList.add("dirty");
}

function markEditorClean() {
  editorDirty = false;
  editorSaveState.textContent = "No changes";
  editorSaveState.classList.remove("dirty");
}

function renderSmartReframeState(state = "loading", message = "") {
  smartReframeDot.className = `smart-status-dot ${state}`;
  if (state === "loading") {
    smartReframeStatus.textContent = "Analysing subjects…";
    smartReframeMetrics.hidden = true;
    smartReframeMessage.textContent = "Detection stays on this device and the result is reused during export.";
    return;
  }
  if (!smartReframeAnalysis) {
    smartReframeStatus.textContent = "Smart analysis unavailable";
    smartReframeMetrics.hidden = true;
    smartReframeMessage.textContent = message || "A safe centered crop will be used. Manual mode remains available.";
    return;
  }
  const analysis = smartReframeAnalysis;
  const qualityLabel = {
    strong: "Strong subject lock",
    good: "Good subject lock",
    limited: "Limited subject lock",
    "centered-fallback": "Safe centered fallback",
  }[analysis.quality] || "Smart path ready";
  smartReframeStatus.textContent = qualityLabel;
  smartReframeMetrics.hidden = false;
  smartDetectionValue.textContent = `${Math.round(Number(analysis.detection_ratio || 0) * 100)}%`;
  smartSceneValue.textContent = String(analysis.scene_cuts || 0);
  smartZoomValue.textContent = `${Number(analysis.zoom || 1).toFixed(2)}×`;
  smartReframeMessage.textContent = analysis.message || message;
}

async function loadSmartReframe(clip) {
  const request = ++smartReframeRequest;
  smartReframeAnalysis = null;
  renderSmartReframeState("loading");
  renderCropKeyframes();
  try {
    const analysisUrl = clip.reframe_analysis_url
      || `/api/jobs/${currentJobId}/clips/${clip.id}/reframe-analysis`;
    const response = await fetch(`${API_BASE}${analysisUrl}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await responseError(response, "Smart analysis unavailable."));
    const data = await response.json();
    if (request !== smartReframeRequest || editingClip !== clip) return;
    smartReframeAnalysis = {
      ...data,
      points: Array.isArray(data.points)
        ? data.points.map((point) => ({
          time: Number(point.time),
          x: Number(point.x),
          y: Number(point.y),
          confidence: Number(point.confidence || 0),
          sceneCut: Boolean(point.scene_cut),
        }))
        : [],
    };
    renderSmartReframeState(data.points?.length ? "ready" : "limited");
    renderCropKeyframes();
    drawEditorPreview();
  } catch (error) {
    if (request !== smartReframeRequest || editingClip !== clip) return;
    smartReframeAnalysis = null;
    renderSmartReframeState("limited", error.message);
    renderCropKeyframes();
    drawEditorPreview();
  }
}

async function openEditor(clip) {
  editingClip = clip;
  clipDurationSec = Math.max(clip.end - clip.start, 0.1);
  trimStartSec = 0;
  trimEndSec = clipDurationSec;
  playheadSec = 0;
  pendingSeekSec = null;
  draggingMode = null;
  trimRegion.classList.remove("dragging");
  timelineScrubTooltip.classList.remove("visible");
  selectedCaptionGroupIndex = -1;
  undoStack = [];
  undoEditorBtn.disabled = true;
  captionToggle.dataset.on = "true";
  captionToggle.setAttribute("aria-pressed", "true");
  captionText.disabled = false;
  cropFocus.value = "50";
  cropVertical.value = "50";
  cropZoom.value = "100";
  cropKeyframes = [];
  smartReframeAnalysis = null;
  captionPosition.value = "bottom";
  editorCaptionWords = (Array.isArray(clip.caption_words) ? clip.caption_words : [])
    .map((word) => ({ ...word, start: Number(word.start), end: Number(word.end) }));
  syncCaptionTextFromWords();
  activateChoice("editorFormatChoices", clip.output_format || "vertical");
  activateChoice("editorCaptionChoices", clip.caption_style || "bold");
  activateChoice("captionSizeChoices", "medium");
  activateChoice("reframeModeChoices", "auto");
  editorStatus.textContent = "";
  editorStatus.style.color = "";
  captionEditStatus.textContent = "";
  markEditorClean();
  editorVideo.pause();
  editorVideo.removeAttribute("src");
  editorVideo.load();
  editorPreviewStatus.textContent = `Preparing a clean ${formatTimePrecise(clipDurationSec)} clip preview…`;
  editorVideo.onloadedmetadata = () => {
    if (editorVideo.videoWidth && editorVideo.videoHeight) {
      editorPreviewStage.style.setProperty(
        "--source-ratio",
        `${editorVideo.videoWidth} / ${editorVideo.videoHeight}`
      );
    }
    editorPreviewStatus.textContent = `Clip-only preview · ${formatTimePrecise(clipDurationSec)} · export uses the original source.`;
    seekTo(playheadSec);
  };
  editorSection.hidden = false;
  updateTimelineRuler();
  updateTrimUI();
  renderCaptionSegments();
  renderCropKeyframes();
  updateEditorPreview();
  void loadSmartReframe(clip);
  editorSection.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const response = await fetch(`${API_BASE}${clip.editor_preview_url}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await responseError(response, "Clean preview unavailable."));
    const data = await response.json();
    if (editingClip !== clip) return;
    if (data.source_width && data.source_height) {
      editorPreviewStage.style.setProperty("--source-ratio", `${data.source_width} / ${data.source_height}`);
    }
    editorVideo.src = `${API_BASE}${data.preview_url}?studio=16`;
  } catch (error) {
    if (editingClip !== clip) return;
    editorVideo.src = `${API_BASE}${clip.preview_url}`;
    editorPreviewStatus.textContent = `${error.message} Showing the rendered clip instead.`;
  }

  editorFrames.innerHTML = `<p class="frames-loading">loading timeline…</p>`;
  try {
    const response = await fetch(`${API_BASE}${clip.thumbnails_url}`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    editorFrames.innerHTML = data.thumbnails
      .map((url) => `<img src="${API_BASE}${url}" class="editor-frame" alt="" />`)
      .join("");
  } catch {
    editorFrames.innerHTML = `<p class="frames-loading">Timeline preview unavailable — precise trimming still works.</p>`;
  }
}

function closeEditor() {
  editorSection.hidden = true;
  editorVideo.pause();
  editorVideo.removeAttribute("src");
  editingClip = null;
  editorCaptionWords = [];
  cropKeyframes = [];
  smartReframeAnalysis = null;
  smartReframeRequest += 1;
  cropDragState = null;
  if (previewAnimationFrame !== null) cancelAnimationFrame(previewAnimationFrame);
  previewAnimationFrame = null;
  pendingSeekSec = null;
  draggingMode = null;
  trimRegion.classList.remove("dragging");
  timelineScrubTooltip.classList.remove("visible");
  undoStack = [];
}
el("editorClose").addEventListener("click", closeEditor);

function updateEditorPreview() {
  const format = selectedValue("editorFormatChoices") || "vertical";
  editorPreviewStage.dataset.format = format;
  const reframeMode = selectedValue("reframeModeChoices") || "auto";
  cropControl.hidden = format === "original" || reframeMode === "auto";
  smartReframeInfo.hidden = format === "original" || reframeMode !== "auto";
  reframeDragHint.hidden = format === "original" || reframeMode !== "manual";
  reframeHelp.textContent = reframeMode === "manual"
    ? "Manual preview matches the exported crop. Drag the video and save positions over time."
    : "Smart auto follows the likely speaker, recovers after scene cuts, and smooths camera movement. The preview shows its planned path.";
  renderCropKeyframes();
  syncCropControlsForPlayhead();
  drawEditorPreview();
  updateCaptionLayoutPreview();
  updateCaptionPreview();
}

function interpolateCropPoints(points, seconds, fallback = { x: .5, y: .5 }) {
  if (!points.length) return fallback;
  points = [...points].sort((first, second) => first.time - second.time);
  if (seconds <= points[0].time) return { x: points[0].x, y: points[0].y };
  if (seconds >= points.at(-1).time) return { x: points.at(-1).x, y: points.at(-1).y };
  const afterIndex = points.findIndex((point) => point.time >= seconds);
  const before = points[afterIndex - 1];
  const after = points[afterIndex];
  const progress = (seconds - before.time) / Math.max(after.time - before.time, .001);
  return {
    x: before.x + (after.x - before.x) * progress,
    y: before.y + (after.y - before.y) * progress,
  };
}

function cropAtTime(seconds = playheadSec) {
  return interpolateCropPoints(cropKeyframes, seconds, {
    x: Number(cropFocus.value) / 100,
    y: Number(cropVertical.value) / 100,
  });
}

function smartCropAtTime(seconds = playheadSec) {
  return interpolateCropPoints(smartReframeAnalysis?.points || [], seconds);
}

function setCropControlLabels() {
  const horizontal = Number(cropFocus.value);
  const vertical = Number(cropVertical.value);
  cropLabel.textContent = horizontal < 34 ? "Left" : horizontal > 66 ? "Right" : "Center";
  cropVerticalLabel.textContent = vertical < 34 ? "Top" : vertical > 66 ? "Bottom" : "Center";
  cropZoomLabel.textContent = `${(Number(cropZoom.value) / 100).toFixed(2)}×`;
}

function syncCropControlsForPlayhead() {
  if (!cropKeyframes.length || cropDragState) {
    setCropControlLabels();
    return;
  }
  const crop = cropAtTime(playheadSec);
  cropFocus.value = String(Math.round(crop.x * 100));
  cropVertical.value = String(Math.round(crop.y * 100));
  setCropControlLabels();
}

function previewCropGeometry(format = selectedValue("editorFormatChoices") || "vertical") {
  const sourceWidth = editorVideo.videoWidth || 1280;
  const sourceHeight = editorVideo.videoHeight || 720;
  if (format === "original") {
    return { sourceWidth, sourceHeight, sx: 0, sy: 0, cropWidth: sourceWidth, cropHeight: sourceHeight };
  }
  const layout = CAPTION_LAYOUTS[format];
  const targetRatio = layout.width / layout.height;
  const sourceRatio = sourceWidth / sourceHeight;
  let cropWidth = sourceRatio > targetRatio ? sourceHeight * targetRatio : sourceWidth;
  let cropHeight = sourceRatio > targetRatio ? sourceHeight : sourceWidth / targetRatio;
  const manual = selectedValue("reframeModeChoices") === "manual";
  const zoom = manual
    ? clamp(Number(cropZoom.value) / 100, 1, 3)
    : clamp(Number(smartReframeAnalysis?.zoom || 1), 1, 3);
  cropWidth /= zoom;
  cropHeight /= zoom;
  const focus = manual
    ? (cropDragState
      ? { x: Number(cropFocus.value) / 100, y: Number(cropVertical.value) / 100 }
      : cropAtTime(editorVideo.currentTime || playheadSec))
    : smartCropAtTime(editorVideo.currentTime || playheadSec);
  return {
    sourceWidth,
    sourceHeight,
    cropWidth,
    cropHeight,
    sx: (sourceWidth - cropWidth) * clamp(focus.x, 0, 1),
    sy: (sourceHeight - cropHeight) * clamp(focus.y, 0, 1),
  };
}

function drawEditorPreview() {
  const context = editorPreviewCanvas.getContext("2d");
  if (!context) return;
  const rect = editorPreviewStage.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const canvasWidth = Math.max(Math.round(rect.width * pixelRatio), 2);
  const canvasHeight = Math.max(Math.round(rect.height * pixelRatio), 2);
  if (editorPreviewCanvas.width !== canvasWidth || editorPreviewCanvas.height !== canvasHeight) {
    editorPreviewCanvas.width = canvasWidth;
    editorPreviewCanvas.height = canvasHeight;
  }
  context.fillStyle = "#050505";
  context.fillRect(0, 0, canvasWidth, canvasHeight);
  if (editorVideo.readyState < 2 || !editorVideo.videoWidth) return;
  const crop = previewCropGeometry();
  try {
    context.drawImage(
      editorVideo,
      crop.sx,
      crop.sy,
      crop.cropWidth,
      crop.cropHeight,
      0,
      0,
      canvasWidth,
      canvasHeight,
    );
  } catch {
    // Metadata can become ready one event before the decoded frame is drawable.
  }
}

function animateEditorPreview() {
  drawEditorPreview();
  syncCropControlsForPlayhead();
  if (!editorVideo.paused && editingClip) {
    previewAnimationFrame = requestAnimationFrame(animateEditorPreview);
  } else {
    previewAnimationFrame = null;
  }
}

function findCropKeyframeIndex(time = playheadSec, tolerance = .16) {
  return cropKeyframes.findIndex((point) => Math.abs(point.time - time) <= tolerance);
}

function upsertCropKeyframe(time = playheadSec) {
  const point = {
    time: clamp(Number(time), 0, clipDurationSec),
    x: Number(cropFocus.value) / 100,
    y: Number(cropVertical.value) / 100,
  };
  const existing = findCropKeyframeIndex(point.time);
  if (existing >= 0) cropKeyframes[existing] = point;
  else cropKeyframes.push(point);
  cropKeyframes.sort((first, second) => first.time - second.time);
  renderCropKeyframes();
  drawEditorPreview();
}

function renderCropKeyframes() {
  const manual = selectedValue("reframeModeChoices") === "manual";
  const format = selectedValue("editorFormatChoices") || "vertical";
  const points = manual ? cropKeyframes : (smartReframeAnalysis?.points || []);
  reframeTrackWrap.hidden = format === "original" || (!manual && !points.length);
  reframeKeyframeTrack.innerHTML = "";
  points.forEach((point, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "reframe-keyframe";
    if (!manual) button.classList.add("smart");
    if (point.sceneCut) button.classList.add("scene-cut");
    button.style.left = `${clipDurationSec ? (point.time / clipDurationSec) * 100 : 0}%`;
    button.title = `${manual ? "Manual" : "Smart"} reframe position at ${formatTimePrecise(point.time)}`;
    button.setAttribute("aria-label", button.title);
    button.classList.toggle(
      "active",
      manual
        ? index === findCropKeyframeIndex()
        : Math.abs(Number(point.time) - playheadSec) <= .18
    );
    button.addEventListener("click", () => seekTo(point.time));
    reframeKeyframeTrack.appendChild(button);
  });
  const activeIndex = findCropKeyframeIndex();
  deleteCropKeyframeBtn.disabled = !manual || activeIndex < 0;
  setCropKeyframeBtn.textContent = activeIndex < 0
    ? "Set position here"
    : "Update this position";
}

function cropKeyframesForExport(start, end) {
  if (!cropKeyframes.length) return [];
  const startCrop = cropAtTime(start);
  const endCrop = cropAtTime(end);
  const points = [
    { time: 0, ...startCrop },
    ...cropKeyframes
      .filter((point) => point.time > start + .01 && point.time < end - .01)
      .map((point) => ({ time: point.time - start, x: point.x, y: point.y })),
    { time: end - start, ...endCrop },
  ];
  return points.filter((point, index) => index === 0 || point.time - points[index - 1].time > .01);
}

setCropKeyframeBtn.addEventListener("click", () => {
  pushUndo();
  upsertCropKeyframe();
  markEditorDirty();
});

deleteCropKeyframeBtn.addEventListener("click", () => {
  const index = findCropKeyframeIndex();
  if (index < 0) return;
  pushUndo();
  cropKeyframes.splice(index, 1);
  renderCropKeyframes();
  syncCropControlsForPlayhead();
  drawEditorPreview();
  markEditorDirty();
});

function handleCropControlInput() {
  setCropControlLabels();
  if (cropKeyframes.length && document.activeElement !== cropZoom) upsertCropKeyframe();
  drawEditorPreview();
  markEditorDirty();
}

[cropFocus, cropVertical, cropZoom].forEach((control) => {
  control.addEventListener("input", handleCropControlInput);
});

editorPreviewCanvas.addEventListener("pointerdown", (event) => {
  if (selectedValue("reframeModeChoices") !== "manual" || selectedValue("editorFormatChoices") === "original") return;
  pushUndo();
  cropDragState = {
    clientX: event.clientX,
    clientY: event.clientY,
    x: Number(cropFocus.value) / 100,
    y: Number(cropVertical.value) / 100,
  };
  editorPreviewCanvas.setPointerCapture(event.pointerId);
  editorPreviewCanvas.classList.add("dragging");
  event.preventDefault();
});

editorPreviewCanvas.addEventListener("pointermove", (event) => {
  if (!cropDragState) return;
  const geometry = previewCropGeometry();
  const rect = editorPreviewCanvas.getBoundingClientRect();
  const overflowX = Math.max((geometry.sourceWidth / geometry.cropWidth - 1) * rect.width, 1);
  const overflowY = Math.max((geometry.sourceHeight / geometry.cropHeight - 1) * rect.height, 1);
  const x = clamp(cropDragState.x - (event.clientX - cropDragState.clientX) / overflowX, 0, 1);
  const y = clamp(cropDragState.y - (event.clientY - cropDragState.clientY) / overflowY, 0, 1);
  cropFocus.value = String(Math.round(x * 100));
  cropVertical.value = String(Math.round(y * 100));
  setCropControlLabels();
  drawEditorPreview();
  markEditorDirty();
});

function finishCropDrag() {
  if (!cropDragState) return;
  cropDragState = null;
  editorPreviewCanvas.classList.remove("dragging");
  upsertCropKeyframe();
}

editorPreviewCanvas.addEventListener("pointerup", finishCropDrag);
editorPreviewCanvas.addEventListener("pointercancel", finishCropDrag);

function captionLayoutForPreview() {
  const format = selectedValue("editorFormatChoices") || "vertical";
  if (format !== "original") return { ...CAPTION_LAYOUTS[format], format, orientation: format };
  const width = editorVideo.videoWidth || 1280;
  const height = editorVideo.videoHeight || 720;
  const orientation = height > width ? "vertical" : "landscape";
  return { ...CAPTION_LAYOUTS[orientation], width, height, format, orientation };
}

function updateCaptionLayoutPreview() {
  const layout = captionLayoutForPreview();
  const style = selectedValue("editorCaptionChoices") || "bold";
  const size = selectedValue("captionSizeChoices") || "medium";
  const preset = CAPTION_STYLE_PRESETS[style];
  const fontSize = Math.round(clamp(
    layout.width * layout.baseSize * preset.scale * CAPTION_SIZE_SCALE[size],
    28,
    72
  ));
  captionPreview.dataset.orientation = layout.orientation;
  captionPreview.style.setProperty("--caption-font-size", `${(fontSize / layout.width) * 100}cqw`);
  captionPreview.style.setProperty("--caption-outline", `${(preset.outline / layout.width) * 100}cqw`);
  captionPreview.style.setProperty("--caption-shadow", `${(preset.shadow / layout.width) * 100}cqw`);
  captionPreview.style.setProperty("--caption-safe-x", `${layout.safeX}%`);
  const safeV = captionPosition.value === "top" ? layout.top : layout.bottom;
  captionPreview.style.setProperty("--caption-safe-v", `${safeV}%`);
  const formatLabel = layout.format === "original" ? "Original" : layout.orientation === "vertical" ? "9:16" : "16:9";
  captionLayoutHint.textContent = `${formatLabel} safe area · ${fontSize}px export type · maximum 2 lines.`;
}

function captionGroupsDetailed(words = editorCaptionWords, layout = captionLayoutForPreview()) {
  const groups = [];
  let current = [];
  let startIndex = 0;
  words.forEach((sourceWord, index) => {
    const word = { ...sourceWord, word: String(sourceWord.word || "").trim() };
    if (!word.word) return;
    const previous = current[current.length - 1];
    const pause = previous ? Number(word.start) - Number(previous.end) : 0;
    const characters = current.reduce((total, item) => total + item.word.length, 0) + current.length + word.word.length;
    const duration = current.length ? Number(word.end) - Number(current[0].start) : 0;
    const explicitJoin = Boolean(previous?.join_next);
    const shouldBreak = previous && !explicitJoin && (
      current.length >= layout.maxWords || characters > layout.maxCharacters || duration > layout.maxDuration || pause > .58 ||
      /[.!?…]$/.test(previous.word) || previous.break_after
    );
    if (shouldBreak) {
      groups.push({ words: current, startIndex, endIndex: index - 1 });
      current = [];
      startIndex = index;
    }
    current.push(word);
  });
  if (current.length) groups.push({ words: current, startIndex, endIndex: words.length - 1 });
  return groups;
}

function captionDisplayWords(group, finalGroup) {
  const displayWords = group.words.map((word, groupWordIndex) => ({
    ...word,
    groupWordIndex,
    displayWord: String(word.word || "").trim(),
  }));
  if (!displayWords.length) return displayWords;
  const match = displayWords[0].displayWord.match(/[A-Za-zÀ-ÖØ-öø-ÿ]/);
  if (match) {
    const index = match.index;
    const text = displayWords[0].displayWord;
    displayWords[0].displayWord = `${text.slice(0, index)}${text[index].toUpperCase()}${text.slice(index + 1)}`;
  }
  const last = displayWords[displayWords.length - 1];
  if (finalGroup && !/[.!?…]$/.test(last.displayWord)) last.displayWord += ".";
  return displayWords;
}

function wrapCaptionWords(words, maxLineCharacters) {
  const lines = [[]];
  words.forEach((word) => {
    const line = lines[lines.length - 1];
    const proposedLength = [...line.map((item) => item.displayWord), word.displayWord].join(" ").length;
    if (line.length && proposedLength > maxLineCharacters && lines.length < 2) lines.push([]);
    lines[lines.length - 1].push(word);
  });
  return lines;
}

function captionWordsForPreview(start = trimStartSec, end = trimEndSec) {
  return editorCaptionWords
    .filter((word) => Number(word.end) > start && Number(word.start) < end)
    .map((word) => ({
      ...word,
      start: Math.max(Number(word.start), start),
      end: Math.min(Number(word.end), end),
    }))
    .filter((word) => word.end - word.start >= .02);
}

function updateCaptionPreview() {
  if (!editingClip || captionToggle.dataset.on !== "true") {
    captionPreview.textContent = "";
    return;
  }
  const layout = captionLayoutForPreview();
  const groups = captionGroupsDetailed(captionWordsForPreview(), layout);
  const groupIndex = groups.findIndex(({ words }) => (
    playheadSec >= Number(words[0].start) - .01 &&
    playheadSec <= Number(words[words.length - 1].end) + .01
  ));
  const group = groups[groupIndex];
  captionPreview.dataset.style = selectedValue("editorCaptionChoices") || "bold";
  captionPreview.dataset.position = captionPosition.value;
  captionPreview.dataset.size = selectedValue("captionSizeChoices") || "medium";
  updateCaptionLayoutPreview();
  if (!group) {
    captionPreview.replaceChildren();
    return;
  }
  const displayWords = captionDisplayWords(group, groupIndex === groups.length - 1);
  const lines = wrapCaptionWords(displayWords, layout.maxLineCharacters);
  captionPreview.replaceChildren(...lines.map((line) => {
    const lineElement = document.createElement("span");
    lineElement.className = "caption-preview-line";
    line.forEach((word, index) => {
      const wordElement = document.createElement("span");
      wordElement.textContent = `${index ? " " : ""}${word.displayWord}`;
      const wordStart = Number(word.start);
      const nextWord = group.words[word.groupWordIndex + 1];
      const wordEnd = Math.max(Number(word.end), nextWord ? Number(nextWord.start) : Number(word.end));
      if (playheadSec >= wordStart && playheadSec <= wordEnd + .01) {
        wordElement.className = "active-word";
        const progress = clamp(((playheadSec - wordStart) / Math.max(wordEnd - wordStart, .01)) * 100, 0, 100);
        wordElement.style.setProperty("--karaoke-progress", `${progress}%`);
      }
      lineElement.appendChild(wordElement);
    });
    return lineElement;
  }));
}

function syncCaptionTextFromWords() {
  captionText.value = editorCaptionWords.map((word) => word.word).join(" ");
}

function renderCaptionSegments() {
  const groups = captionGroupsDetailed();
  captionSegmentCount.textContent = `${groups.length} segment${groups.length === 1 ? "" : "s"}`;
  captionSegmentTrack.replaceChildren();
  groups.forEach((group, index) => {
    const start = Number(group.words[0].start);
    const end = Number(group.words[group.words.length - 1].end);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `caption-track-segment${index === selectedCaptionGroupIndex ? " selected" : ""}`;
    button.style.left = `${(start / clipDurationSec) * 100}%`;
    button.style.width = `${Math.max(((end - start) / clipDurationSec) * 100, 1.4)}%`;
    button.textContent = group.words.map((word) => word.word).join(" ");
    button.title = `${formatTimePrecise(start)} – ${formatTimePrecise(end)} · ${button.textContent}`;
    button.addEventListener("click", () => selectCaptionGroup(index, true));
    captionSegmentTrack.appendChild(button);
  });
  if (!groups.length) {
    const empty = document.createElement("span");
    empty.className = "caption-track-empty";
    empty.textContent = "No caption segments";
    captionSegmentTrack.appendChild(empty);
  }
  if (selectedCaptionGroupIndex >= groups.length) selectedCaptionGroupIndex = -1;
  if (selectedCaptionGroupIndex < 0) captionEditPanel.hidden = true;
}

function selectCaptionGroup(index, seek = false) {
  const groups = captionGroupsDetailed();
  const group = groups[index];
  if (!group) return;
  selectedCaptionGroupIndex = index;
  captionSegmentText.value = group.words.map((word) => word.word).join(" ");
  captionSegmentStart.value = Number(group.words[0].start).toFixed(3);
  captionSegmentEnd.value = Number(group.words[group.words.length - 1].end).toFixed(3);
  captionEditPanel.hidden = false;
  captionEditStatus.textContent = "";
  if (seek) seekTo(Number(group.words[0].start));
  renderCaptionSegments();
}

function setCaptionEditStatus(message, error = false) {
  captionEditStatus.textContent = message;
  captionEditStatus.style.color = error ? "var(--coral)" : "var(--fog-dim)";
}

function distributeCaptionTokens(tokens, start, end, boundary) {
  const step = (end - start) / tokens.length;
  return tokens.map((word, index) => ({
    word,
    start: start + index * step,
    end: Math.min(start + (index + 1) * step, end),
    break_after: index === tokens.length - 1 ? Boolean(boundary.break_after) : false,
    join_next: index === tokens.length - 1 ? Boolean(boundary.join_next) : false,
  }));
}

function updateSelectedCaptionSegment() {
  const groups = captionGroupsDetailed();
  const group = groups[selectedCaptionGroupIndex];
  if (!group) return;
  const tokens = captionSegmentText.value.trim().match(/\S+/g) || [];
  if (!tokens.length) {
    deleteSelectedCaptionSegment();
    return;
  }
  const requestedStart = Number(captionSegmentStart.value);
  const requestedEnd = Number(captionSegmentEnd.value);
  const previousEnd = group.startIndex > 0 ? Number(editorCaptionWords[group.startIndex - 1].end) : 0;
  const nextStart = group.endIndex + 1 < editorCaptionWords.length
    ? Number(editorCaptionWords[group.endIndex + 1].start)
    : clipDurationSec;
  if (!Number.isFinite(requestedStart) || !Number.isFinite(requestedEnd) || requestedStart < previousEnd - .02 || requestedEnd > nextStart + .02 || requestedEnd - requestedStart < .1) {
    setCaptionEditStatus(`Keep timing between ${formatTimePrecise(previousEnd)} and ${formatTimePrecise(nextStart)}.`, true);
    return;
  }
  const start = Math.max(requestedStart, previousEnd);
  const end = Math.min(requestedEnd, nextStart);
  pushUndo();
  const boundary = editorCaptionWords[group.endIndex];
  const replacement = distributeCaptionTokens(tokens, start, end, boundary);
  editorCaptionWords.splice(group.startIndex, group.endIndex - group.startIndex + 1, ...replacement);
  syncCaptionTextFromWords();
  markEditorDirty();
  const newIndex = captionGroupsDetailed().findIndex(({ words }) => Number(words[0].start) >= start - .01);
  selectedCaptionGroupIndex = Math.max(newIndex, 0);
  renderCaptionSegments();
  selectCaptionGroup(selectedCaptionGroupIndex);
  updateCaptionPreview();
  setCaptionEditStatus("Phrase and timing updated.");
}

function splitSelectedCaptionSegment() {
  const groups = captionGroupsDetailed();
  const group = groups[selectedCaptionGroupIndex];
  if (!group || group.words.length < 2) {
    setCaptionEditStatus("This segment needs at least two words before it can be split.", true);
    return;
  }
  const start = Number(group.words[0].start);
  const end = Number(group.words[group.words.length - 1].end);
  if (playheadSec <= start || playheadSec >= end) {
    setCaptionEditStatus("Move the playhead inside the selected segment first.", true);
    return;
  }
  let boundary = group.words.findIndex((word) => Number(word.start) >= playheadSec);
  boundary = clamp(boundary, 1, group.words.length - 1);
  pushUndo();
  const target = editorCaptionWords[group.startIndex + boundary - 1];
  target.break_after = true;
  target.join_next = false;
  markEditorDirty();
  renderCaptionSegments();
  selectCaptionGroup(selectedCaptionGroupIndex);
  updateCaptionPreview();
  setCaptionEditStatus("Segment split at the nearest word boundary.");
}

function mergeSelectedCaptionSegment() {
  const groups = captionGroupsDetailed();
  const current = groups[selectedCaptionGroupIndex];
  const next = groups[selectedCaptionGroupIndex + 1];
  if (!current || !next) {
    setCaptionEditStatus("There is no next segment to merge.", true);
    return;
  }
  const combined = [...current.words, ...next.words];
  const characters = combined.reduce((total, word) => total + word.word.length, 0) + combined.length - 1;
  const duration = Number(combined[combined.length - 1].end) - Number(combined[0].start);
  if (combined.length > 5 || characters > 30 || duration > 2.6) {
    setCaptionEditStatus("Those segments would be too long for a safe 9:16 caption.", true);
    return;
  }
  pushUndo();
  const boundary = editorCaptionWords[current.endIndex];
  boundary.break_after = false;
  boundary.join_next = true;
  markEditorDirty();
  renderCaptionSegments();
  selectCaptionGroup(selectedCaptionGroupIndex);
  updateCaptionPreview();
  setCaptionEditStatus("Segments merged.");
}

function deleteSelectedCaptionSegment() {
  const groups = captionGroupsDetailed();
  const group = groups[selectedCaptionGroupIndex];
  if (!group) return;
  pushUndo();
  editorCaptionWords.splice(group.startIndex, group.endIndex - group.startIndex + 1);
  selectedCaptionGroupIndex = -1;
  syncCaptionTextFromWords();
  markEditorDirty();
  renderCaptionSegments();
  updateCaptionPreview();
}

function rebuildWordsFromTranscript() {
  const tokens = captionText.value.trim().match(/\S+/g) || [];
  if (!tokens.length) {
    editorCaptionWords = [];
  } else {
    editorCaptionWords = distributeCaptionTokens(tokens, 0, clipDurationSec, {});
  }
  selectedCaptionGroupIndex = -1;
  renderCaptionSegments();
  updateCaptionPreview();
}

function updateTimelineRuler() {
  timelineRuler.replaceChildren();
  const ticks = clipDurationSec < 12 ? 5 : 6;
  for (let index = 0; index < ticks; index += 1) {
    const seconds = (index / (ticks - 1)) * clipDurationSec;
    const tick = document.createElement("span");
    tick.className = "timeline-tick";
    tick.style.left = `${(index / (ticks - 1)) * 100}%`;
    tick.textContent = formatTimePrecise(seconds);
    timelineRuler.appendChild(tick);
  }
}

function seekTo(seconds) {
  if (!editingClip) return;
  playheadSec = clamp(Number(seconds) || 0, 0, clipDurationSec);
  updatePlayheadUI();
  updateCaptionPreview();
  syncCropControlsForPlayhead();
  drawEditorPreview();
  const target = playheadSec;
  if (editorVideo.readyState > 0 && Number.isFinite(editorVideo.duration) && Math.abs(editorVideo.currentTime - target) > .02) {
    pendingSeekSec = playheadSec;
    editorVideo.currentTime = target;
  }
}

function updatePlayheadUI() {
  const percentage = clipDurationSec ? (playheadSec / clipDurationSec) * 100 : 0;
  timelinePlayhead.style.left = `${percentage}%`;
  playheadReadout.value = formatTimePrecise(playheadSec);
  playheadReadout.textContent = formatTimePrecise(playheadSec);
  editorTimeline.setAttribute("aria-valuemax", String(clipDurationSec.toFixed(1)));
  editorTimeline.setAttribute("aria-valuenow", String(playheadSec.toFixed(1)));
  setInBtn.disabled = playheadSec > trimEndSec - MIN_TRIM_DURATION;
  setOutBtn.disabled = playheadSec < trimStartSec + MIN_TRIM_DURATION;
  renderCropKeyframes();
}

function updateTrimUI() {
  const startPercentage = clipDurationSec ? (trimStartSec / clipDurationSec) * 100 : 0;
  const endPercentage = clipDurationSec ? (trimEndSec / clipDurationSec) * 100 : 100;
  trimRegion.style.left = `${startPercentage}%`;
  trimRegion.style.right = `${100 - endPercentage}%`;
  trimMaskLeft.style.width = `${startPercentage}%`;
  trimMaskRight.style.width = `${100 - endPercentage}%`;
  if (document.activeElement !== trimStartInput) trimStartInput.value = trimStartSec.toFixed(1);
  if (document.activeElement !== trimEndInput) trimEndInput.value = trimEndSec.toFixed(1);
  trimStartInput.max = Math.max(trimEndSec - MIN_TRIM_DURATION, 0).toFixed(1);
  trimEndInput.max = clipDurationSec.toFixed(1);
  trimEndInput.min = Math.min(trimStartSec + MIN_TRIM_DURATION, clipDurationSec).toFixed(1);
  trimReadout.textContent = `${formatTimePrecise(trimStartSec)} – ${formatTimePrecise(trimEndSec)}`;
  trimDurationReadout.textContent = `${formatTimePrecise(trimEndSec - trimStartSec)} selected`;
  timelineDurationReadout.textContent = `/ ${formatTimePrecise(clipDurationSec)}`;
  trimLeftLabel.textContent = `IN ${formatTimePrecise(trimStartSec)}`;
  trimRightLabel.textContent = `OUT ${formatTimePrecise(trimEndSec)}`;
  trimRegion.setAttribute("aria-label", `Selected range ${formatTimePrecise(trimStartSec)} to ${formatTimePrecise(trimEndSec)}`);
  trimLeft.setAttribute("aria-valuenow", trimStartSec.toFixed(1));
  trimRight.setAttribute("aria-valuenow", trimEndSec.toFixed(1));
  updatePlayheadUI();
  updateCaptionPreview();
  updateSpeechBoundaryStatus();
}

function wordEndsThought(word) {
  return /[.!?]["')\]]*$/.test(String(word?.word || "").trim());
}

function pauseAfterWord(index, seconds = .65) {
  const current = editorCaptionWords[index];
  const next = editorCaptionWords[index + 1];
  return !next || Number(next.start) - Number(current.end) >= seconds;
}

function speechBoundaryCandidates() {
  const starts = [];
  const ends = [];
  editorCaptionWords.forEach((word, index) => {
    const previous = editorCaptionWords[index - 1];
    if (!previous || wordEndsThought(previous) || Number(word.start) - Number(previous.end) >= .65) {
      starts.push(Number(word.start));
    }
    if (wordEndsThought(word) || pauseAfterWord(index)) ends.push(Number(word.end));
  });
  return { starts, ends };
}

function speechBoundaryState() {
  if (!editorCaptionWords.length) return { available: false, startSafe: true, endSafe: true };
  const { starts, ends } = speechBoundaryCandidates();
  const firstIncluded = editorCaptionWords.findIndex((word) => Number(word.end) > trimStartSec + .03);
  let lastIncluded = -1;
  editorCaptionWords.forEach((word, index) => {
    if (Number(word.start) < trimEndSec - .03) lastIncluded = index;
  });
  const startWord = editorCaptionWords[firstIncluded];
  const endWord = editorCaptionWords[lastIncluded];
  const startSafe = Boolean(
    startWord
    && trimStartSec <= Number(startWord.start) + .12
    && starts.some((time) => Math.abs(time - Number(startWord.start)) <= .05)
  );
  const endSafe = Boolean(
    endWord
    && trimEndSec >= Number(endWord.end) - .12
    && ends.some((time) => Math.abs(time - Number(endWord.end)) <= .05)
  );
  return { available: true, startSafe, endSafe, starts, ends };
}

function updateSpeechBoundaryStatus() {
  const state = speechBoundaryState();
  if (!state.available) {
    speechBoundaryStatus.textContent = "Speech timing is unavailable for this clip.";
    speechBoundaryStatus.classList.remove("safe", "warning");
    snapSpeechBoundariesBtn.hidden = true;
    return;
  }
  speechBoundaryStatus.classList.toggle("safe", state.startSafe && state.endSafe);
  speechBoundaryStatus.classList.toggle("warning", !state.startSafe || !state.endSafe);
  if (state.startSafe && state.endSafe) {
    speechBoundaryStatus.textContent = "Speech-safe cut: the clip starts cleanly and ends after a complete phrase.";
    snapSpeechBoundariesBtn.hidden = true;
  } else {
    const problems = [
      !state.startSafe ? "the start may enter during speech" : "",
      !state.endSafe ? "the ending may cut the speaker off" : "",
    ].filter(Boolean);
    speechBoundaryStatus.textContent = `Check the cut: ${problems.join(" and ")}.`;
    snapSpeechBoundariesBtn.hidden = false;
  }
}

snapSpeechBoundariesBtn.addEventListener("click", () => {
  const state = speechBoundaryState();
  if (!state.available) return;
  pushUndo();
  if (!state.startSafe && state.starts.length) {
    trimStartSec = state.starts.reduce((best, time) =>
      Math.abs(time - trimStartSec) < Math.abs(best - trimStartSec) ? time : best
    );
  }
  if (!state.endSafe && state.ends.length) {
    const later = state.ends.filter((time) => time >= trimEndSec - .05);
    const choices = later.length ? later : state.ends;
    trimEndSec = choices.reduce((best, time) =>
      Math.abs(time - trimEndSec) < Math.abs(best - trimEndSec) ? time : best
    );
  }
  trimStartSec = clamp(trimStartSec, 0, Math.max(trimEndSec - MIN_TRIM_DURATION, 0));
  trimEndSec = clamp(trimEndSec, trimStartSec + MIN_TRIM_DURATION, clipDurationSec);
  seekTo(trimStartSec);
  updateTrimUI();
  markEditorDirty();
});

function setTrimStart(seconds, recordUndo = true) {
  const next = clamp(Number(seconds) || 0, 0, Math.max(trimEndSec - MIN_TRIM_DURATION, 0));
  if (Math.abs(next - trimStartSec) < .001) {
    updateTrimUI();
    return;
  }
  if (recordUndo) pushUndo();
  trimStartSec = next;
  seekTo(trimStartSec);
  updateTrimUI();
  markEditorDirty();
}

function setTrimEnd(seconds, recordUndo = true) {
  const parsed = Number(seconds);
  const next = clamp(Number.isFinite(parsed) ? parsed : clipDurationSec, Math.min(trimStartSec + MIN_TRIM_DURATION, clipDurationSec), clipDurationSec);
  if (Math.abs(next - trimEndSec) < .001) {
    updateTrimUI();
    return;
  }
  if (recordUndo) pushUndo();
  trimEndSec = next;
  seekTo(trimEndSec);
  updateTrimUI();
  markEditorDirty();
}

function secondsFromPointer(event) {
  const rect = editorTimeline.getBoundingClientRect();
  return clamp(((event.clientX - rect.left) / rect.width) * clipDurationSec, 0, clipDurationSec);
}

function showTimelineTooltip(seconds) {
  const safeSeconds = clamp(seconds, 0, clipDurationSec);
  const percentage = clipDurationSec ? (safeSeconds / clipDurationSec) * 100 : 0;
  timelineScrubTooltip.style.left = `${percentage}%`;
  timelineScrubTooltip.textContent = formatTimePrecise(safeSeconds);
  timelineScrubTooltip.classList.add("visible");
}

function finishTimelineDrag() {
  if (draggingMode === "selection-pending") seekTo(dragAnchorSec);
  draggingMode = null;
  trimRegion.classList.remove("dragging");
  timelineScrubTooltip.classList.remove("visible");
}

trimLeft.addEventListener("pointerdown", (event) => {
  pushUndo();
  draggingMode = "start";
  markEditorDirty();
  trimRegion.classList.add("dragging");
  showTimelineTooltip(trimStartSec);
  event.preventDefault();
  event.stopPropagation();
});
trimRight.addEventListener("pointerdown", (event) => {
  pushUndo();
  draggingMode = "end";
  markEditorDirty();
  trimRegion.classList.add("dragging");
  showTimelineTooltip(trimEndSec);
  event.preventDefault();
  event.stopPropagation();
});
trimRegion.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".trim-handle")) return;
  draggingMode = "selection-pending";
  dragAnchorSec = secondsFromPointer(event);
  dragInitialStartSec = trimStartSec;
  dragInitialEndSec = trimEndSec;
  dragStartClientX = event.clientX;
  showTimelineTooltip(dragAnchorSec);
  event.preventDefault();
  event.stopPropagation();
});
editorTimeline.addEventListener("pointerdown", (event) => {
  draggingMode = "playhead";
  const seconds = secondsFromPointer(event);
  seekTo(seconds);
  showTimelineTooltip(seconds);
  event.preventDefault();
});
editorTimeline.addEventListener("pointermove", (event) => {
  if (!editingClip) return;
  showTimelineTooltip(secondsFromPointer(event));
});
editorTimeline.addEventListener("pointerleave", () => {
  if (!draggingMode) timelineScrubTooltip.classList.remove("visible");
});
window.addEventListener("pointermove", (event) => {
  if (!draggingMode || !editingClip) return;
  const seconds = secondsFromPointer(event);
  showTimelineTooltip(seconds);
  if (draggingMode === "selection-pending") {
    if (Math.abs(event.clientX - dragStartClientX) < 4) return;
    pushUndo();
    draggingMode = "selection";
    trimRegion.classList.add("dragging");
    markEditorDirty();
  }
  if (draggingMode === "start") setTrimStart(seconds, false);
  else if (draggingMode === "end") setTrimEnd(seconds, false);
  else if (draggingMode === "selection") {
    const selectionDuration = dragInitialEndSec - dragInitialStartSec;
    const offset = seconds - dragAnchorSec;
    trimStartSec = clamp(dragInitialStartSec + offset, 0, Math.max(clipDurationSec - selectionDuration, 0));
    trimEndSec = trimStartSec + selectionDuration;
    seekTo(trimStartSec);
    updateTrimUI();
  } else seekTo(seconds);
});
window.addEventListener("pointerup", finishTimelineDrag);
window.addEventListener("pointercancel", finishTimelineDrag);
editorTimeline.addEventListener("keydown", (event) => {
  if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    seekTo(playheadSec + direction * (event.shiftKey ? 1 : .1));
    event.preventDefault();
  }
});

document.querySelectorAll(".time-nudge").forEach((button) => {
  button.addEventListener("click", () => {
    const amount = Number(button.dataset.nudge);
    if (button.dataset.endpoint === "start") setTrimStart(trimStartSec + amount);
    else setTrimEnd(trimEndSec + amount);
  });
});
function commitTrimInput(endpoint, recordUndo = true) {
  if (endpoint === "start") setTrimStart(trimStartInput.value, recordUndo);
  else setTrimEnd(trimEndInput.value, recordUndo);
}

trimStartInput.addEventListener("change", () => commitTrimInput("start"));
trimEndInput.addEventListener("change", () => commitTrimInput("end"));
trimStartInput.addEventListener("blur", () => commitTrimInput("start"));
trimEndInput.addEventListener("blur", () => commitTrimInput("end"));
[trimStartInput, trimEndInput].forEach((input) => {
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    commitTrimInput(input === trimStartInput ? "start" : "end");
    input.blur();
    event.preventDefault();
  });
});

setInBtn.addEventListener("click", () => setTrimStart(playheadSec));
setOutBtn.addEventListener("click", () => setTrimEnd(playheadSec));
el("jumpInBtn").addEventListener("click", () => seekTo(trimStartSec));
el("jumpOutBtn").addEventListener("click", () => seekTo(trimEndSec));
playPauseBtn.addEventListener("click", async () => {
  if (!editingClip) return;
  if (editorVideo.paused) {
    if (playheadSec < trimStartSec || playheadSec >= trimEndSec) seekTo(trimStartSec);
    await editorVideo.play();
  } else {
    editorVideo.pause();
  }
});

function isTypingTarget(target) {
  return target instanceof HTMLElement && (
    target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)
  );
}

document.addEventListener("keydown", (event) => {
  if (!editingClip || editorSection.hidden || isTypingTarget(event.target)) return;
  if (event.key === " " || event.code === "Space") {
    playPauseBtn.click();
  } else if (event.key.toLowerCase() === "i") {
    if (!setInBtn.disabled) setTrimStart(playheadSec);
  } else if (event.key.toLowerCase() === "o") {
    if (!setOutBtn.disabled) setTrimEnd(playheadSec);
  } else if (event.key === "Home") {
    seekTo(trimStartSec);
  } else if (event.key === "End") {
    seekTo(trimEndSec);
  } else if (["ArrowLeft", "ArrowRight"].includes(event.key) && event.target !== editorTimeline) {
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    seekTo(playheadSec + direction * (event.shiftKey ? 1 : .1));
  } else {
    return;
  }
  event.preventDefault();
});

captionToggle.addEventListener("click", () => {
  pushUndo();
  const next = captionToggle.dataset.on !== "true";
  captionToggle.dataset.on = String(next);
  captionToggle.setAttribute("aria-pressed", String(next));
  captionText.disabled = !next;
  captionPosition.disabled = !next;
  markEditorDirty();
  updateCaptionPreview();
});
captionText.addEventListener("focus", () => {
  if (!transcriptSnapshotTaken) pushUndo();
  transcriptSnapshotTaken = true;
});
captionText.addEventListener("input", () => {
  markEditorDirty();
  window.clearTimeout(transcriptEditTimer);
  transcriptEditTimer = window.setTimeout(() => {
    rebuildWordsFromTranscript();
    transcriptEditTimer = null;
  }, 180);
});
captionText.addEventListener("blur", () => { transcriptSnapshotTaken = false; });
captionPosition.addEventListener("change", () => { markEditorDirty(); updateCaptionPreview(); });
editorVideo.addEventListener("timeupdate", () => {
  if (!editingClip) return;
  if (pendingSeekSec !== null) {
    const actual = clamp(editorVideo.currentTime, 0, clipDurationSec);
    if (Math.abs(actual - pendingSeekSec) > .08) return;
    pendingSeekSec = null;
  }
  playheadSec = clamp(editorVideo.currentTime, 0, clipDurationSec);
  if (!editorVideo.paused && playheadSec >= trimEndSec) {
    editorVideo.pause();
    seekTo(trimStartSec);
  } else {
    updatePlayheadUI();
    updateCaptionPreview();
    syncCropControlsForPlayhead();
    drawEditorPreview();
  }
});
editorVideo.addEventListener("seeked", () => {
  if (!editingClip) return;
  const actual = clamp(editorVideo.currentTime, 0, clipDurationSec);
  if (pendingSeekSec !== null && Math.abs(actual - pendingSeekSec) > .08) return;
  pendingSeekSec = null;
  playheadSec = actual;
  updatePlayheadUI();
  updateCaptionPreview();
  syncCropControlsForPlayhead();
  drawEditorPreview();
});
editorVideo.addEventListener("play", () => {
  if (playheadSec < trimStartSec || playheadSec >= trimEndSec) seekTo(trimStartSec);
  playPauseBtn.textContent = "Ⅱ";
  playPauseBtn.setAttribute("aria-label", "Pause selected clip");
  if (previewAnimationFrame === null) previewAnimationFrame = requestAnimationFrame(animateEditorPreview);
});
editorVideo.addEventListener("pause", () => {
  playPauseBtn.textContent = "▶";
  playPauseBtn.setAttribute("aria-label", "Play selected clip");
  if (previewAnimationFrame !== null) cancelAnimationFrame(previewAnimationFrame);
  previewAnimationFrame = null;
  drawEditorPreview();
});
editorVideo.addEventListener("loadeddata", drawEditorPreview);
window.addEventListener("resize", drawEditorPreview);

el("updateCaptionSegmentBtn").addEventListener("click", updateSelectedCaptionSegment);
el("splitCaptionSegmentBtn").addEventListener("click", splitSelectedCaptionSegment);
el("mergeCaptionSegmentBtn").addEventListener("click", mergeSelectedCaptionSegment);
el("deleteCaptionSegmentBtn").addEventListener("click", deleteSelectedCaptionSegment);

undoEditorBtn.addEventListener("click", () => {
  const state = undoStack.pop();
  if (!state) return;
  restoreEditorState(state);
  undoEditorBtn.disabled = undoStack.length === 0;
  markEditorDirty();
});
el("resetEditorBtn").addEventListener("click", () => editingClip && openEditor(editingClip));

function captionWordsForExport(start, end) {
  return editorCaptionWords
    .filter((word) => Number(word.end) > start && Number(word.start) < end)
    .map((word) => ({
      word: word.word,
      start: Math.max(Number(word.start), start) - start,
      end: Math.min(Number(word.end), end) - start,
      break_after: Boolean(word.break_after),
      join_next: Boolean(word.join_next),
    }))
    .filter((word) => word.end - word.start >= .02)
    .map((word, index, words) => ({
      ...word,
      join_next: index < words.length - 1 ? word.join_next : false,
    }));
}

exportBtn.addEventListener("click", async () => {
  if (!editingClip) return;
  // Typed values may not have emitted change yet, especially on mobile.
  commitTrimInput("start", false);
  commitTrimInput("end", false);
  if (transcriptEditTimer) {
    window.clearTimeout(transcriptEditTimer);
    transcriptEditTimer = null;
    rebuildWordsFromTranscript();
  }
  exportBtn.disabled = true;
  exportBtn.textContent = "Rendering…";
  editorStatus.textContent = "Rendering from the original source. This can take a moment…";
  editorStatus.style.color = "";
  try {
    const reframeMode = selectedValue("reframeModeChoices") || "auto";
    const response = await fetch(`${API_BASE}${editingClip.render_url}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: trimStartSec,
        end: trimEndSec,
        captions: captionToggle.dataset.on === "true",
        output_format: selectedValue("editorFormatChoices") || "vertical",
        caption_style: selectedValue("editorCaptionChoices") || "bold",
        caption_position: captionPosition.value,
        caption_size: selectedValue("captionSizeChoices") || "medium",
        caption_text: captionText.value.trim() || null,
        caption_words: captionWordsForExport(trimStartSec, trimEndSec),
        crop_x: reframeMode === "manual" ? Number(cropFocus.value) / 100 : .5,
        crop_y: reframeMode === "manual" ? Number(cropVertical.value) / 100 : .5,
        crop_zoom: reframeMode === "manual" ? Number(cropZoom.value) / 100 : 1,
        crop_keyframes: reframeMode === "manual" ? cropKeyframesForExport(trimStartSec, trimEndSec) : [],
        reframe_mode: reframeMode,
      }),
    });
    if (!response.ok) throw new Error(await responseError(response, "Export failed."));
    const data = await response.json();
    const renderedUrl = `${API_BASE}${data.preview_url}`;
    editorStatus.innerHTML = `Your edit is ready. <a href="${renderedUrl}" target="_blank" rel="noopener">Preview</a> · <a href="${renderedUrl}" download>Download</a>`;
    markEditorClean();
  } catch (error) {
    editorStatus.textContent = error.message;
    editorStatus.style.color = "var(--coral)";
  } finally {
    exportBtn.disabled = false;
    exportBtn.textContent = "Render this edit";
  }
});

function downloadUrl(url) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function resetUI() {
  clearTimeout(pollTimer);
  stopElapsedClock();
  currentJobId = null;
  currentJobCreatedAt = null;
  errorSection.hidden = true;
  errorRetryBtn.hidden = true;
  errorStage.textContent = "";
  resultsSection.hidden = true;
  progressSection.hidden = true;
  cancelJobBtn.hidden = true;
  retryJobBtn.hidden = true;
  progressMeter.setAttribute("aria-valuenow", "0");
  progressMeterFill.style.width = "0%";
  progressPercent.textContent = "0%";
  progressElapsed.textContent = "Elapsed 0:00";
  progressDetail.textContent = "";
  if (!editorSection.hidden) closeEditor();
  slateHint.style.color = "var(--fog-dim)";
  slateHint.textContent = "Works best with videos under 15 minutes.";
}

function showError(message, stage = "", canRetry = false) {
  errorSection.hidden = false;
  errorDetail.textContent = message;
  errorStage.textContent = stage ? `Stopped during: ${String(stage).replaceAll("_", " ")}` : "";
  errorRetryBtn.hidden = !canRetry;
  errorSection.scrollIntoView({ behavior: "smooth", block: "center" });
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const wholeSeconds = Math.floor(seconds % 60);
  return `${minutes}:${String(wholeSeconds).padStart(2, "0")}`;
}

function formatTimePrecise(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${remainder}`;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

fileInput.addEventListener("change", () => {
  if (!fileInput.files[0]) return;
  urlInput.value = "";
  urlInput.placeholder = fileInput.files[0].name;
});
urlInput.addEventListener("input", () => {
  if (!urlInput.value.trim()) return;
  fileInput.value = "";
  urlInput.placeholder = "Paste a YouTube link…";
});

resumeJobFromUrl();
