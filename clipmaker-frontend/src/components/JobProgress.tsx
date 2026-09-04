import { ArrowUpRight, Check, CircleStop, LoaderCircle, RotateCcw } from "lucide-react";
import { motion } from "motion/react";
import type { JobResponse } from "../types";

interface JobProgressProps {
  job: JobResponse;
  actionBusy: boolean;
  onCancel: () => void;
  onRetry: () => void;
  onNewProject: () => void;
}

const STAGES = [
  ["downloading", "Import"],
  ["transcribing", "Transcript"],
  ["detecting_highlights", "Highlights"],
  ["cutting_clips", "Cut"],
  ["burning_captions", "Finish"],
] as const;

export function JobProgress({ job, actionBusy, onCancel, onRetry, onNewProject }: JobProgressProps) {
  const current = STAGES.findIndex(([key]) => key === job.progress.current_stage);
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled";
  const highlightFailure = failed && job.progress.failed_stage === "detecting_highlights";
  const percent = Math.max(0, Math.min(100, job.progress.percent || 0));
  const headline = highlightFailure
    ? "No clean moments passed the first cut."
    : failed
      ? "This project stopped."
      : cancelled
        ? "Project paused."
        : "Finding the parts worth keeping.";
  const description = highlightFailure
    ? "The video and transcript are saved. Retry to use the broader recovery pass, or adjust the direction."
    : failed
      ? job.error || "Your saved work is safe. Retry the project or begin again."
      : cancelled
        ? "Resume from the completed work whenever you are ready."
        : job.progress.message || "Preparing your video…";

  return (
    <main className="progress-page">
      <motion.section className="progress-minimal" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <header className="progress-heading">
          <span className="page-index">Project / {failed ? "Needs attention" : cancelled ? "Paused" : "Processing"}</span>
          <span className={`progress-state ${failed || cancelled ? "warning" : "working"}`}><i />{failed ? "Stopped" : cancelled ? "Paused" : "Working"}</span>
        </header>

        <h1>{headline}</h1>
        <p className="progress-message">{description}</p>

        <div className="progress-readout"><strong>{percent}%</strong><span>{job.progress.processed_clips || 0}/{job.progress.total_clips || "—"} clips</span></div>
        <div className="progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <motion.span animate={{ width: `${Math.max(1, percent)}%` }} transition={{ type: "spring", stiffness: 80, damping: 18 }} />
        </div>

        <ol className="stage-list">
          {STAGES.map(([key, label], index) => {
            const complete = current > index || job.status === "done";
            const active = current === index && !failed && !cancelled;
            return (
              <li key={key} className={complete ? "complete" : active ? "active" : ""}>
                <span>{complete ? <Check size={12} /> : active ? <LoaderCircle size={12} className="spin" /> : index + 1}</span>
                <b>{label}</b>
              </li>
            );
          })}
        </ol>

        <div className="progress-actions">
          {job.can_cancel ? <button type="button" className="button secondary" disabled={actionBusy} onClick={onCancel}><CircleStop size={15} /> Cancel</button> : null}
          {job.can_retry ? <button type="button" className="button primary" disabled={actionBusy} onClick={onRetry}><RotateCcw size={15} /> Retry saved work</button> : null}
          {failed || cancelled ? <button type="button" className="button secondary" onClick={onNewProject}>Change settings <ArrowUpRight size={15} /></button> : null}
        </div>
      </motion.section>
    </main>
  );
}
