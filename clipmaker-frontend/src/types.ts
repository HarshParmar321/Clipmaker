export type OutputFormat = "landscape" | "vertical" | "original";
export type CaptionStyle = "bold" | "clean" | "karaoke";
export type CaptionPosition = "top" | "center" | "bottom" | "gaming";
export type CaptionSize = "small" | "medium" | "large";
export type ContentMode = "general" | "gaming";
export type LayoutMode = "smart" | "gameplay_reaction";
export type ReframeMode = "auto" | "manual";
export type FacecamCorner = "auto" | "top_left" | "top_right" | "bottom_left" | "bottom_right";

export interface CaptionWord {
  word: string;
  start: number;
  end: number;
  break_after?: boolean;
  join_next?: boolean;
}

export interface Clip {
  id: string;
  title: string;
  start: number;
  end: number;
  score: number;
  score_breakdown?: Record<string, number>;
  reason?: string;
  output_format: OutputFormat;
  caption_style: CaptionStyle;
  layout_mode?: LayoutMode;
  caption_text?: string;
  caption_words?: CaptionWord[];
  preview_url: string;
  download_url: string;
  thumbnails_url: string;
  editor_preview_url: string;
  reframe_analysis_url: string;
  gaming_analysis_url?: string;
  render_url: string;
}

export interface JobProgress {
  percent: number;
  message: string;
  current_stage?: string;
  processed_clips?: number;
  total_clips?: number;
  created_at?: string;
  failed_stage?: string;
  retryable?: boolean;
}

export interface JobResponse {
  job_id: string;
  status: "queued" | "downloading" | "transcribing" | "detecting_highlights" | "cutting_clips" | "burning_captions" | "done" | "failed" | "cancelled";
  error?: string;
  progress: JobProgress;
  can_cancel: boolean;
  can_retry: boolean;
  clips: Clip[];
}

export interface FacecamRegion { x: number; y: number; width: number; height: number }
export interface GamingAnalysis {
  region: FacecamRegion;
  confidence: number;
  detection_ratio: number;
  detector: string;
  corner: FacecamCorner;
  used_fallback: boolean;
  message: string;
}

export interface SmartPoint { time: number; x: number; y: number; confidence: number; scene_cut?: boolean }
export interface SmartAnalysis {
  points: SmartPoint[];
  zoom: number;
  detection_ratio: number;
  scene_cuts: number;
  subject_switches: number;
  quality: string;
  message: string;
}

export interface CreateJobSettings {
  creative_prompt: string | null;
  output_format: OutputFormat;
  caption_style: CaptionStyle;
  content_mode: ContentMode;
  target_clip_count: number;
  min_clip_duration: number;
  max_clip_duration: number;
}
