import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Download, ExternalLink, LoaderCircle, RotateCcw, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { mediaUrl, request } from "../api";
import type {
  CaptionPosition,
  CaptionSize,
  CaptionStyle,
  CaptionWord,
  Clip,
  FacecamCorner,
  FacecamRegion,
  GamingAnalysis,
  LayoutMode,
  OutputFormat,
  ReframeMode,
  SmartAnalysis,
} from "../types";
import { ClipRail } from "./ClipRail";
import { Inspector } from "./Inspector";
import { PreviewCanvas } from "./PreviewCanvas";
import { Timeline } from "./Timeline";

interface StudioProps {
  clips: Clip[];
}

interface PreviewResponse { preview_url: string; source_width: number; source_height: number }
interface ThumbnailResponse { thumbnails: string[] }
interface RenderResponse { preview_url: string; download_url: string }

const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? value : minimum));

function fallbackRegion(corner: FacecamCorner): FacecamRegion {
  const resolved = corner === "auto" ? "bottom_right" : corner;
  const width = .32;
  const height = .34;
  const inset = .015;
  return {
    x: resolved.endsWith("left") ? inset : 1 - width - inset,
    y: resolved.startsWith("top") ? inset : 1 - height - inset,
    width,
    height,
  };
}

function retimeText(text: string, duration: number): CaptionWord[] {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return [];
  const step = duration / tokens.length;
  return tokens.map((word, index) => ({ word, start: index * step, end: Math.min(duration, (index + 1) * step) }));
}

function normalizedWords(words: CaptionWord[]): string {
  return words.map((word) => word.word).join(" ").replace(/\s+/g, " ").trim();
}

