import type { CreateJobSettings, JobResponse } from "./types";

const browserUrl = new URL(window.location.href);
const requestedPort = browserUrl.searchParams.get("api_port");
const apiPort = /^\d{2,5}$/.test(requestedPort ?? "") ? requestedPort : "8000";
const apiHost = ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ? window.location.hostname
  : "localhost";

export const API_BASE = `http://${apiHost}:${apiPort}`;

export async function errorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null) as { detail?: string | Array<{ msg?: string }> } | null;
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item.msg).filter(Boolean).join(" · ") || fallback;
  }
  return fallback;
}

export async function request<T>(path: string, init?: RequestInit, fallback = "Request failed"): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) throw new Error(await errorMessage(response, fallback));
  return response.json() as Promise<T>;
}

export function mediaUrl(path?: string | null): string {
  if (!path) return "";
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/`, { cache: "no-store", signal: AbortSignal.timeout(4500) });
    return response.ok;
  } catch {
    return false;
  }
}

export function createUrlJob(url: string, settings: CreateJobSettings): Promise<{ job_id: string }> {
  return request("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, ...settings }),
  }, "Could not start this video.");
}

export function createUploadJob(file: File, settings: CreateJobSettings): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  Object.entries(settings).forEach(([key, value]) => {
    if (value !== null) form.append(key, String(value));
  });
  return request("/api/jobs/upload", { method: "POST", body: form }, "Could not upload this video.");
}

export function getJob(jobId: string): Promise<JobResponse> {
  return request(`/api/jobs/${jobId}`, { cache: "no-store" }, "Could not load this job.");
}

export function cancelJob(jobId: string): Promise<unknown> {
  return request(`/api/jobs/${jobId}/cancel`, { method: "POST" }, "Could not cancel this job.");
}

export function retryJob(jobId: string): Promise<unknown> {
  return request(`/api/jobs/${jobId}/retry`, { method: "POST" }, "Could not retry this job.");
}
