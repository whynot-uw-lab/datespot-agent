export type RunEventType =
  | "snapshot"
  | "queued"
  | "running"
  | "progress"
  | "place_result"
  | "browser_ready"
  | "browser_closed"
  | "report_saved"
  | "completed"
  | "failed"
  | "replay_reset";

export interface RunEvent {
  runId: string;
  sequence: number;
  occurredAt: string;
  type: RunEventType;
  data: Record<string, unknown>;
}

export interface RunProjection {
  latestSequence: number;
  progressItems: RunEvent[];
  placeResults: PlaceResult[];
  awaitingSnapshot: boolean;
  status: string;
  terminal: boolean;
  reportAvailable: boolean;
}

export const createRunProjection = (): RunProjection => ({
  latestSequence: 0,
  progressItems: [],
  placeResults: [],
  awaitingSnapshot: false,
  status: "queued",
  terminal: false,
  reportAvailable: false,
});

export const reduceRunEvent = (
  state: RunProjection,
  event: RunEvent,
): RunProjection => {
  if (event.type === "replay_reset") {
    const latestSequence = Number(event.data.latestSequence ?? event.sequence);
    return {
      ...state,
      latestSequence,
      awaitingSnapshot: true,
    };
  }

  if (event.type === "snapshot" && state.awaitingSnapshot) {
    return {
      ...createRunProjection(),
      latestSequence: event.sequence,
      awaitingSnapshot: false,
      status: String(event.data.status ?? "running"),
      reportAvailable: Boolean(event.data.reportAvailable),
      terminal: ["completed", "failed"].includes(String(event.data.status)),
    };
  }

  if (event.sequence <= state.latestSequence) {
    return state;
  }

  const next: RunProjection = {
    ...state,
    latestSequence: event.sequence,
  };

  if (event.type === "progress") {
    return { ...next, progressItems: [...next.progressItems, event] };
  }
  if (event.type === "place_result") {
    return {
      ...next,
      placeResults: [...next.placeResults, event.data as unknown as PlaceResult],
    };
  }
  if (["queued", "running", "completed", "failed"].includes(event.type)) {
    return {
      ...next,
      status: event.type,
      terminal: event.type === "completed" || event.type === "failed",
      reportAvailable: Boolean(event.data.reportAvailable),
    };
  }
  if (event.type === "snapshot") {
    const status = String(event.data.status ?? next.status);
    return {
      ...next,
      status,
      terminal: status === "completed" || status === "failed",
      reportAvailable: Boolean(event.data.reportAvailable),
    };
  }
  return next;
};
import type { PlaceResult } from "../api/contracts";
