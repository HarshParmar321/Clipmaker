import { Captions, Focus, Gamepad2, LayoutTemplate, ScanFace, SlidersHorizontal } from "lucide-react";
import type {
  CaptionPosition,
  CaptionSize,
  CaptionStyle,
  FacecamCorner,
  GamingAnalysis,
  LayoutMode,
  OutputFormat,
  ReframeMode,
  SmartAnalysis,
} from "../types";
import { SegmentedControl } from "./SegmentedControl";

interface InspectorProps {
  format: OutputFormat;
  layout: LayoutMode;
  reframeMode: ReframeMode;
  cropX: number;
  cropY: number;
  cropZoom: number;
  gamingAnalysis: GamingAnalysis | null;
  gamingLoading: boolean;
  smartAnalysis: SmartAnalysis | null;
  facecamCorner: FacecamCorner;
  captionsOn: boolean;
  captionStyle: CaptionStyle;
  captionSize: CaptionSize;
  captionPosition: CaptionPosition;
  captionText: string;
  onFormat: (value: OutputFormat) => void;
  onLayout: (value: LayoutMode) => void;
  onReframe: (value: ReframeMode) => void;
  onCrop: (axis: "x" | "y" | "zoom", value: number) => void;
  onFacecamCorner: (value: FacecamCorner) => void;
  onCaptionsOn: (value: boolean) => void;
  onCaptionStyle: (value: CaptionStyle) => void;
  onCaptionSize: (value: CaptionSize) => void;
  onCaptionPosition: (value: CaptionPosition) => void;
  onCaptionText: (value: string) => void;
}

const CORNERS: Array<{ value: FacecamCorner; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "top_left", label: "↖" },
  { value: "top_right", label: "↗" },
  { value: "bottom_left", label: "↙" },
  { value: "bottom_right", label: "↘" },
];

export function Inspector(props: InspectorProps) {
  const gaming = props.format === "vertical" && props.layout === "gameplay_reaction";
  return (
    <aside className="inspector">
      <div className="inspector-heading"><SlidersHorizontal size={17} /><span>Clip settings</span></div>

      <section className="inspector-section">
        <h3><LayoutTemplate size={16} /> Canvas</h3>
        <SegmentedControl label="Output format" value={props.format} onChange={props.onFormat} options={[
          { value: "vertical", label: "9:16" },
          { value: "landscape", label: "16:9" },
          { value: "original", label: "Original" },
        ]} compact />
      </section>

      {props.format === "vertical" ? (
        <section className="inspector-section">
          <h3><Gamepad2 size={16} /> Layout</h3>
          <SegmentedControl label="Composition" value={props.layout} onChange={props.onLayout} options={[
            { value: "smart", label: "Speaker" },
            { value: "gameplay_reaction", label: "Game + face" },
          ]} compact />
          {gaming ? (
            <div className="analysis-card">
              <div><span className={`status-light ${props.gamingLoading ? "loading" : props.gamingAnalysis?.used_fallback ? "warning" : "ready"}`} /><b>{props.gamingLoading ? "Finding facecam…" : props.gamingAnalysis?.used_fallback ? "Check the corner" : "Facecam detected"}</b></div>
              <p>{props.gamingAnalysis?.message || "Gameplay stays above your enlarged reaction camera."}</p>
              <span className="micro-label">Facecam position in source</span>
              <div className="corner-grid">
                {CORNERS.map((corner) => <button key={corner.value} type="button" className={props.facecamCorner === corner.value ? "active" : ""} onClick={() => props.onFacecamCorner(corner.value)}>{corner.label}</button>)}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {props.format !== "original" ? (
        <section className="inspector-section">
          <h3><Focus size={16} /> Framing</h3>
          <SegmentedControl label="Camera movement" value={props.reframeMode} onChange={props.onReframe} options={[
            { value: "auto", label: gaming ? "Centered" : "Smart auto" },
            { value: "manual", label: "Manual" },
          ]} compact />
          {props.reframeMode === "auto" && !gaming ? (
            <div className="analysis-card compact-analysis">
              <div><ScanFace size={15} /><b>{props.smartAnalysis ? `${Math.round(props.smartAnalysis.detection_ratio * 100)}% subject lock` : "Analysing subject…"}</b></div>
              <p>{props.smartAnalysis?.message || "The camera path follows the likely speaker and smooths movement."}</p>
            </div>
          ) : null}
          {props.reframeMode === "manual" ? (
            <div className="slider-stack">
              <label><span>Horizontal <b>{Math.round(props.cropX * 100)}%</b></span><input type="range" min={0} max={1} step={.01} value={props.cropX} onChange={(event) => props.onCrop("x", Number(event.target.value))} /></label>
              <label><span>Vertical <b>{Math.round(props.cropY * 100)}%</b></span><input type="range" min={0} max={1} step={.01} value={props.cropY} onChange={(event) => props.onCrop("y", Number(event.target.value))} /></label>
              {!gaming ? <label><span>Zoom <b>{props.cropZoom.toFixed(2)}×</b></span><input type="range" min={1} max={2} step={.05} value={props.cropZoom} onChange={(event) => props.onCrop("zoom", Number(event.target.value))} /></label> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="inspector-section captions-section">
        <h3><Captions size={16} /> Captions <button type="button" role="switch" aria-checked={props.captionsOn} className={`switch ${props.captionsOn ? "on" : ""}`} onClick={() => props.onCaptionsOn(!props.captionsOn)}><i /></button></h3>
        {props.captionsOn ? (
          <>
            <SegmentedControl label="Style" value={props.captionStyle} onChange={props.onCaptionStyle} options={[
              { value: "bold", label: "Bold" }, { value: "clean", label: "Clean" }, { value: "karaoke", label: "Karaoke" },
            ]} compact />
            <div className="two-field-row">
              <label>Size<select value={props.captionSize} onChange={(event) => props.onCaptionSize(event.target.value as CaptionSize)}><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
              <label>Position<select value={props.captionPosition} onChange={(event) => props.onCaptionPosition(event.target.value as CaptionPosition)}><option value="bottom">Bottom</option>{gaming ? <option value="gaming">Game boundary</option> : null}<option value="center">Center</option><option value="top">Top</option></select></label>
            </div>
            <label className="transcript-field">Transcript<textarea rows={5} value={props.captionText} onChange={(event) => props.onCaptionText(event.target.value)} /></label>
          </>
        ) : null}
      </section>
    </aside>
  );
}
