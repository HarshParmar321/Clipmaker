import { useCallback, useEffect, useRef } from "react";
import type { RefObject } from "react";
import type {
  CaptionPosition,
  CaptionSize,
  CaptionStyle,
  CaptionWord,
  FacecamRegion,
  LayoutMode,
  OutputFormat,
  ReframeMode,
  SmartAnalysis,
} from "../types";

interface PreviewCanvasProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  source: string;
  format: OutputFormat;
  layout: LayoutMode;
  reframeMode: ReframeMode;
  cropX: number;
  cropY: number;
  cropZoom: number;
  facecamRegion: FacecamRegion;
  smartAnalysis: SmartAnalysis | null;
  captionsOn: boolean;
  captionStyle: CaptionStyle;
  captionPosition: CaptionPosition;
  captionSize: CaptionSize;
  captionWords: CaptionWord[];
  playhead: number;
  onReady: (width: number, height: number) => void;
  onTimeUpdate: (time: number) => void;
  onEnded: () => void;
}

const clamp = (value: number, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, value));

function interpolateSmart(analysis: SmartAnalysis | null, time: number) {
  const points = analysis?.points ?? [];
  if (!points.length) return { x: .5, y: .5 };
  if (time <= points[0].time) return points[0];
  if (time >= points[points.length - 1].time) return points[points.length - 1];
  const index = points.findIndex((point) => point.time >= time);
  const before = points[index - 1];
  const after = points[index];
  const progress = (time - before.time) / Math.max(after.time - before.time, .001);
  return { x: before.x + (after.x - before.x) * progress, y: before.y + (after.y - before.y) * progress };
}

function drawCover(
  context: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  region: FacecamRegion,
  destination: { x: number; y: number; width: number; height: number },
  focus: { x: number; y: number },
  zoom = 1,
) {
  const sourceX = clamp(region.x) * video.videoWidth;
  const sourceY = clamp(region.y) * video.videoHeight;
  const sourceWidth = clamp(region.width, .02, 1 - region.x) * video.videoWidth;
  const sourceHeight = clamp(region.height, .02, 1 - region.y) * video.videoHeight;
  const targetRatio = destination.width / Math.max(destination.height, 1);
  const sourceRatio = sourceWidth / Math.max(sourceHeight, 1);
  let cropWidth = sourceRatio > targetRatio ? sourceHeight * targetRatio : sourceWidth;
  let cropHeight = sourceRatio > targetRatio ? sourceHeight : sourceWidth / targetRatio;
  cropWidth /= zoom;
  cropHeight /= zoom;
  const sx = sourceX + (sourceWidth - cropWidth) * clamp(focus.x);
  const sy = sourceY + (sourceHeight - cropHeight) * clamp(focus.y);
  context.drawImage(video, sx, sy, cropWidth, cropHeight, destination.x, destination.y, destination.width, destination.height);
}

function activeCaption(words: CaptionWord[], playhead: number): { words: string[]; active: number } | null {
  const wordIndex = words.findIndex((word) => playhead >= word.start - .03 && playhead <= word.end + .08);
  if (wordIndex < 0) return null;
  let start = wordIndex;
  while (start > 0 && wordIndex - start < 4 && words[start].start - words[start - 1].end < .55) start -= 1;
  const visible = words.slice(start, start + 5);
  return { words: visible.map((word) => word.word), active: wordIndex - start };
}

export function PreviewCanvas(props: PreviewCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const {
    videoRef, source, format, layout, reframeMode, cropX, cropY, cropZoom,
    facecamRegion, smartAnalysis, captionsOn, captionStyle, captionPosition,
    captionSize, captionWords, playhead, onReady, onTimeUpdate, onEnded,
  } = props;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    const video = videoRef.current;
    if (!canvas || !stage || !video) return;
    const rect = stage.getBoundingClientRect();
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(2, Math.round(rect.width * scale));
    const height = Math.max(2, Math.round(rect.height * scale));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.fillStyle = "#030405";
    context.fillRect(0, 0, width, height);
    if (video.readyState < 2 || !video.videoWidth) return;
    try {
      if (format === "vertical" && layout === "gameplay_reaction") {
        const gameplayHeight = Math.round(height * .65);
        drawCover(context, video, { x: 0, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width, height: gameplayHeight }, reframeMode === "manual" ? { x: cropX, y: cropY } : { x: .5, y: .5 });
        drawCover(context, video, facecamRegion, { x: 0, y: gameplayHeight, width, height: height - gameplayHeight }, { x: .5, y: .42 });
        context.fillStyle = "rgba(3,4,5,.94)";
        context.fillRect(0, gameplayHeight - 3 * scale, width, 6 * scale);
      } else if (format === "original") {
        context.drawImage(video, 0, 0, width, height);
      } else {
        const focus = reframeMode === "auto" && format === "vertical"
          ? interpolateSmart(smartAnalysis, video.currentTime)
          : { x: cropX, y: cropY };
        drawCover(context, video, { x: 0, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width, height }, focus, reframeMode === "manual" ? cropZoom : smartAnalysis?.zoom ?? 1);
      }
    } catch {
      // A loaded metadata event can arrive one frame before drawing is possible.
    }
  }, [videoRef, format, layout, reframeMode, cropX, cropY, cropZoom, facecamRegion, smartAnalysis]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(draw);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [draw]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let frame = 0;
    const loop = () => {
      draw();
      onTimeUpdate(video.currentTime);
      if (!video.paused && !video.ended) frame = requestAnimationFrame(loop);
    };
    const begin = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(loop); };
    const redraw = () => { draw(); onTimeUpdate(video.currentTime); };
    video.addEventListener("play", begin);
    video.addEventListener("seeked", redraw);
    video.addEventListener("loadeddata", redraw);
    return () => {
      cancelAnimationFrame(frame);
      video.removeEventListener("play", begin);
      video.removeEventListener("seeked", redraw);
      video.removeEventListener("loadeddata", redraw);
    };
  }, [draw, onTimeUpdate, videoRef]);

  useEffect(draw, [draw, playhead]);
  const caption = captionsOn ? activeCaption(captionWords, playhead) : null;
  const ratio = format === "vertical" ? "9 / 16" : format === "landscape" ? "16 / 9" : "var(--source-ratio, 16 / 9)";

  return (
    <div className="preview-wrap">
      <div ref={stageRef} className={`preview-stage ${format}`} style={{ aspectRatio: ratio }}>
        <canvas ref={canvasRef} aria-label="Clip export preview" />
        {caption ? (
          <div className={`caption-overlay ${captionStyle} ${captionPosition} ${captionSize}`}>
            {caption.words.map((word, index) => (
              <span key={`${word}-${index}`} className={`caption-word ${index === caption.active ? "active" : ""}`}>{word}</span>
            ))}
          </div>
        ) : null}
      </div>
      <video
        ref={videoRef}
        className="source-video"
        src={source}
        crossOrigin="anonymous"
        playsInline
        preload="auto"
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          stageRef.current?.style.setProperty("--source-ratio", `${video.videoWidth} / ${video.videoHeight}`);
          onReady(video.videoWidth, video.videoHeight);
          draw();
        }}
        onEnded={onEnded}
      />
    </div>
  );
}
