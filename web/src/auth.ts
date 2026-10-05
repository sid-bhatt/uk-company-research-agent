/**
 * Authentication (Auth.js v5).
 *
 * - GitHub sign-in when AUTH_GITHUB_ID / AUTH_GITHUB_SECRET are set.
 * - AUTH_ALLOWED_USERS (comma-separated GitHub logins) restricts who may sign in: a public
 *   demo must not become a free LLM endpoint.
 * - Local development without GitHub credentials gets a "dev login" (any name, no password),
 *   which never exists in a production build.
 *
 * The session's `user.id` ("github:<login>" or "dev:<name>") is the identity sent to the API
 * and recorded on every review.
 */
import NextAuth, { type NextAuthConfig } from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";

const githubConfigured = Boolean(process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET);
export const devLoginEnabled =
  process.env.NODE_ENV === "development" && process.env.AUTH_DEV_LOGIN !== "false";

const allowed = (process.env.AUTH_ALLOWED_USERS ?? "")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

const providers: NextAuthConfig["providers"] = [];
if (githubConfigured) providers.push(GitHub);
if (devLoginEnabled) {
  providers.push(
    Credentials({
      id: "dev",
      name: "Dev login",
      credentials: { name: { label: "Your name", type: "text" } },
      authorize: (credentials) => {
        const name = String(credentials?.name ?? "").trim().slice(0, 60);
        return name ? { id: `dev:${name.toLowerCase()}`, name } : null;
      },
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers,
  session: { strategy: "jwt", maxAge: 8 * 60 * 60 }, // a working day
  pages: { signIn: "/signin" },
  callbacks: {
    signIn({ account, profile }) {
      if (account?.provider !== "github" || allowed.length === 0) return true;
      const login = String(profile?.login ?? "").toLowerCase();
      return allowed.includes(login);
    },
    jwt({ token, account, profile, user }) {
      if (account?.provider === "github" && profile?.login) token.uid = `github:${profile.login}`;
      else if (account?.provider === "dev" && user?.id) token.uid = user.id;
      return token;
    },
    session({ session, token }) {
      if (typeof token.uid === "string") session.user.id = token.uid;
      return session;
    },
  },
});
