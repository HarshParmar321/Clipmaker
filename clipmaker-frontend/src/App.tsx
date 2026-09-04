import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { cancelJob, checkHealth, createUploadJob, createUrlJob, getJob, retryJob } from "./api";
import { CreateJobPanel } from "./components/CreateJobPanel";
import { Header } from "./components/Header";
import { JobProgress } from "./components/JobProgress";
import { Studio } from "./components/Studio";
import type { CreateJobSettings, JobResponse } from "./types";

function jobFromUrl(): string {
  return new URL(window.location.href).searchParams.get("job") || "";
}

function setJobInUrl(jobId: string) {
  const url = new URL(window.location.href);
  if (jobId) url.searchParams.set("job", jobId);
  else url.searchParams.delete("job");
  window.history.replaceState({}, "", url);
}

export default function App() {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [jobId, setJobId] = useState(jobFromUrl);
  const [job, setJob] = useState<JobResponse | null>(null);
  const [creating, setCreating] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    const update = () => void checkHealth().then((value) => { if (mounted) setHealthy(value); });
    update();
    const interval = window.setInterval(update, 8_000);
    return () => { mounted = false; window.clearInterval(interval); };
  }, []);

  const refreshJob = useCallback(async () => {
    if (!jobId) return;
    try {
      const next = await getJob(jobId);
      setJob(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load this project.");
    }
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        setJob(next);
        setError("");
        if (!["done", "failed", "cancelled"].includes(next.status)) {
          timer = window.setTimeout(poll, 2_000);
        }
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Could not load this project.");
        timer = window.setTimeout(poll, 4_000);
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId]);

  const create = async (source: { url?: string; file?: File }, settings: CreateJobSettings) => {
    setCreating(true);
    setError("");
    try {
      if (!(await checkHealth())) throw new Error("The backend is offline. Start it on port 8000 and try again.");
      const result = source.file
        ? await createUploadJob(source.file, settings)
        : await createUrlJob(source.url || "", settings);
      setJobId(result.job_id);
      setJobInUrl(result.job_id);
      setJob(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start this project.");
    } finally {
      setCreating(false);
    }
  };

  const runAction = async (action: "cancel" | "retry") => {
    if (!jobId) return;
    setActionBusy(true);
    setError("");
    try {
      if (action === "cancel") await cancelJob(jobId);
      else await retryJob(jobId);
      await refreshJob();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Could not ${action} this project.`);
    } finally {
      setActionBusy(false);
    }
  };

  const newProject = () => {
    setJobId("");
    setJob(null);
    setError("");
    setJobInUrl("");
  };

  const workspace = job?.status === "done" && Boolean(job.clips.length);
  return (
    <div className="app-shell">
      <Header healthy={healthy} workspace={workspace} onNewProject={newProject} />
      <AnimatePresence mode="wait">
        {workspace && job ? (
          <motion.div key="workspace" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Studio clips={job.clips} />
          </motion.div>
        ) : jobId && job ? (
          <motion.div key="progress" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <JobProgress job={job} actionBusy={actionBusy} onCancel={() => void runAction("cancel")} onRetry={() => void runAction("retry")} onNewProject={newProject} />
          </motion.div>
        ) : (
          <motion.div key="create" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <CreateJobPanel busy={creating} onCreate={create} />
          </motion.div>
        )}
      </AnimatePresence>

      {error ? (
        <motion.div className="global-error" role="alert" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <AlertTriangle size={18} />
          <span>{error}</span>
          {jobId ? <button type="button" onClick={() => void refreshJob()}><RotateCcw size={15} /> Retry</button> : null}
        </motion.div>
      ) : null}
    </div>
  );
}
