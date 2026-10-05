/**
 * Optimistic auth check (Next.js 16 "proxy", formerly middleware): send signed-out visitors
 * to the sign-in page. This is a convenience, not the security boundary: every page and
 * route handler verifies the session again, and the API verifies its own signed token.
 */
import { NextResponse } from "next/server";

import { auth } from "@/auth";

export const proxy = auth((req) => {
  if (!req.auth) {
    const url = new URL("/signin", req.nextUrl);
    url.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }
});

export const config = {
  // Everything except sign-in, Auth.js routes and static assets.
  matcher: ["/((?!signin|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
