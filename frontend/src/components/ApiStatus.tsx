"use client";

import { useEffect, useState } from "react";

type Status =
  | { state: "checking" }
  | { state: "connected"; codeSha: string }
  | { state: "unavailable" };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
// Render's free tier can take about a minute to wake.
const TIMEOUT_MS = 90_000;

/** Shows whether the browser can reach the backend API, and which version is running. */
export function ApiStatus() {
  const [status, setStatus] = useState<Status>({ state: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    // Cleanup also aborts the request, and that isn't an API failure.
    let cleanedUp = false;

    async function check() {
      try {
        const response = await fetch(`${API_URL}/health`, { signal: controller.signal });
        if (!response.ok) throw new Error(`Health check returned ${response.status}`);
        const body: { code_sha: string } = await response.json();
        if (!cleanedUp) setStatus({ state: "connected", codeSha: body.code_sha });
      } catch (error) {
        if (cleanedUp) return;
        console.error("API health check failed", error);
        setStatus({ state: "unavailable" });
      } finally {
        clearTimeout(timeout);
      }
    }

    void check();
    return () => {
      cleanedUp = true;
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return (
    <p className="status" role="status">
      {status.state === "checking" && "Connecting to the API. This can take up to a minute."}
      {status.state === "connected" && (
        <>
          API connected, version <code>{status.codeSha.slice(0, 7)}</code>
        </>
      )}
      {status.state === "unavailable" && "The API isn't available right now."}
    </p>
  );
}
