import { buildReportQuery, requestJson, type ReportFilters } from "./client";
import type {
  ReportPage,
  RunAccepted,
  RunConfig,
  RunReport,
  RunSnapshot,
} from "./contracts";

export const createRun = (config: RunConfig): Promise<RunAccepted> =>
  requestJson("/runs", { method: "POST", body: JSON.stringify(config) });

export const getRun = (runId: string): Promise<RunSnapshot> =>
  requestJson(`/runs/${encodeURIComponent(runId)}`);

export const getRunReport = (runId: string): Promise<RunReport> =>
  requestJson(`/runs/${encodeURIComponent(runId)}/report`);

export const getReports = (filters: ReportFilters): Promise<ReportPage> =>
  requestJson(`/reports${buildReportQuery(filters)}`);

export const getPersistedReport = (runId: string): Promise<RunReport> =>
  requestJson(`/reports/${encodeURIComponent(runId)}`);
