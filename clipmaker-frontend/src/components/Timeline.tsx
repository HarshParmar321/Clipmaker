import { Pause, Play, SkipBack, SkipForward } from "lucide-react";
import type { CaptionWord } from "../types";

interface TimelineProps {
  duration: number;
  start: number;
  end: number;
  playhead: number;
  playing: boolean;
  thumbnails: string[];
  words: CaptionWord[];
  speechSafe: boolean;
  onStartChange: (value: number) => void;
  onEndChange: (value: number) => void;
  onSeek: (value: number) => void;
  onTogglePlay: () => void;
  onSnapSpeech: () => void;
}

const percent = (value: number, duration: number) => `${duration ? value / duration * 100 : 0}%`;

export function formatTime(value: number): string {
  const safe = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(safe / 60);
  return `${minutes}:${(safe % 60).toFixed(1).padStart(4, "0")}`;
}

export function Timeline({
  duration, start, end, playhead, playing, thumbnails, words, speechSafe,
  onStartChange, onEndChange, onSeek, onTogglePlay, onSnapSpeech,
}: TimelineProps) {
  const selectedDuration = Math.max(0, end - start);
  return (
    <section className="timeline-panel" aria-label="Trim clip">
      <div className="transport-row">
        <div className="transport-buttons">
          <button type="button" onClick={() => onSeek(start)} aria-label="Jump to clip start"><SkipBack size={17} /></button>
          <button type="button" className="play-button" onClick={onTogglePlay} aria-label={playing ? "Pause clip" : "Play selected clip"}>
            {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
          </button>
          <button type="button" onClick={() => onSeek(end)} aria-label="Jump to clip end"><SkipForward size={17} /></button>
        </div>
        <div className="time-display"><strong>{formatTime(playhead)}</strong><span>/ {formatTime(duration)}</span></div>
        <div className="selection-duration"><span>Selected</span><b>{formatTime(selectedDuration)}</b></div>
      </div>

      <div
        className="timeline-visual"
        onPointerDown={(event) => {
          if ((event.target as HTMLElement).closest("input")) return;
          const rect = event.currentTarget.getBoundingClientRect();
          onSeek(Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration)));
        }}
      >
        <div className="thumbnail-strip">
          {thumbnails.length ? thumbnails.map((source, index) => <img key={`${source}-${index}`} src={source} alt="" />) : <span>Timeline previews are loading…</span>}
        </div>
        <div className="timeline-shade before" style={{ width: percent(start, duration) }} />
        <div className="timeline-shade after" style={{ left: percent(end, duration) }} />
        <div className="trim-selection" style={{ left: percent(start, duration), right: `${100 - (duration ? end / duration * 100 : 0)}%` }} />
        <div className="playhead" style={{ left: percent(playhead, duration) }}><i /></div>
        <input className="trim-slider start" aria-label="Clip start" type="range" min={0} max={duration} step={.05} value={start} onChange={(event) => onStartChange(Number(event.target.value))} />
        <input className="trim-slider end" aria-label="Clip end" type="range" min={0} max={duration} step={.05} value={end} onChange={(event) => onEndChange(Number(event.target.value))} />
        <span className="trim-tag in" style={{ left: percent(start, duration) }}>IN {formatTime(start)}</span>
        <span className="trim-tag out" style={{ left: percent(end, duration) }}>OUT {formatTime(end)}</span>
      </div>

      <div className="caption-lane" aria-label="Caption timing">
        <span>Speech</span>
        <div>{words.map((word, index) => (
          <i key={`${word.start}-${index}`} style={{ left: percent(word.start, duration), width: `${Math.max(.35, (word.end - word.start) / duration * 100)}%` }} title={word.word} />
        ))}</div>
      </div>

      <div className="trim-footer">
        <div className="time-field">
          <label htmlFor="trim-start">Start</label>
          <button type="button" onClick={() => onStartChange(start - .1)}>−0.1</button>
          <input id="trim-start" type="number" min={0} max={end - 1} step={.1} value={start.toFixed(1)} onChange={(event) => onStartChange(Number(event.target.value))} />
          <button type="button" onClick={() => onStartChange(start + .1)}>+0.1</button>
        </div>
        <div className={`speech-status ${speechSafe ? "safe" : "warning"}`}>
          <span />
          {speechSafe ? "Clean start and complete ending" : "A sentence may be cut at the trim point"}
          {!speechSafe ? <button type="button" onClick={onSnapSpeech}>Fix automatically</button> : null}
        </div>
        <div className="time-field">
          <label htmlFor="trim-end">End</label>
          <button type="button" onClick={() => onEndChange(end - .1)}>−0.1</button>
          <input id="trim-end" type="number" min={start + 1} max={duration} step={.1} value={end.toFixed(1)} onChange={(event) => onEndChange(Number(event.target.value))} />
          <button type="button" onClick={() => onEndChange(end + .1)}>+0.1</button>
        </div>
      </div>
    </section>
  );
}
