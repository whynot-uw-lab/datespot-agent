import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./sse", () => ({
  lastEventKey: (runId: string) => `last:${runId}`,
  streamRunEvents: vi.fn(),
}));

import { useRunEvents } from "./hooks";
import { streamRunEvents } from "./sse";

describe("useRunEvents", () => {
  it("does not connect when the HTTP snapshot is already terminal", async () => {
    const { result } = renderHook(() => useRunEvents("run-1", false));

    await waitFor(() => expect(result.current.connectionState).toBe("ended"));
    expect(streamRunEvents).not.toHaveBeenCalled();
  });
});
