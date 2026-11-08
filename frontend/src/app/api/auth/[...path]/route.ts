import type { NextRequest } from "next/server";

import { getAuth } from "@/lib/auth/server";

// Proxies sign-in, OAuth callbacks and session requests to Neon Auth.
type Handlers = ReturnType<ReturnType<typeof getAuth>["handler"]>;
type Context = Parameters<Handlers["GET"]>[1];

export const GET = (request: NextRequest, context: Context) =>
  getAuth().handler().GET(request, context);
export const POST = (request: NextRequest, context: Context) =>
  getAuth().handler().POST(request, context);
export const PUT = (request: NextRequest, context: Context) =>
  getAuth().handler().PUT(request, context);
export const PATCH = (request: NextRequest, context: Context) =>
  getAuth().handler().PATCH(request, context);
export const DELETE = (request: NextRequest, context: Context) =>
  getAuth().handler().DELETE(request, context);
