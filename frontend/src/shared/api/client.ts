import axios from "axios";
import { getToken } from "./keycloak";

export const PORTAL = (import.meta.env.VITE_PORTAL ?? "digitizing") as "digitizing" | "customer";

const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use(async (config) => {
  try {
    const token = await getToken();
    config.headers.Authorization = `Bearer ${token}`;
  } catch {
    // Keycloak not yet initialised — request proceeds without auth token
  }
  config.headers["X-Portal"] = PORTAL;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      window.location.reload();
    }
    return Promise.reject(err);
  }
);

export default api;
