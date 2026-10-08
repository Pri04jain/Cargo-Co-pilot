/**
 * Auth state management using Zustand.
 * Token is persisted to localStorage so it survives page refreshes.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api } from "./api";

interface AuthState {
  token: string | null;
  user: { email: string; role: string; tenant_id: string } | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,

      login: async (email, password) => {
        const res = await api.post("/api/v1/auth/login", { email, password });
        const { access_token } = res.data;

        // Decode the JWT payload to get user info (no library needed for display)
        const payload = JSON.parse(atob(access_token.split(".")[1]));

        set({
          token: access_token,
          user: {
            email: payload.email,
            role: payload.role ?? "reviewer",
            tenant_id: payload.tenant_id,
          },
        });

        // Set default auth header for all future requests
        api.defaults.headers.common["Authorization"] = `Bearer ${access_token}`;
      },

      logout: () => {
        set({ token: null, user: null });
        delete api.defaults.headers.common["Authorization"];
      },
    }),
    {
      name: "cargo-copilot-auth",
      onRehydrateStorage: () => (state) => {
        // Restore auth header after page reload
        if (state?.token) {
          api.defaults.headers.common["Authorization"] = `Bearer ${state.token}`;
        }
      },
    }
  )
);
