import { useEffect, useReducer, useState } from "react";

import { connectBrowserStream, type BrowserStreamState } from "./browserStream";
import {
  createRunProjection,
  reduceRunEvent,
  type RunProjection,
} from "./runEventReducer";
import { lastEventKey, streamRunEvents } from "./sse";

type EventConnectionState = "connecting" | "connected" | "reconnecting" | "ended" | "error";

const wait = (milliseconds: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
  });

export const useRunEvents = (runId: string) => {
  const [projection, dispatch] = useReducer(reduceRunEvent, undefined, createRunProjection);
  const [connectionState, setConnectionState] = useState<EventConnectionState>("connecting");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const consume = async () => {
      let reconnecting = false;
      while (active && !controller.signal.aborted) {
        setConnectionState(reconnecting ? "reconnecting" : "connecting");
        const stored = sessionStorage.getItem(lastEventKey(runId));
        const cursor = stored && /^\d+$/.test(stored) ? Number(stored) : undefined;
        try {
          const result = await streamRunEvents({
            runId,
            lastEventId: cursor,
            signal: controller.signal,
            onEvent: (event) => {
              setConnectionState("connected");
              dispatch(event);
            },
          });
          if (result.terminal) {
            setConnectionState("ended");
            return;
          }
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof SyntaxError) {
            setConnectionState("error");
            return;
          }
        }
        reconnecting = true;
        await wait(2_000, controller.signal);
      }
    };
    void consume();
    return () => {
      active = false;
      controller.abort();
    };
  }, [runId]);

  return { projection: projection as RunProjection, connectionState };
};

export const useBrowserStream = (runId: string, enabled = true) => {
  const [state, setState] = useState<BrowserStreamState>("waiting");
  const [frameUrl, setFrameUrl] = useState<string>();

  useEffect(() => {
    if (!enabled) {
      setState("ended");
      setFrameUrl(undefined);
      return;
    }
    const connection = connectBrowserStream({
      runId,
      onState: setState,
      onFrame: setFrameUrl,
    });
    return () => connection.close();
  }, [enabled, runId]);

  return { state, frameUrl };
};
