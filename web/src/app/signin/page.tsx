import { redirect } from "next/navigation";

import { auth, devLoginEnabled, signIn } from "@/auth";

export default async function SignIn(props: PageProps<"/signin">) {
  const params = await props.searchParams;
  const callbackUrl = typeof params.callbackUrl === "string" && params.callbackUrl.startsWith("/") ? params.callbackUrl : "/";
  if ((await auth())?.user) redirect(callbackUrl);
  const github = Boolean(process.env.AUTH_GITHUB_ID);

  return (
    <div className="mx-auto max-w-sm space-y-6 py-12">
      <div>
        <h1 className="text-2xl font-bold">Sign in</h1>
        <p className="mt-1 text-sm text-zinc-500">Reports and review decisions are recorded under your identity.</p>
      </div>
      {params.error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {params.error === "AccessDenied" ? "This account isn't on the allow-list for this demo." : "Sign-in failed."}
        </p>
      )}
      {github && (
        <form action={async () => { "use server"; await signIn("github", { redirectTo: callbackUrl }); }}>
          <button className="w-full rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900">
            Continue with GitHub
          </button>
        </form>
      )}
      {devLoginEnabled && (
        <form
          action={async (form: FormData) => { "use server"; await signIn("dev", { name: form.get("name"), redirectTo: callbackUrl }); }}
          className="space-y-2 rounded-md border border-dashed border-zinc-300 p-4 dark:border-zinc-700"
        >
          <label htmlFor="name" className="block text-sm font-medium">Dev login (local only)</label>
          <input id="name" name="name" required placeholder="Your name" className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          <button className="w-full rounded-md border border-zinc-300 px-4 py-2 text-sm dark:border-zinc-700">Sign in</button>
        </form>
      )}
      {!github && !devLoginEnabled && <p className="text-sm">No sign-in method is configured.</p>}
    </div>
  );
}
