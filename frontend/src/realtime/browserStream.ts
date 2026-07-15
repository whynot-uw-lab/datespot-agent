export type BrowserStreamState = "waiting" | "ready" | "ended" | "error";

export interface BrowserSocket {
  binaryType: BinaryType;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onopen: ((event: Event) => void) | null;
  close(): void;
}

interface BrowserStreamOptions {
  runId: string;
  socketFactory?: (url: string) => BrowserSocket;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
  onFrame: (url: string) => void;
  onState: (state: BrowserStreamState) => void;
}

export interface BrowserStreamConnection {
  close(): void;
}

export const connectBrowserStream = ({
  runId,
  socketFactory = (url) => new WebSocket(url),
  createObjectURL = (blob) => URL.createObjectURL(blob),
  revokeObjectURL = (url) => URL.revokeObjectURL(url),
  onFrame,
  onState,
}: BrowserStreamOptions): BrowserStreamConnection => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${window.location.host}/runs/${encodeURIComponent(runId)}/browser-stream`;
  const socket = socketFactory(url);
  let currentFrame: string | undefined;
  let closedByClient = false;
  socket.binaryType = "arraybuffer";
  socket.onopen = () => onState("waiting");
  socket.onerror = () => onState("error");
  socket.onclose = (event) => {
    if (closedByClient) return;
    onState(event.code === 1000 || event.code === 4409 ? "ended" : "error");
  };
  socket.onmessage = (message) => {
    if (typeof message.data === "string") {
      const control = JSON.parse(message.data) as { type?: string };
      if (["waiting", "ready", "ended", "error"].includes(control.type ?? "")) {
        onState(control.type as BrowserStreamState);
      }
      return;
    }
    const blob =
      message.data instanceof Blob
        ? message.data
        : new Blob([message.data as ArrayBuffer], { type: "image/jpeg" });
    const nextFrame = createObjectURL(blob);
    if (currentFrame) revokeObjectURL(currentFrame);
    currentFrame = nextFrame;
    onFrame(nextFrame);
    onState("ready");
  };
  return {
    close: () => {
      closedByClient = true;
      if (currentFrame) {
        revokeObjectURL(currentFrame);
        currentFrame = undefined;
      }
      socket.close();
    },
  };
};
