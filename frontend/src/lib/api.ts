/**
 * Axios instance with:
 * - Base URL from env (defaults to same origin — Vite proxy handles /api)
 * - Silent token refresh on 401 using the stored refresh token
 * - Redirect to /login only when refresh itself fails
 */

import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? "",
  headers: { "Content-Type": "application/json" },
});

// Track whether a refresh is already in flight to avoid parallel refresh calls
let isRefreshing = false;

// Queue of callbacks waiting for the refresh to complete
let waitingQueue: Array<{
  resolve: (token: string) => void;
  reject: (err: unknown) => void;
}> = [];

function flushQueue(err: unknown, token: string | null) {
  waitingQueue.forEach(({ resolve, reject }) =>
    err ? reject(err) : resolve(token!)
  );
  waitingQueue = [];
}

function clearAuthAndRedirect() {
  localStorage.removeItem("cargo-copilot-auth");
  delete api.defaults.headers.common["Authorization"];
  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;

    // Only intercept 401s that haven't already been retried,
    // and skip auth endpoints (login/refresh/logout) to avoid loops.
    if (
      error.response?.status !== 401 ||
      original?._retried ||
      original?.url?.includes("/api/v1/auth/")
    ) {
      return Promise.reject(error);
    }

    // Lazily read the store to avoid circular dependency at module init time
    // (api.ts is imported by auth.ts, so we can't do a top-level import)
    const getStore = () =>
      // useAuthStore is a Zustand store — we access its state directly
      (window as any).__cargoAuthStore as {
        refreshToken: string | null;
        setTokens: (a: string, r: string) => void;
      } | undefined;

    // Pull refresh token from localStorage directly — avoids the circular dep
    // while still being reactive to state written by auth.ts
    const persisted = JSON.parse(
      localStorage.getItem("cargo-copilot-auth") ?? "{}"
    ) as { state?: { refreshToken?: string | null } };
    const refreshToken = persisted?.state?.refreshToken ?? null;

    if (!refreshToken) {
      clearAuthAndRedirect();
      return Promise.reject(error);
    }

    if (isRefreshing) {
      // Queue this request to be retried after the in-flight refresh settles
      return new Promise<string>((resolve, reject) => {
        waitingQueue.push({ resolve, reject });
      }).then((newToken) => {
        original.headers["Authorization"] = `Bearer ${newToken}`;
        return api(original);
      });
    }

    isRefreshing = true;
    original._retried = true;

    try {
      const res = await axios.post(
        `${api.defaults.baseURL ?? ""}/api/v1/auth/refresh`,
        { refresh_token: refreshToken },
        { headers: { "Content-Type": "application/json" } }
      );

      const { access_token, refresh_token: newRefresh } = res.data as {
        access_token: string;
        refresh_token: string;
      };

      // Update localStorage directly — auth.ts Zustand store will be in sync
      // on the next render cycle; immediate requests need the header updated now
      const stored = JSON.parse(
        localStorage.getItem("cargo-copilot-auth") ?? "{}"
      ) as { state?: Record<string, unknown> };
      if (stored.state) {
        stored.state.token = access_token;
        stored.state.refreshToken = newRefresh;
        localStorage.setItem("cargo-copilot-auth", JSON.stringify(stored));
      }
      api.defaults.headers.common["Authorization"] = `Bearer ${access_token}`;

      flushQueue(null, access_token);
      original.headers["Authorization"] = `Bearer ${access_token}`;
      return api(original);
    } catch (refreshErr) {
      flushQueue(refreshErr, null);
      clearAuthAndRedirect();
      return Promise.reject(refreshErr);
    } finally {
      isRefreshing = false;
    }
  }
);
