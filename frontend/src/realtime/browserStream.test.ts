import { describe, expect, it, vi } from "vitest";

import { connectBrowserStream, type BrowserSocket } from "./browserStream";

class FakeSocket implements BrowserSocket {
  binaryType: BinaryType = "blob";
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  close = vi.fn();
}

describe("connectBrowserStream", () => {
  it("replaces and revokes JPEG object URLs", () => {
    const socket = new FakeSocket();
    const createObjectURL = vi
      .fn()
      .mockReturnValueOnce("blob:first")
      .mockReturnValueOnce("blob:second");
    const revokeObjectURL = vi.fn();
    const onFrame = vi.fn();

    const connection = connectBrowserStream({
      runId: "run-1",
      socketFactory: () => socket,
      createObjectURL,
      revokeObjectURL,
      onFrame,
      onState: vi.fn(),
    });
    socket.onmessage?.(
      new MessageEvent("message", { data: new Uint8Array([1]).buffer }),
    );
    socket.onmessage?.(
      new MessageEvent("message", { data: new Uint8Array([2]).buffer }),
    );

    expect(socket.binaryType).toBe("arraybuffer");
    expect(onFrame).toHaveBeenLastCalledWith("blob:second");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:first");

    connection.close();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:second");
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it("maps control messages to public states", () => {
    const socket = new FakeSocket();
    const onState = vi.fn();
    connectBrowserStream({
      runId: "run-1",
      socketFactory: () => socket,
      createObjectURL: vi.fn(),
      revokeObjectURL: vi.fn(),
      onFrame: vi.fn(),
      onState,
    });

    socket.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "waiting" }),
      }),
    );
    socket.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "ready" }),
      }),
    );

    expect(onState).toHaveBeenCalledWith("waiting");
    expect(onState).toHaveBeenCalledWith("ready");
  });

  it("revokes the last frame when the remote stream ends", () => {
    const socket = new FakeSocket();
    const revokeObjectURL = vi.fn();
    const onFrame = vi.fn();
    connectBrowserStream({
      runId: "run-1",
      socketFactory: () => socket,
      createObjectURL: () => "blob:last",
      revokeObjectURL,
      onFrame,
      onState: vi.fn(),
    });
    socket.onmessage?.(
      new MessageEvent("message", { data: new Uint8Array([1]).buffer }),
    );
    socket.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "ended" }),
      }),
    );

    expect(revokeObjectURL).toHaveBeenCalledWith("blob:last");
    expect(onFrame).toHaveBeenLastCalledWith(undefined);
  });
});
