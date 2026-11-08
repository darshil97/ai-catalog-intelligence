"use client";

import { authClient } from "@/lib/auth/client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Render's free tier can take about a minute to wake.
const TIMEOUT_MS = 90_000;

/** An error response from the API, with the machine-readable code when it sent one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Refresh this long before expiry, so a token never expires between fetching and use.
const TOKEN_REFRESH_MARGIN_SECONDS = 60;

let cachedToken: { token: string; expiresAt: number } | null = null;

/** Reads a JWT's expiry, in seconds since the epoch, or 0 if it can't be read. */
function expiresAt(token: string): number {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return Number((JSON.parse(atob(payload)) as { exp?: number }).exp) || 0;
  } catch {
    return 0;
  }
}

/**
 * Returns a short-lived Neon Auth JWT for the API, or null when signed out.
 *
 * Uses the /token endpoint rather than the session: this app's auth route answers
 * get-session from a cookie cache, which carries the opaque session token, not the JWT.
 */
async function getToken(forceRefresh = false): Promise<string | null> {
  const now = Date.now() / 1000;
  if (!forceRefresh && cachedToken && cachedToken.expiresAt - TOKEN_REFRESH_MARGIN_SECONDS > now) {
    return cachedToken.token;
  }
  cachedToken = null;
  try {
    const { data } = await authClient.token();
    const token = data?.token;
    // A JWT has three parts. Anything else would only earn a 401 from the API.
    if (!token || token.split(".").length !== 3) return null;
    cachedToken = { token, expiresAt: expiresAt(token) };
    return token;
  } catch {
    // Signed out: the token endpoint refuses without a session.
    return null;
  }
}

/** Forgets the cached token, such as after signing out. */
export function clearApiToken(): void {
  cachedToken = null;
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    // Not JSON, such as a proxy error page. The status alone still says enough.
  }
  if (detail && typeof detail === "object" && "code" in detail && "message" in detail) {
    const { code, message } = detail as { code: string; message: string };
    return new ApiError(response.status, code, message);
  }
  if (Array.isArray(detail)) {
    // FastAPI validation errors: show the first message, which names the bad value.
    const first = detail[0] as { msg?: string } | undefined;
    return new ApiError(response.status, "invalid", first?.msg ?? "Invalid request");
  }
  const fallback = response.status === 404 ? "Not found" : `Request failed (${response.status})`;
  return new ApiError(response.status, response.status === 404 ? "not_found" : "error", fallback);
}

/**
 * Calls the API as the signed-in user.
 *
 * @throws ApiError for any non-2xx response, including 401 when signed out.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const send = async (token: string | null) => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body) headers.set("Content-Type", "application/json");
    return fetch(`${API_URL}${path}`, {
      ...init,
      headers,
      signal: init.signal ?? AbortSignal.timeout(TIMEOUT_MS),
    });
  };

  let response = await send(await getToken());
  if (response.status === 401) {
    // The cached token may have expired in flight. One fresh attempt, never a loop.
    response = await send(await getToken(true));
  }
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Shortcut for JSON request bodies. */
export function jsonBody(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body) };
}
