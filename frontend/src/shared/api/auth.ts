import api, { PORTAL } from "./client";
import type { AuthUser } from "../types";

export async function login(email: string, password: string): Promise<AuthUser> {
  const { data } = await api.post<AuthUser>("/auth/login", { email, password, portal: PORTAL });
  localStorage.setItem("docmate_token", data.access_token);
  return data;
}

export function logout() {
  localStorage.removeItem("docmate_token");
  window.location.href = "/login";
}

export function getStoredUser(): AuthUser | null {
  const token = localStorage.getItem("docmate_token");
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return {
      user_id: parseInt(payload.sub),
      role: payload.role,
      portal: payload.portal,
      full_name: "",
      access_token: token,
    };
  } catch {
    return null;
  }
}
