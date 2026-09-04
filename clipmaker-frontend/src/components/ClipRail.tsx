import { Download, Play, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { mediaUrl } from "../api";
import type { Clip } from "../types";

interface ClipRailProps {
  clips: Clip[];
  selectedId: string;
  onSelect: (clip: Clip) => void;
}

function duration(clip: Clip): string {
  const seconds = Math.max(0, clip.end - clip.start);
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

export function ClipRail({ clips, selectedId, onSelect }: ClipRailProps) {
  return (
    <aside className="clip-rail">
      <div className="rail-heading">
        <div><span>Your clips</span><b>{clips.length}</b></div>
        <small>Ranked by Clipmaker</small>
      </div>
      <div className="rail-list">
        {clips.map((clip, index) => (
          <motion.article
            layout
            key={clip.id}
            className={`rail-card ${selectedId === clip.id ? "selected" : ""}`}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: Math.min(index * .045, .2) }}
          >
            <button type="button" className="rail-select" onClick={() => onSelect(clip)} aria-label={`Edit ${clip.title}`}>
              <span className={`rail-media ${clip.output_format}`}>
                <video src={mediaUrl(clip.preview_url)} muted preload="metadata" playsInline />
                <i><Play size={13} fill="currentColor" /></i>
                <em>{duration(clip)}</em>
              </span>
              <span className="rail-copy">
                <b>{clip.title}</b>
                <small><Sparkles size={11} /> {clip.score}% quality</small>
              </span>
            </button>
            <a className="rail-download" href={mediaUrl(clip.download_url)} download title="Download clip" aria-label={`Download ${clip.title}`}><Download size={15} /></a>
          </motion.article>
        ))}
      </div>
    </aside>
  );
}
