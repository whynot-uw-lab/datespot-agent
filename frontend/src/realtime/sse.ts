import { createParser } from "eventsource-parser";

import type { RunEvent } from "./runEventReducer";

export const lastEventKey = (runId: string): string =>
  `datespot:last-event-id:${runId}`;

interface StreamRunEventsOptions {
  runId: string;
  lastEventId?: number;
  fetcher?: typeof fetch;
  onEvent: (event: RunEvent) => void;
  signal?: AbortSignal;
}

export interface StreamResult {
  terminal: boolean;
  lastEventId?: number;
}

export const streamRunEvents = async ({
  runId,
  lastEventId,
  fetcher = fetch,
  onEvent,
  signal,
}: StreamRunEventsOptions): Promise<StreamResult> => {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (lastEventId !== undefined) {
    headers["Last-Event-ID"] = String(lastEventId);
  }
  const response = await fetcher(`/runs/${encodeURIComponent(runId)}/events`, {
    headers,
    signal,
  });
  if (!response.ok) throw new Error("실시간 진행 연결에 실패함");
  if (!response.body) throw new Error("실시간 진행 연결에 실패함");

  let cursor = lastEventId;
  let terminal = false;
  const parser = createParser({
    maxBufferSize: 1_048_576,
    onEvent: (message) => {
      const parsed = JSON.parse(message.data) as RunEvent;
      onEvent(parsed);
      if (message.id !== undefined && /^\d+$/.test(message.id)) {
        cursor = Number(message.id);
        sessionStorage.setItem(lastEventKey(runId), message.id);
      }
      const snapshotStatus = String(parsed.data.status ?? "");
      terminal =
        terminal ||
        parsed.type === "completed" ||
        parsed.type === "failed" ||
        (parsed.type === "snapshot" &&
          (snapshotStatus === "completed" || snapshotStatus === "failed"));
    },
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.feed(decoder.decode(value, { stream: true }));
  }
  parser.feed(`${decoder.decode()}\n`);
  return { terminal, lastEventId: cursor };
};
