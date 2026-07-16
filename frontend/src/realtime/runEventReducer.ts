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

export type ProgressStatus =
  | "started"
  | "in_progress"
  | "completed"
  | "skipped"
  | "failed";

export interface RunProgressData {
  stage: string;
  message: string;
  status?: ProgressStatus;
  placeId?: string;
  placeName?: string;
  current?: number;
  total?: number;
  inputCount?: number;
  durationMs?: number;
  score?: number;
  photoUrls?: string[];
}

export interface RunPlaceResultData {
  status: "analyzed" | "failed";
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

const optionalNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

export const getRunProgressData = (
  event: RunEvent,
): RunProgressData | null => {
  if (event.type !== "progress") return null;
  const stage = event.data.stage;
  const message = event.data.message;
  if (typeof stage !== "string" || typeof message !== "string") return null;
  const rawStatus = event.data.status;
  const status = ["started", "in_progress", "completed", "skipped", "failed"]
    .includes(String(rawStatus))
    ? rawStatus as ProgressStatus
    : undefined;
  const photoUrls = Array.isArray(event.data.photoUrls)
    ? event.data.photoUrls
      .filter(
        (value): value is string =>
          typeof value === "string" && /^https?:\/\//i.test(value),
      )
      .slice(0, 5)
    : undefined;
  return {
    stage,
    message,
    status,
    placeId: typeof event.data.placeId === "string" ? event.data.placeId : undefined,
    placeName: typeof event.data.placeName === "string" ? event.data.placeName : undefined,
    current: optionalNumber(event.data.current),
    total: optionalNumber(event.data.total),
    inputCount: optionalNumber(event.data.inputCount),
    durationMs: optionalNumber(event.data.durationMs),
    score: optionalNumber(event.data.score),
    photoUrls,
  };
};

export interface RunProjection {
  latestSequence: number;
  lastAppliedSequence: number;
  resetReplayUntil: number | null;
  progressItems: RunEvent[];
  placeResults: RunPlaceResultData[];
  awaitingSnapshot: boolean;
  status: string;
  terminal: boolean;
  reportAvailable: boolean;
}

export const createRunProjection = (): RunProjection => ({
  latestSequence: 0,
  lastAppliedSequence: 0,
  resetReplayUntil: null,
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
      resetReplayUntil: latestSequence,
      awaitingSnapshot: true,
    };
  }

  if (event.type === "snapshot" && state.awaitingSnapshot) {
    return {
      ...createRunProjection(),
      latestSequence: event.sequence,
      lastAppliedSequence: 0,
      resetReplayUntil: state.resetReplayUntil,
      awaitingSnapshot: false,
      status: String(event.data.status ?? "running"),
      reportAvailable: Boolean(event.data.reportAvailable),
      terminal: ["completed", "failed"].includes(String(event.data.status)),
    };
  }

  if (event.type === "snapshot") {
    const status = String(event.data.status ?? state.status);
    return {
      ...state,
      latestSequence: Math.max(state.latestSequence, event.sequence),
      lastAppliedSequence: Math.max(state.lastAppliedSequence, event.sequence),
      status,
      terminal: status === "completed" || status === "failed",
      reportAvailable: Boolean(event.data.reportAvailable),
    };
  }

  if (event.sequence <= state.lastAppliedSequence) {
    return state;
  }

  const next: RunProjection = {
    ...state,
    latestSequence: Math.max(state.latestSequence, event.sequence),
    lastAppliedSequence: event.sequence,
    resetReplayUntil:
      state.resetReplayUntil !== null && event.sequence >= state.resetReplayUntil
        ? null
        : state.resetReplayUntil,
  };

  if (event.type === "progress") {
    return { ...next, progressItems: [...next.progressItems, event] };
  }
  if (event.type === "place_result") {
    return {
      ...next,
      placeResults: [
        ...next.placeResults,
        event.data as unknown as RunPlaceResultData,
      ],
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
  return next;
};
