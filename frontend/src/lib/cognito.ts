/**
 * Real sign-in with Cognito's managed login: authorization code with PKCE, in the browser, no
 * client secret. Configured at build time; with no domain set, the demo people are used instead.
 * The ID token Cognito returns is exchanged by the API for Lane's own session (POST /auth/cognito).
 */

const DOMAIN = (process.env.NEXT_PUBLIC_LANE_COGNITO_DOMAIN ?? "").replace(/\/$/, "");
const CLIENT_ID = process.env.NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID ?? "";
const PENDING = "lane.signin"; // the verifier, state and where to return, for one sign-in

type Pending = { verifier: string; state: string; returnTo: string };

export function cognitoEnabled(): boolean {
  return Boolean(DOMAIN && CLIENT_ID);
}

function callbackUrl(): string {
  return `${window.location.origin}/auth/callback`;
}

function randomString(bytes = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Go to Cognito's sign-in page; it comes back to /auth/callback. */
export async function startSignIn(): Promise<void> {
  const pending: Pending = {
    verifier: randomString(48),
    state: randomString(16),
    returnTo: window.location.pathname + window.location.search,
  };
  sessionStorage.setItem(PENDING, JSON.stringify(pending));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: callbackUrl(),
    scope: "openid email profile",
    state: pending.state,
    code_challenge: await challenge(pending.verifier),
    code_challenge_method: "S256",
  });
  const url = new URL("/oauth2/authorize", DOMAIN); // Cognito's own page, not one of ours
  url.search = params.toString();
  window.location.assign(url.href);
}

/** Back from Cognito: trade the code for the ID token. Returns it and where to go next. */
export async function finishSignIn(
  code: string,
  state: string,
): Promise<{ idToken: string; returnTo: string }> {
  const raw = sessionStorage.getItem(PENDING);
  sessionStorage.removeItem(PENDING);
  const pending = raw ? (JSON.parse(raw) as Pending) : null;
  if (!pending || pending.state !== state) {
    throw new Error("This sign-in was not started here. Sign in again.");
  }
  const res = await fetch(`${DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      code,
      redirect_uri: callbackUrl(),
      code_verifier: pending.verifier,
    }),
  });
  if (!res.ok) throw new Error("Cognito did not accept the sign-in. Sign in again.");
  const body = (await res.json()) as { id_token?: string };
  if (!body.id_token) throw new Error("Cognito sent no ID token. Sign in again.");
  return { idToken: body.id_token, returnTo: pending.returnTo || "/" };
}

/** Where to send the browser to end the Cognito session too. */
export function signOutUrl(): string {
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    logout_uri: `${window.location.origin}/`,
  });
  return `${DOMAIN}/logout?${params}`;
}
