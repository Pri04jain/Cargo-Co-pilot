/**
 * Auth state management using Zustand.
 *
 * Token strategy:
 * - Login stores both the short-lived access token (60 min) and the
 *   long-lived refresh token (30 days) in localStorage.
 * - The Axios interceptor in api.ts silently exchanges the refresh token
 *   for a new pair on any 401, then retries the original request.
 * - Logout calls POST /auth/logout to revoke the refresh token server-side,
 *   then clears local state.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api } from "./api";

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: { email: string; role: string; tenant_id: string } | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Called by the Axios interceptor after a successful token refresh. */
  setTokens: (accessToken: string, refreshToken: string) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,

      login: async (email, password) => {
        const res = await api.post("/api/v1/auth/login", { email, password });
        const { access_token, refresh_token } = res.data;

        // Decode JWT payload for display (no library needed)
        const payload = JSON.parse(atob(access_token.split(".")[1]));

        api.defaults.headers.common["Authorization"] = `Bearer ${access_token}`;

        set({
          token: access_token,
          refreshToken: refresh_token,
          user: {
            email: payload.email,
            role: payload.role ?? "reviewer",
            tenant_id: payload.tenant_id,
          },
        });
      },

      logout: async () => {
        const rt = get().refreshToken;
        try {
          if (rt) {
            await api.post("/api/v1/auth/logout", { refresh_token: rt });
          }
        } catch {
          // Ignore — we're logging out regardless
        } finally {
          set({ token: null, refreshToken: null, user: null });
          delete api.defaults.headers.common["Authorization"];
        }
      },

      setTokens: (accessToken, refreshToken) => {
        api.defaults.headers.common["Authorization"] = `Bearer ${accessToken}`;
        set({ token: accessToken, refreshToken });
      },
    }),
    {
      name: "cargo-copilot-auth",
      onRehydrateStorage: () => (state) => {
        if (state?.token) {
          api.defaults.headers.common["Authorization"] = `Bearer ${state.token}`;
        }
      },
    }
  )
);
