import type { AuthUser } from "../types";
export { logout } from "./keycloak";

// Stub retained for backward compat with LoginPage (unused but must not break tsc)
export async function login(_email: string, _password: string): Promise<AuthUser> {
  throw new Error("Direct login disabled — authentication handled by Keycloak");
}

export function getStoredUser(): AuthUser | null {
  return null;
}
