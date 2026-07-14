import axios from "axios";
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
    if (err.response?.status === 401 && !isAuthRecoveryInFlight()) {
      window.location.reload();
    }
    return Promise.reject(err);
  }
);

export default api;
