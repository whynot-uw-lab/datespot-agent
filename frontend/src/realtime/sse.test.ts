import { describe, expect, it, vi } from "vitest";

import { lastEventKey, streamRunEvents } from "./sse";

describe("streamRunEvents", () => {
  it("sends the resume cursor and persists parsed canonical IDs", async () => {
    const payload = [
      "id: 2",
      "event: progress",
      'data: {"runId":"run-1","sequence":2,"occurredAt":"2026-07-15T00:00:00Z","type":"progress","data":{"stage":"candidate_search","message":"검색 중"}}',
      "",
      "id: 3",
      "event: completed",
      'data: {"runId":"run-1","sequence":3,"occurredAt":"2026-07-15T00:00:01Z","type":"completed","data":{"status":"completed","reportAvailable":true}}',
      "",
    ].join("\n");
    const fetcher = vi.fn().mockResolvedValue(
      new Response(payload, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    const onEvent = vi.fn();

    const result = await streamRunEvents({
      runId: "run-1",
      lastEventId: 1,
      fetcher,
      onEvent,
    });

    expect(fetcher).toHaveBeenCalledWith(
      "/runs/run-1/events",
      expect.objectContaining({
        headers: expect.objectContaining({ "Last-Event-ID": "1" }),
      }),
    );
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "completed", sequence: 3 }),
    );
    expect(sessionStorage.getItem(lastEventKey("run-1"))).toBe("3");
    expect(result).toEqual({ terminal: true, lastEventId: 3 });
  });

  it("throws a public error for a failed HTTP handshake", async () => {
    await expect(
      streamRunEvents({
        runId: "missing",
        fetcher: vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("실시간 진행 연결에 실패함");
  });

  it("treats a terminal snapshot as a terminal stream", async () => {
    const snapshot = [
      "event: snapshot",
      'data: {"runId":"run-1","sequence":4,"occurredAt":"2026-07-15T00:00:00Z","type":"snapshot","data":{"status":"completed","reportAvailable":true}}',
      "",
      "",
    ].join("\n");

    await expect(
      streamRunEvents({
        runId: "run-1",
        fetcher: vi.fn().mockResolvedValue(
          new Response(snapshot, {
            headers: { "Content-Type": "text/event-stream" },
          }),
        ),
        onEvent: vi.fn(),
      }),
    ).resolves.toMatchObject({ terminal: true });
  });
});
