import axios from "axios";

export const PORTAL = (window.location.hostname.includes("customer") ? "customer" : "digitizing") as
  | "digitizing"
  | "customer";

const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("docmate_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  config.headers["X-Portal"] = PORTAL;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("docmate_token");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export default api;
