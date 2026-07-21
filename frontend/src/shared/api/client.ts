import axios from "axios";
import { message } from "antd";
import { getToken, isAuthRecoveryInFlight } from "./keycloak";

export const PORTAL = (import.meta.env.VITE_PORTAL ?? "digitizing") as "digitizing" | "customer";

const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use(async (config) => {
  try {
    const token = await getToken();
    config.headers.Authorization = `Bearer ${token}`;
  } catch (err) {
    if (isAuthRecoveryInFlight()) {
      // A login redirect is already in progress — abort instead of letting
      // this request go out unauthenticated, which would just 401 and (with
      // every other concurrent request doing the same) pile on more
      // reload()/login() calls on top of the one already underway.
      return Promise.reject(err);
    }
    // Keycloak not yet initialised — request proceeds without auth token
  }
  config.headers["X-Portal"] = PORTAL;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // Backend echoes the same X-Request-ID it logged the failure under (see
    // request_context_middleware in main.py) — surfacing it here means a
    // user can hand this to support and it's directly searchable server-side,
    // instead of "it broke" with no way to find the matching backend log line.
    console.error(
      `API ${err.config?.method?.toUpperCase()} ${err.config?.url} failed:`,
      err.response?.status,
      "request-id:", err.response?.headers?.["x-request-id"]
    );
    if (err.response?.status === 401 && !isAuthRecoveryInFlight()) {
      // Session genuinely expired (startSessionKeepAlive covers the false-
      // positive "still actively working" case) — a bare reload with no
      // explanation reads as the app randomly breaking mid-task, so say why
      // before it happens.
      message.warning("Your session expired. Reloading...", 1.5);
      setTimeout(() => window.location.reload(), 1500);
    }
    return Promise.reject(err);
  }
);

export default api;
