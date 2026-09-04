import { useRef, useState } from "react";
import { ArrowRight, Check, Film, Gamepad2, Link2, Upload } from "lucide-react";
import { motion } from "motion/react";
import type { CaptionStyle, ContentMode, CreateJobSettings, OutputFormat } from "../types";
import { SegmentedControl } from "./SegmentedControl";

interface CreateJobPanelProps {
  busy: boolean;
  onCreate: (source: { url?: string; file?: File }, settings: CreateJobSettings) => Promise<void>;
}

const LENGTHS = {
  quick: { min: 15, max: 30 },
  balanced: { min: 20, max: 60 },
  deep: { min: 45, max: 90 },
} as const;

const PROMPTS = ["Strong opinions", "Useful advice", "Funny reactions"] as const;

export function CreateJobPanel({ busy, onCreate }: CreateJobPanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [sourceTab, setSourceTab] = useState<"url" | "upload">("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [prompt, setPrompt] = useState("");
  const [contentMode, setContentMode] = useState<ContentMode>("general");
  const [format, setFormat] = useState<OutputFormat>("vertical");
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>("bold");
  const [count, setCount] = useState<3 | 5 | 8>(5);
  const [length, setLength] = useState<keyof typeof LENGTHS>("balanced");
  const [error, setError] = useState("");

  const chooseContent = (value: ContentMode) => {
    setContentMode(value);
    if (value === "gaming") setFormat("vertical");
  };

  const submit = async () => {
    if (sourceTab === "url" && !url.trim()) return setError("Paste a YouTube link to continue.");
    if (sourceTab === "upload" && !file) return setError("Choose a video file to continue.");
    setError("");
    const timing = LENGTHS[length];
    await onCreate(
      sourceTab === "url" ? { url: url.trim() } : { file: file ?? undefined },
      {
        creative_prompt: prompt.trim() || null,
        output_format: format,
        caption_style: captionStyle,
        content_mode: contentMode,
        target_clip_count: count,
        min_clip_duration: timing.min,
        max_clip_duration: timing.max,
      },
    );
  };

  return (
    <main className="director-home">
      <motion.section className="director-hero" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .4 }}>
        <p className="director-kicker">AI clip director / 01</p>
        <h1>Turn long videos into<br /><span>clips people finish.</span></h1>
        <p className="director-summary">Find the moments worth sharing, frame them for any screen, and finish them with captions that feel intentional.</p>
      </motion.section>

      <motion.section className="project-slate" id="create-project" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .45, delay: .08 }}>
        <div className="slate-stripes" aria-hidden="true" />
        <div className="slate-header">
          <div>
            <span>New project</span>
            <strong>Choose your source</strong>
          </div>
          <span className="slate-take">Take 01</span>
        </div>

        <div className="slate-source">
          <div className="source-tabs" role="tablist" aria-label="Video source">
            <button type="button" role="tab" aria-selected={sourceTab === "url"} className={sourceTab === "url" ? "active" : ""} onClick={() => setSourceTab("url")}><Link2 size={15} /> YouTube link</button>
            <button type="button" role="tab" aria-selected={sourceTab === "upload"} className={sourceTab === "upload" ? "active" : ""} onClick={() => setSourceTab("upload")}><Upload size={15} /> Upload file</button>
          </div>

          {sourceTab === "url" ? (
            <label className="source-input slate-url">
              <Link2 size={18} />
              <input aria-label="YouTube video URL" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="Paste a YouTube video or livestream link" />
              <span>youtube.com</span>
            </label>
          ) : (
            <>
              <button type="button" className="upload-drop slate-upload" onClick={() => fileInput.current?.click()}>
                <span className="upload-icon"><Upload size={18} /></span>
                <span>{file ? file.name : "Choose a video from your device"}</span>
                <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : "MP4, MOV or WEBM"}</small>
              </button>
              <input ref={fileInput} type="file" accept="video/*" hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
            </>
          )}
        </div>

        <div className="slate-grid">
          <section className="slate-block">
            <div className="slate-label"><span>02</span><div><strong>What are we cutting?</strong><small>Changes how moments are found and framed.</small></div></div>
            <div className="director-modes" role="radiogroup" aria-label="Content type">
              <button type="button" role="radio" aria-checked={contentMode === "general"} className={contentMode === "general" ? "active" : ""} onClick={() => chooseContent("general")}>
                <Film size={18} /><span><b>General video</b><small>Podcasts, interviews, talks</small></span><i />
              </button>
              <button type="button" role="radio" aria-checked={contentMode === "gaming"} className={contentMode === "gaming" ? "active" : ""} onClick={() => chooseContent("gaming")}>
                <Gamepad2 size={18} /><span><b>Gaming stream</b><small>Gameplay with OBS facecam</small></span><i />
              </button>
            </div>

            <label className="prompt-field director-prompt">
              <span>Creative direction <em>Optional</em></span>
              <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={1000} placeholder={contentMode === "gaming" ? "Clutch wins, funny failures, strong reactions…" : "Surprising insights, practical advice, strong opinions…"} />
            </label>
            <div className="prompt-chips" aria-label="Creative direction suggestions">
              {PROMPTS.map((value) => <button type="button" key={value} onClick={() => setPrompt(`Find ${value.toLowerCase()}.`)}>{value}</button>)}
            </div>
          </section>

          <section className="slate-block output-block">
            <div className="slate-label"><span>03</span><div><strong>Output</strong><small>Ready-to-publish defaults. Adjust if needed.</small></div></div>
            <div className="settings-grid director-settings">
              <SegmentedControl label="Format" value={format} onChange={setFormat} options={[
                { value: "vertical", label: "9:16", detail: "Shorts" },
                { value: "landscape", label: "16:9", detail: "Wide", disabled: contentMode === "gaming" },
                { value: "original", label: "Original", detail: "Uncropped", disabled: contentMode === "gaming" },
              ]} />
              <SegmentedControl label="Captions" value={captionStyle} onChange={setCaptionStyle} options={[
                { value: "bold", label: "Bold" }, { value: "clean", label: "Clean" }, { value: "karaoke", label: "Karaoke" },
              ]} compact />
              <SegmentedControl label="Clips" value={String(count) as "3" | "5" | "8"} onChange={(value) => setCount(Number(value) as 3 | 5 | 8)} options={[
                { value: "3", label: "3" }, { value: "5", label: "5" }, { value: "8", label: "8" },
              ]} compact />
              <SegmentedControl label="Length" value={length} onChange={setLength} options={[
                { value: "quick", label: "Quick", detail: "15–30s" },
                { value: "balanced", label: "Balanced", detail: "20–60s" },
                { value: "deep", label: "Deep", detail: "45–90s" },
              ]} />
            </div>
          </section>
        </div>

        <footer className="slate-footer">
          <p><Check size={13} /> Private media <span /> Auto cleanup <span /> Saved progress</p>
          <div>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <button type="button" className="button primary director-submit" disabled={busy} onClick={() => void submit()}>
              {busy ? "Starting project…" : "Direct my clips"}<ArrowRight size={18} />
            </button>
          </div>
        </footer>
      </motion.section>

      <section className="director-process" id="how-it-works">
        <div className="process-heading"><span>How it works</span><strong>From long-form to finished clips.</strong></div>
        <ol>
          <li><span>01</span><div><b>Find the moment</b><p>Speech, reactions, context and payoff—not random timestamps.</p></div></li>
          <li><span>02</span><div><b>Build the frame</b><p>Speaker-aware crops or gameplay-first layouts with facecam.</p></div></li>
          <li><span>03</span><div><b>Finish the cut</b><p>Natural in-and-out points, readable captions, ready to export.</p></div></li>
        </ol>
      </section>
    </main>
  );
}
