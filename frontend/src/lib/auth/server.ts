import { createNeonAuth } from "@neon/auth/next/server";

type NeonAuth = ReturnType<typeof createNeonAuth>;

let instance: NeonAuth | null = null;

/**
 * Server-side Neon Auth, created on first use. Sign-in requests go through this app's
 * /api/auth route, so session cookies are first-party and work in browsers that block
 * third-party cookies.
 *
 * Created lazily because the SDK validates its settings immediately, and the build (on CI
 * and Vercel) imports route modules without needing the secrets.
 *
 * @throws Error if NEON_AUTH_BASE_URL or NEON_AUTH_COOKIE_SECRET is missing or too short.
 */
export function getAuth(): NeonAuth {
  instance ??= createNeonAuth({
    // Both are server-only, and never reach the browser.
    baseUrl: process.env.NEON_AUTH_BASE_URL ?? "",
    cookies: {
      secret: process.env.NEON_AUTH_COOKIE_SECRET ?? "",
      // Google's sign-in returns with a cross-site navigation, which "strict" cookies miss.
      sameSite: "lax",
    },
  });
  return instance;
}
