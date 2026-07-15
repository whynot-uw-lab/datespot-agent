export class AppError extends Error {
  constructor(
    message: string,
    readonly status = 0,
    readonly code = "unknown_error",
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "AppError";
  }
}

export interface ReportFilters {
  status?: string;
  location?: string;
  searchKeyword?: string;
  dateFrom?: string;
  dateTo?: string;
  cursor?: string;
}

interface ErrorEnvelope {
  detail?: unknown;
}

const validationErrors = (detail: unknown): Record<string, string> => {
  if (!Array.isArray(detail)) return {};
  const fields: Record<string, string> = {};
  for (const issue of detail) {
    if (typeof issue !== "object" || issue === null) continue;
    const { loc, msg } = issue as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const path = loc
      .filter((part) => part !== "body")
      .map(String)
      .join(".");
    if (path) fields[path] = msg;
  }
  return fields;
};

export const requestJson = async <T>(
  url: string,
  init?: RequestInit,
): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new AppError("서버에 연결할 수 없음", 0, "network_error");
  }

  if (response.ok) {
    return (await response.json()) as T;
  }

  let envelope: ErrorEnvelope = {};
  try {
    envelope = (await response.json()) as ErrorEnvelope;
  } catch {
    // Non-JSON failures use the public fallback below.
  }
  const fields = validationErrors(envelope.detail);
  if (response.status === 422 && Object.keys(fields).length) {
    throw new AppError(
      "입력값을 확인해 주세요",
      response.status,
      "validation_error",
      fields,
    );
  }
  const publicDetail =
    typeof envelope.detail === "object" && envelope.detail !== null
      ? (envelope.detail as { code?: string; message?: string })
      : undefined;
  throw new AppError(
    publicDetail?.message ?? "요청을 처리할 수 없음",
    response.status,
    publicDetail?.code ?? "request_failed",
  );
};

export const buildReportQuery = (filters: ReportFilters): string => {
  const params = new URLSearchParams({ limit: "20" });
  const ordered: Array<[keyof ReportFilters, string]> = [
    ["status", "status"],
    ["location", "location"],
    ["searchKeyword", "searchKeyword"],
    ["dateFrom", "dateFrom"],
    ["dateTo", "dateTo"],
    ["cursor", "cursor"],
  ];
  for (const [field, parameter] of ordered) {
    const value = filters[field]?.trim();
    if (value) params.set(parameter, value);
  }
  return `?${params.toString()}`;
};
