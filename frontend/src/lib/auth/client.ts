"use client";

import { createAuthClient } from "@neon/auth/next";

/** Browser-side Neon Auth, talking to this app's /api/auth route. */
export const authClient = createAuthClient();
