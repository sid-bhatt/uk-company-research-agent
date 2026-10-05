import type { Metadata } from "next";
import Link from "next/link";

import { auth, signOut } from "@/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "KYB Due-Diligence",
  description: "Cited KYB risk reports on UK companies from Companies House data.",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const session = await auth();
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold">KYB Due-Diligence <span className="font-normal text-zinc-500">· UK</span></Link>
            {session?.user && (
              <form
                action={async () => {
                  "use server";
                  await signOut({ redirectTo: "/signin" });
                }}
                className="flex items-center gap-3 text-sm"
              >
                <span className="text-zinc-500">{session.user.name ?? session.user.id}</span>
                <button className="underline">Sign out</button>
              </form>
            )}
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
        <footer className="mx-auto max-w-5xl px-4 pb-8 text-xs text-zinc-500">
          Demo of automated screening over public Companies House data. Not financial, legal or compliance advice.
        </footer>
      </body>
    </html>
  );
}
