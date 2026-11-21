export type Fixture = "corrected" | "vulnerable";

export type Suite = {
  id: string;
  name: string;
  created_at: string;
};

export type Run = {
  id: string;
  suite_id: string;
  suite_name: string;
  fixture: Fixture;
  created_at: string;
  replay_of: string | null;
  evidence: string;
  engine_version: string;
  spec_version: string;
  passed: boolean;
  case_count: number;
  failed_count: number;
  critical_failed: number;
};

type AssertionValue = string | number | boolean | null;

type Assertion = {
  check: string;
  passed: boolean;
  expected: AssertionValue;
  actual: AssertionValue;
};

type TraceEvent = {
  seq: number;
  kind: string;
  elapsed_ms: number;
  tool: string | null;
  attempt: number | null;
};

export type CaseResult = {
  case_id: string;
  critical: boolean;
  passed: boolean;
  outcome: string;
  assertions: Assertion[];
  trace: TraceEvent[];
};

export type RunDetail = Run & { results: CaseResult[] };

type ApiOptions = {
  method?: "POST";
  body?: string;
  version?: "v1" | "v2";
  signal?: AbortSignal;
};

export async function api<T>(token: string, path: string, options?: ApiOptions): Promise<T> {
  const response = await fetch(`/api/${options?.version ?? "v1"}${path}`, {
    method: options?.method ?? "GET",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: options?.body,
    cache: "no-store",
    signal: options?.signal,
  });

  if (!response.ok) {
    const messages: Record<number, string> = {
      400: "The API rejected the request.",
      401: "The API token was rejected.",
      403: "The API token cannot perform this action.",
      404: "The requested item was not found.",
      408: "The evaluation request took too long to upload.",
      413: "The request exceeds the API size limit.",
      422: "The request does not match the evaluation specification.",
      429: "The API is busy. Try again shortly.",
      502: "The evaluation API is unavailable.",
      503: "The evaluation API is temporarily unavailable.",
    };
    throw new Error(messages[response.status] ?? `Request failed (HTTP ${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export function displayName(value: string) {
  return value.replaceAll("_", " ");
}

export function formattedTime(isoDate: string) {
  return new Date(isoDate).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
