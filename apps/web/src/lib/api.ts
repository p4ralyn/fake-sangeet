/**
 * Typed fetch wrapper. Same-origin only (Vite proxies /api and /auth to the
 * FastAPI backend — see vite.config.ts and frontend-api-contract.md
 * "Transport rules"). Sends X-CSRF-Token on every mutating request and
 * parses the `{"error": {...}}` envelope into a typed ApiError.
 */
import type { ErrorEnvelope } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null,
    details: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

/**
 * A 401 from GET /api/v1/me. Per the contract, this is the signed-out
 * signal, not an error to surface — callers should render a signed-out UI,
 * never a toast.
 */
export class SignedOutError extends Error {
  constructor() {
    super("Signed out");
    this.name = "SignedOutError";
  }
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;

/** Called by session.ts once /api/v1/me resolves. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function parseErrorEnvelope(response: Response): Promise<ErrorEnvelope | null> {
  try {
    return (await response.json()) as ErrorEnvelope;
  } catch {
    return null;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  let body: BodyInit | undefined;

  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      body = options.body;
    } else {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.body);
    }
  }

  if (MUTATING_METHODS.has(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(path, {
    ...options,
    method,
    headers,
    body,
    credentials: "same-origin",
  });

  if (response.status === 401 && path === "/api/v1/me") {
    throw new SignedOutError();
  }

  if (!response.ok) {
    const envelope = await parseErrorEnvelope(response);
    if (envelope?.error) {
      throw new ApiError(
        response.status,
        envelope.error.code,
        envelope.error.message,
        envelope.error.request_id,
        envelope.error.details,
      );
    }
    throw new ApiError(response.status, "INTERNAL", `Request failed (${response.status}).`, null, null);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PUT", body }),
  del: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "DELETE" }),
};
