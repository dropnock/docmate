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

export async function getToken(): Promise<string> {
  const kc = getKeycloak();
  try {
    await kc.updateToken(30);
  } catch {
    if (!authRecoveryInFlight) {
      authRecoveryInFlight = true;
      kc.login();
    }
    throw new Error("Token refresh failed — redirecting to login");
  }
  return kc.token!;
}

export function logout() {
  localStorage.removeItem("docmate_customer_realm");
  getKeycloak().logout({ redirectUri: window.location.origin });
}
