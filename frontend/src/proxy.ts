import type { NextRequest } from "next/server";

import { getAuth } from "@/lib/auth/server";

// Signed-out visitors to app pages go to the home page, which offers sign-in. The API checks
// every request itself, so this only saves a pointless page load.
export default function proxy(request: NextRequest) {
  return getAuth().middleware({ loginUrl: "/" })(request);
}

export const config = {
  matcher: ["/suppliers/:path*", "/batches/:path*"],
};
