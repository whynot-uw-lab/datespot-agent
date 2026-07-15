import { describe, expect, it } from "vitest";

import {
  createRunProjection,
  reduceRunEvent,
  type RunEvent,
} from "./runEventReducer";

const event = (
  sequence: number,
  type: RunEvent["type"],
  data: Record<string, unknown>,
): RunEvent => ({
  runId: "run-1",
  sequence,
  occurredAt: "2026-07-15T00:00:00Z",
  type,
  data,
});

describe("reduceRunEvent", () => {
  it("ignores duplicate canonical events", () => {
    const progress = event(1, "progress", {
      stage: "candidate_search",
      message: "후보 검색 중",
    });

    const once = reduceRunEvent(createRunProjection(), progress);
    const twice = reduceRunEvent(once, progress);

    expect(twice.progressItems).toHaveLength(1);
    expect(twice.latestSequence).toBe(1);
  });

  it("replaces local progress when replay reset is followed by snapshot", () => {
    const progressed = reduceRunEvent(
      createRunProjection(),
      event(4, "progress", {
        stage: "place_detail",
        message: "이전 진행",
      }),
    );
    const reset = reduceRunEvent(
      progressed,
      event(8, "replay_reset", { latestSequence: 8 }),
    );
    const snapshot = reduceRunEvent(
      reset,
      event(8, "snapshot", {
        runId: "run-1",
        status: "running",
        reportAvailable: false,
      }),
    );

    expect(reset.awaitingSnapshot).toBe(true);
    expect(snapshot.awaitingSnapshot).toBe(false);
    expect(snapshot.progressItems).toEqual([]);
    expect(snapshot.status).toBe("running");
    expect(snapshot.latestSequence).toBe(8);
  });

  it("collects place results and marks terminal status", () => {
    const withPlace = reduceRunEvent(
      createRunProjection(),
      event(2, "place_result", {
        status: "analyzed",
        name: "호우주의보",
        finalScore: 8.7,
      }),
    );
    const completed = reduceRunEvent(
      withPlace,
      event(3, "completed", {
        status: "completed",
        reportAvailable: true,
      }),
    );

    expect(completed.placeResults).toEqual([
      expect.objectContaining({ name: "호우주의보", finalScore: 8.7 }),
    ]);
    expect(completed.status).toBe("completed");
    expect(completed.terminal).toBe(true);
    expect(completed.reportAvailable).toBe(true);
  });
});