export function Studio({ clips }: StudioProps) {
  const [selectedId, setSelectedId] = useState(clips[0]?.id ?? "");
  const clip = clips.find((item) => item.id === selectedId) ?? clips[0];
  const duration = Math.max(.1, (clip?.end ?? 0) - (clip?.start ?? 0));
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastTimeUpdate = useRef(0);

  const [source, setSource] = useState("");
  const [thumbnails, setThumbnails] = useState<string[]>([]);
  const [previewReady, setPreviewReady] = useState(false);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(duration);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [format, setFormat] = useState<OutputFormat>(clip?.output_format ?? "vertical");
  const [layout, setLayout] = useState<LayoutMode>(clip?.layout_mode ?? "smart");
  const [reframeMode, setReframeMode] = useState<ReframeMode>("auto");
  const [cropX, setCropX] = useState(.5);
  const [cropY, setCropY] = useState(.5);
  const [cropZoom, setCropZoom] = useState(1);
  const [facecamCorner, setFacecamCorner] = useState<FacecamCorner>("auto");
  const [gamingAnalysis, setGamingAnalysis] = useState<GamingAnalysis | null>(null);
  const [gamingLoading, setGamingLoading] = useState(false);
  const [smartAnalysis, setSmartAnalysis] = useState<SmartAnalysis | null>(null);
  const [captionsOn, setCaptionsOn] = useState(true);
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>(clip?.caption_style ?? "bold");
  const [captionSize, setCaptionSize] = useState<CaptionSize>("medium");
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>(clip?.layout_mode === "gameplay_reaction" ? "gaming" : "bottom");
  const [captionText, setCaptionText] = useState(clip?.caption_text ?? "");
  const [rendering, setRendering] = useState(false);
  const [renderedUrl, setRenderedUrl] = useState("");
  const [renderedDownloadUrl, setRenderedDownloadUrl] = useState("");
  const [editorError, setEditorError] = useState("");
  const [resetVersion, setResetVersion] = useState(0);

  const sourceWords = useMemo(
    () => (clip?.caption_words ?? []).map((word) => ({ ...word, start: Number(word.start), end: Number(word.end) })),
    [clip],
  );
  const previewWords = useMemo(
    () => normalizedWords(sourceWords) === captionText.replace(/\s+/g, " ").trim() ? sourceWords : retimeText(captionText, duration),
    [captionText, duration, sourceWords],
  );
  const facecamRegion = gamingAnalysis?.region ?? fallbackRegion(facecamCorner);

  useEffect(() => {
    if (!clip) return;
    let cancelled = false;
    setSource(mediaUrl(clip.preview_url));
    setThumbnails([]);
    setPreviewReady(false);
    setTrimStart(0);
    setTrimEnd(Math.max(.1, clip.end - clip.start));
    setPlayhead(0);
    setPlaying(false);
    setFormat(clip.output_format || "vertical");
    setLayout(clip.layout_mode || "smart");
    setReframeMode("auto");
    setCropX(.5);
    setCropY(.5);
    setCropZoom(1);
    setFacecamCorner("auto");
    setGamingAnalysis(null);
    setSmartAnalysis(null);
    setCaptionStyle(clip.caption_style || "bold");
    setCaptionSize("medium");
    setCaptionPosition(clip.layout_mode === "gameplay_reaction" ? "gaming" : "bottom");
    setCaptionText(clip.caption_text || normalizedWords(clip.caption_words || []));
    setRenderedUrl("");
    setRenderedDownloadUrl("");
    setEditorError("");

    const previewPromise = request<PreviewResponse>(clip.editor_preview_url, { cache: "no-store" }, "Clean preview unavailable");
    const thumbnailsPromise = request<ThumbnailResponse>(clip.thumbnails_url, { cache: "no-store" }, "Timeline unavailable");
    void Promise.allSettled([previewPromise, thumbnailsPromise]).then(([previewResult, thumbnailResult]) => {
      if (cancelled) return;
      if (previewResult.status === "fulfilled") setSource(mediaUrl(previewResult.value.preview_url));
      if (thumbnailResult.status === "fulfilled") setThumbnails(thumbnailResult.value.thumbnails.map(mediaUrl));
    });
    return () => { cancelled = true; };
  }, [clip, resetVersion]);

  useEffect(() => {
    if (!clip || format !== "vertical" || layout !== "gameplay_reaction") return;
    let cancelled = false;
    setGamingLoading(true);
    const path = clip.gaming_analysis_url;
    if (!path) {
      setGamingAnalysis(null);
      setGamingLoading(false);
      return;
    }
    const separator = path.includes("?") ? "&" : "?";
    void request<GamingAnalysis>(`${path}${separator}corner=${facecamCorner}`, { cache: "no-store" }, "Could not analyse the facecam")
      .then((analysis) => { if (!cancelled) setGamingAnalysis(analysis); })
      .catch((error: Error) => { if (!cancelled) { setGamingAnalysis(null); setEditorError(error.message); } })
      .finally(() => { if (!cancelled) setGamingLoading(false); });
    return () => { cancelled = true; };
  }, [clip, facecamCorner, format, layout]);

  useEffect(() => {
    if (!clip || format !== "vertical" || layout !== "smart" || reframeMode !== "auto") return;
    let cancelled = false;
    void request<SmartAnalysis>(clip.reframe_analysis_url, { cache: "no-store" }, "Smart framing unavailable")
      .then((analysis) => { if (!cancelled) setSmartAnalysis(analysis); })
      .catch(() => { if (!cancelled) setSmartAnalysis(null); });
    return () => { cancelled = true; };
  }, [clip, format, layout, reframeMode]);

  const seek = useCallback((value: number) => {
    const target = clamp(value, 0, duration);
    setPlayhead(target);
    if (videoRef.current) videoRef.current.currentTime = target;
  }, [duration]);

  const updatePlayhead = useCallback((value: number) => {
    const now = performance.now();
    if (now - lastTimeUpdate.current < 45 && value < trimEnd - .03) return;
    lastTimeUpdate.current = now;
    if (value >= trimEnd - .015 && videoRef.current && !videoRef.current.paused) {
      videoRef.current.pause();
      videoRef.current.currentTime = trimStart;
      setPlaying(false);
      setPlayhead(trimStart);
      return;
    }
    setPlayhead(value);
  }, [trimEnd, trimStart]);

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      if (video.currentTime < trimStart || video.currentTime >= trimEnd) video.currentTime = trimStart;
      void video.play();
      setPlaying(true);
    } else {
      video.pause();
      setPlaying(false);
    }
  };

  const changeStart = (value: number) => {
    const next = clamp(value, 0, Math.max(0, trimEnd - 1));
    setTrimStart(next);
    seek(next);
  };
  const changeEnd = (value: number) => {
    const next = clamp(value, Math.min(duration, trimStart + 1), duration);
    setTrimEnd(next);
    seek(next);
  };

  const speechSafe = useMemo(() => {
    if (!previewWords.length) return true;
    return previewWords.some((word) => Math.abs(word.start - trimStart) <= .18)
      && previewWords.some((word) => Math.abs(word.end - trimEnd) <= .2);
  }, [previewWords, trimEnd, trimStart]);

  const snapSpeech = () => {
    if (!previewWords.length) return;
    const nearestStart = previewWords.reduce((best, word) => Math.abs(word.start - trimStart) < Math.abs(best - trimStart) ? word.start : best, previewWords[0].start);
    const nearestEnd = previewWords.reduce((best, word) => Math.abs(word.end - trimEnd) < Math.abs(best - trimEnd) ? word.end : best, previewWords[0].end);
    setTrimStart(clamp(nearestStart, 0, Math.max(0, nearestEnd - 1)));
    setTrimEnd(clamp(nearestEnd, nearestStart + 1, duration));
    seek(nearestStart);
  };

  const changeFormat = (value: OutputFormat) => {
    setFormat(value);
    if (value !== "vertical") {
      setLayout("smart");
      if (captionPosition === "gaming") setCaptionPosition("bottom");
    }
  };
  const changeLayout = (value: LayoutMode) => {
    setLayout(value);
    if (value === "gameplay_reaction" && captionPosition === "bottom") setCaptionPosition("gaming");
    if (value === "smart" && captionPosition === "gaming") setCaptionPosition("bottom");
  };

  const renderClip = async () => {
    if (!clip) return;
    setRendering(true);
    setEditorError("");
    setRenderedUrl("");
    setRenderedDownloadUrl("");
    try {
      const words = previewWords
        .filter((word) => word.end > trimStart && word.start < trimEnd)
        .map((word) => ({
          ...word,
          start: Math.max(word.start, trimStart) - trimStart,
          end: Math.min(word.end, trimEnd) - trimStart,
          break_after: Boolean(word.break_after),
          join_next: Boolean(word.join_next),
        }))
        .filter((word) => word.end - word.start >= .02);
      const result = await request<RenderResponse>(clip.render_url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: trimStart,
          end: trimEnd,
          captions: captionsOn,
          output_format: format,
          caption_style: captionStyle,
          caption_position: format === "vertical" && layout === "gameplay_reaction" && captionPosition === "bottom" ? "gaming" : captionPosition,
          caption_size: captionSize,
          caption_text: captionText.trim() || null,
          caption_words: words,
          crop_x: cropX,
          crop_y: cropY,
          crop_zoom: cropZoom,
          crop_keyframes: [],
          reframe_mode: reframeMode,
          layout_mode: format === "vertical" ? layout : "smart",
          facecam_corner: facecamCorner,
          facecam_region: format === "vertical" && layout === "gameplay_reaction" ? facecamRegion : null,
        }),
      }, "Could not render this edit.");
      setRenderedUrl(mediaUrl(result.preview_url));
      setRenderedDownloadUrl(mediaUrl(result.download_url));
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : "Could not render this edit.");
    } finally {
      setRendering(false);
    }
  };

  if (!clip) return null;
  return (
    <main className="workspace-shell">
      <ClipRail clips={clips} selectedId={clip.id} onSelect={(next) => setSelectedId(next.id)} />
      <section className="studio">
        <header className="studio-header">
          <div><span className="eyebrow"><Sparkles size={13} /> AI-selected clip</span><h1>{clip.title}</h1></div>
          <div className="score-badge"><b>{clip.score}</b><span>Quality</span></div>
        </header>

        <div className="studio-grid">
          <div className="stage-column">
            <div className="stage-surface">
              {!previewReady ? <div className="preview-loading"><LoaderCircle className="spin" /><span>Preparing exact preview…</span></div> : null}
              <PreviewCanvas
                videoRef={videoRef}
                source={source}
                format={format}
                layout={layout}
                reframeMode={reframeMode}
                cropX={cropX}
                cropY={cropY}
                cropZoom={cropZoom}
                facecamRegion={facecamRegion}
                smartAnalysis={smartAnalysis}
                captionsOn={captionsOn}
                captionStyle={captionStyle}
                captionPosition={captionPosition}
                captionSize={captionSize}
                captionWords={previewWords}
                playhead={playhead}
                onReady={() => setPreviewReady(true)}
                onTimeUpdate={updatePlayhead}
                onEnded={() => setPlaying(false)}
              />
            </div>
            <p className="preview-help">This preview matches the selected canvas and layout. Rendering always uses the original source.</p>
          </div>
          <Inspector
            format={format}
            layout={layout}
            reframeMode={reframeMode}
            cropX={cropX}
            cropY={cropY}
            cropZoom={cropZoom}
            gamingAnalysis={gamingAnalysis}
            gamingLoading={gamingLoading}
            smartAnalysis={smartAnalysis}
            facecamCorner={facecamCorner}
            captionsOn={captionsOn}
            captionStyle={captionStyle}
            captionSize={captionSize}
            captionPosition={captionPosition}
            captionText={captionText}
            onFormat={changeFormat}
            onLayout={changeLayout}
            onReframe={setReframeMode}
            onCrop={(axis, value) => axis === "x" ? setCropX(value) : axis === "y" ? setCropY(value) : setCropZoom(value)}
            onFacecamCorner={setFacecamCorner}
            onCaptionsOn={setCaptionsOn}
            onCaptionStyle={setCaptionStyle}
            onCaptionSize={setCaptionSize}
            onCaptionPosition={setCaptionPosition}
            onCaptionText={setCaptionText}
          />
        </div>

        <Timeline
          duration={duration}
          start={trimStart}
          end={trimEnd}
          playhead={playhead}
          playing={playing}
          thumbnails={thumbnails}
          words={previewWords}
          speechSafe={speechSafe}
          onStartChange={changeStart}
          onEndChange={changeEnd}
          onSeek={seek}
          onTogglePlay={togglePlay}
          onSnapSpeech={snapSpeech}
        />

        <AnimatePresence mode="wait">
          {renderedUrl ? (
            <motion.div className="render-result" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <span><Check size={18} /> Your edited clip is ready.</span>
              <div><a href={renderedUrl} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Preview</a><a href={renderedDownloadUrl || renderedUrl} download><Download size={15} /> Download</a></div>
            </motion.div>
          ) : editorError ? <motion.p className="editor-error" role="alert" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>{editorError}</motion.p> : null}
        </AnimatePresence>

        <footer className="studio-footer">
          <button type="button" className="button ghost" onClick={() => setResetVersion((version) => version + 1)}><RotateCcw size={16} /> Reset changes</button>
          <button type="button" className="button primary render-button" disabled={rendering} onClick={() => void renderClip()}>
            {rendering ? <LoaderCircle size={18} className="spin" /> : <Sparkles size={18} />}
            {rendering ? "Rendering your clip…" : "Render this edit"}
          </button>
        </footer>
      </section>
    </main>
  );
}
