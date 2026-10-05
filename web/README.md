# Web app (Next.js 16)

Analyst UI for the KYB API: start a report, watch it build, read the cited result.

```bash
cp .env.example .env.local     # set AUTH_SECRET (openssl rand -base64 32)
npm install
npm run dev                    # http://localhost:3000 (needs the API: `make api`)
```

## How it fits together
- **Backend-for-frontend.** The browser only talks to Next.js. Server components and route
  handlers verify the session, then call the API with a 5-minute HS256 token naming the user
  (`API_JWT_SECRET`, shared with the API). The API never trusts a browser directly, and every
  review is recorded under a verified identity.
- **Auth.js v5** (pinned beta): GitHub sign-in, optional `AUTH_ALLOWED_USERS` allow-list (a public
  demo shouldn't be a free LLM endpoint), and a dev-only login that doesn't exist in production builds.
- `src/proxy.ts` (Next 16's renamed middleware) only redirects signed-out visitors; pages and
  handlers re-check the session, as Next's docs advise.
- **Live progress** over server-sent events, proxied through `/api/reports/[id]/events`.
- **Typed API:** `src/lib/api/schema.d.ts` is generated from the backend's OpenAPI schema
  (`make api-types`). CI fails if either side drifts.

```bash
npm run lint && npm run typecheck && npm test && npm run build
```
