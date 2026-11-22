/**
 * App shell — sidebar navigation + main content area.
 * The sidebar shows the current user's email and role.
 */

import { Link, useLocation } from "react-router-dom";
import {
  Ship, FileText, AlertCircle, Sliders, BarChart3, LogOut, MessageSquare,
} from "lucide-react";
import { useAuthStore } from "../lib/auth";

const navItems = [
  { path: "/documents",    label: "Documents",     icon: FileText },
  { path: "/review",       label: "Review Queue",  icon: AlertCircle },
  { path: "/chat",         label: "Ask Copilot",   icon: MessageSquare },
  { path: "/rules",        label: "Rules",         icon: Sliders },
  { path: "/metrics",      label: "Metrics",       icon: BarChart3 },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { user, logout } = useAuthStore();

  return (
    <div className="min-h-screen flex">
      {/* Sidebar */}
      <aside className="w-60 bg-slate-900 text-white flex flex-col shrink-0">
        {/* Brand */}
        <div className="p-5 border-b border-white/10">
          <div className="flex items-center gap-3">
            <div className="bg-blue-500 rounded-lg p-1.5">
              <Ship className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="font-bold text-base leading-tight">Cargo Copilot</h1>
              <p className="text-xs text-blue-300 leading-tight">Logistics AI</p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-0.5">
          {navItems.map(({ path, label, icon: Icon }) => {
            const active = location.pathname.startsWith(path);
            return (
              <Link
                key={path}
                to={path}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  active
                    ? "bg-white/15 text-white font-medium"
                    : "text-slate-400 hover:bg-white/8 hover:text-white"
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* User footer */}
        <div className="p-4 border-t border-white/10">
          <p className="text-xs text-slate-400 truncate mb-0.5">{user?.email}</p>
          <p className="text-xs text-slate-500 capitalize mb-3">{user?.role}</p>
          <button
            onClick={logout}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-gray-50">
        {children}
      </main>
    </div>
  );
}
