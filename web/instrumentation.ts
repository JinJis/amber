// SC-0 / AUTH-1: refuse to boot in production on well-known dev secrets — the web-side counterpart
// of each python service's assert_production_secrets(). AUTH_SECRET signs the Auth.js session JWT
// (a dev value → forgeable sessions, CR-10); SERVICE_TOKEN is the BFF↔studio trust token (a dev
// value → anyone can impersonate the BFF). Next.js calls register() once at server startup.
export async function register() {
  // register() also runs in the Edge runtime (middleware); the secret check + hard-exit only make
  // sense in the Node server process. NODE_ENV!=production stays on the dev fallbacks silently.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.NODE_ENV !== "production") return;

  const leaked: string[] = [];
  const authSecret = process.env.AUTH_SECRET;
  if (!authSecret || authSecret === "dev-secret-change-me") leaked.push("AUTH_SECRET");
  const serviceToken = process.env.SERVICE_TOKEN;
  if (!serviceToken || serviceToken === "dev-service-token") leaked.push("SERVICE_TOKEN");

  if (leaked.length) {
    // Fail LOUDLY and hard — a security guard must not be swallowed by the bootstrap. process.exit
    // guarantees the container never serves traffic on dev secrets (it just restart-loops, which is
    // the visible signal that the deploy is misconfigured).
    console.error(
      `[FATAL] production requires real secrets for: ${leaked.join(", ")} (dev defaults refused)`,
    );
    process.exit(1);
  }
}
