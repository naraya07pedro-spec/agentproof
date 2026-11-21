const BODY_TIMEOUT_MS = 5000;
const UUID = "[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}";
const routes = {
  v1: {
    get: new RegExp(`^(?:suites|runs)(?:/${UUID})?$`),
    post: new RegExp(`^(?:suites|suites/${UUID}/runs|runs/${UUID}/replay)$`),
    maxRequestBytes: 128 * 1024,
    upstreamTimeoutMs: 15_000,
  },
  v2: {
    get: new RegExp(`^(?:agents|suites|runs(?:/${UUID})?)$`),
    post: /^runs$/,
    maxRequestBytes: 1024,
    upstreamTimeoutMs: 40_000,
  },
} as const;

type Context = { params: Promise<{ path: string[] }> };

function failure(status: number, detail: string, closeConnection = false) {
  const headers = new Headers({ "Cache-Control": "no-store" });
  if (closeConnection) headers.set("Connection", "close");
  return Response.json({ detail }, { status, headers });
}

async function readBody(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  deadline: AbortSignal,
  limit: number,
): Promise<string | Response> {
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let bytesRead = 0;
  let body = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (deadline.aborted) return failure(408, "Request body timed out.", true);
      if (done) return body + decoder.decode();

      bytesRead += value.byteLength;
      if (bytesRead > limit) {
        return failure(413, "Request exceeds API size limit.", true);
      }
      body += decoder.decode(value, { stream: true });
    }
  } catch {
    return failure(400, "Invalid request body.", true);
  } finally {
    reader.releaseLock();
  }
}

async function readRequest(request: Request, limit: number): Promise<string | Response> {
  if (Number(request.headers.get("content-length")) > limit) {
    return failure(413, "Request exceeds API size limit.", true);
  }
  const reader = request.body?.getReader();
  if (!reader) return "";

  const deadline = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<Response>((resolve) => {
    timer = setTimeout(() => {
      deadline.abort();
      // Closing the HTTP connection lets Next.js release its inbound stream.
      resolve(failure(408, "Request body timed out.", true));
    }, BODY_TIMEOUT_MS);
  });
  try {
    return await Promise.race([readBody(reader, deadline.signal, limit), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

export function createForwarder(version: keyof typeof routes) {
  const route = routes[version];
  return async function forward(request: Request, context: Context) {
    const path = (await context.params).path.join("/");
    const allowed = request.method === "GET" ? route.get : request.method === "POST" ? route.post : null;
    if (!allowed?.test(path)) return failure(404, "Route not found.");

    const authorization = request.headers.get("authorization");
    if (!authorization) return failure(401, "API token required.");

    let body: string | undefined;
    if (request.method === "POST") {
      const input = await readRequest(request, route.maxRequestBytes);
      if (input instanceof Response) return input;
      body = input || undefined;
    }

    try {
      const origin = process.env.AGENTPROOF_API_URL || "http://127.0.0.1:8000";
      const upstream = await fetch(new URL(`/${version}/${path}`, origin), {
        method: request.method,
        headers: { authorization, "Content-Type": "application/json" },
        body,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(route.upstreamTimeoutMs)]),
      });
      return new Response(upstream.body, {
        status: upstream.status,
        headers: { "Cache-Control": "no-store", "Content-Type": "application/json" },
      });
    } catch {
      return failure(502, "The evaluation API is unavailable.");
    }
  };
}
