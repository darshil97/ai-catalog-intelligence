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

/** Returns a short-lived Neon Auth JWT for the API, or null when signed out. */
async function getToken(): Promise<string | null> {
  const { data } = await authClient.getSession();
  return data?.session?.token ?? null;
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
    response = await send(await getToken());
  }
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Shortcut for JSON request bodies. */
export function jsonBody(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body) };
}
