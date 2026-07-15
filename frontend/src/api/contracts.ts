export type RunStatus = "queued" | "running" | "completed" | "failed";
export type ReportStatus = "pending" | "running" | "completed" | "failed";
export type PlaceResultStatus = "analyzed" | "failed";

export interface Weights {
  photoPercent: number;
  reviewPercent: number;
}

export interface ScoringCriteria {
  photo: string;
  review: string;
}

export interface RunConfig {
  location: string;
  searchKeyword: string;
  maxPlaces: number;
  weights: Weights;
  scoring: ScoringCriteria;
}

export interface RunAccepted {
  runId: string;
  status: RunStatus;
  statusUrl: string;
  reportUrl: string;
}

export interface RunSnapshot {
  runId: string;
  status: RunStatus;
  config: RunConfig;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  reportAvailable: boolean;
  error?: string | null;
}

export interface PlaceResult {
  status: PlaceResultStatus;
  placeId?: string | null;
  name: string;
  category?: string | null;
  address?: string | null;
  photoScore?: number | null;
  reviewScore?: number | null;
  finalScore?: number | null;
  photoReason?: string | null;
  reviewReason?: string | null;
  failureReason?: string | null;
}

export interface RunReport {
  runId: string;
  status: ReportStatus;
  config: RunConfig;
  results: PlaceResult[];
  errors: string[];
  createdAt: string;
}

export interface ReportSummary {
  runId: string;
  status: ReportStatus;
  config: RunConfig;
  createdAt: string;
  resultCount: number;
  errorCount: number;
  reportUrl: string;
}

export interface ReportPage {
  items: ReportSummary[];
  nextCursor?: string | null;
  invalidReportCount: number;
}
