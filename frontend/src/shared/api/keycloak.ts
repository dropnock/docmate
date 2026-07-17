import Keycloak from "keycloak-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const KEYCLOAK_URL: string = ((import.meta as any).env?.VITE_KEYCLOAK_URL as string | undefined) ?? "http://localhost:8180";

let _kc: Keycloak | null = null;

// Once a refresh failure triggers kc.login() (a redirect), every other
// in-flight request would otherwise independently retrigger a redirect/
// reload of its own — the resulting storm of Location/History API calls
// trips the browser's rate limiter (SecurityError: "The operation is
// insecure."), and the page never finishes loading. This flag lets one
// recovery redirect win and everything else back off.
let authRecoveryInFlight = false;

export function initKeycloak(realm: string, clientId: string): Promise<boolean> {
  _kc = new Keycloak({ url: KEYCLOAK_URL, realm, clientId });
  return _kc.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
}

export function getKeycloak(): Keycloak {
  if (!_kc) throw new Error("Keycloak not initialised");
  return _kc;
}

export function isAuthRecoveryInFlight(): boolean {
  return authRecoveryInFlight;
}

function handleRefreshFailure(): void {
  if (!authRecoveryInFlight) {
    authRecoveryInFlight = true;
    getKeycloak().login();
  }
}

export async function getToken(): Promise<string> {
  const kc = getKeycloak();
  try {
    await kc.updateToken(30);
  } catch {
    handleRefreshFailure();
    throw new Error("Token refresh failed — redirecting to login");
  }
  return kc.token!;
}

// Keycloak's SSO session only counts as "active" when a token refresh grant
// actually reaches it — the backend validates the JWT locally (see
// core/security.py) and never talks to Keycloak per request, so ordinary API
// traffic alone doesn't touch the SSO session at all. With accessTokenLifespan
// at 1h (doc-realm.json), updateToken() (called reactively from client.ts's
// request interceptor) doesn't actually hit Keycloak until the token is near
// that 1h mark — by which point the realm's ~30min SSO idle timeout has
// already elapsed server-side, so the "refresh" fails and the user is bounced
// even though they were actively working the whole time. This loop forces a
// real refresh grant on a short interval — comfortably inside the idle
// timeout — for as long as there's been genuine recent activity, so the SSO
// session's last-refreshed time keeps getting bumped during real work.
//
// Gating on real activity (not just "tab is open") is deliberate: a browser
// tab left open with no one at the keyboard should still be allowed to idle
// out for real, same as today — this only fixes the false-positive case.
const KEEP_ALIVE_INTERVAL_MS = 2 * 60 * 1000; // 2 min — comfortably under the SSO idle timeout
const ACTIVITY_WINDOW_MS = 10 * 60 * 1000; // 10 min — generous enough to cover reading/reviewing pauses
const ACTIVITY_EVENTS = ["mousedown", "keydown", "touchstart", "wheel", "scroll"] as const;

let lastActivityAt = Date.now();
let keepAliveStarted = false;

function recordActivity(): void {
  lastActivityAt = Date.now();
}

export function startSessionKeepAlive(): void {
  if (keepAliveStarted) return;
  keepAliveStarted = true;

  for (const event of ACTIVITY_EVENTS) {
    window.addEventListener(event, recordActivity, { passive: true, capture: true });
  }

  setInterval(() => {
    if (Date.now() - lastActivityAt > ACTIVITY_WINDOW_MS) return;
    // minValidity: -1 forces an unconditional refresh grant regardless of
    // the current token's remaining validity — the point here is touching
    // Keycloak's SSO session, not renewing a token that's actually about to
    // expire (that case is already handled reactively by getToken()).
    getKeycloak()
      .updateToken(-1)
      .catch(handleRefreshFailure);
  }, KEEP_ALIVE_INTERVAL_MS);
}

export function logout() {
  localStorage.removeItem("docmate_customer_realm");
  getKeycloak().logout({ redirectUri: window.location.origin });
}
