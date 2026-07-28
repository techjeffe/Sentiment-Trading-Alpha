"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { Activity, TrendingUp, FileText, Heart, Settings, BarChart3, Menu, X, Info, Search, List } from "lucide-react";

type NavItem = {
  href: string;
  label: string;
  icon: React.ReactNode;
  badge?: string;
};

const navItems: NavItem[] = [
  { href: "/", label: "Dashboard", icon: <Activity size={16} /> },
  { href: "/trading", label: "Trading", icon: <TrendingUp size={16} /> },
  { href: "/alpha", label: "Alpha Analytics", icon: <BarChart3 size={16} /> },
  { href: "/news", label: "News & Filings", icon: <FileText size={16} /> },
  { href: "/trade-list", label: "Trade List", icon: <List size={16} /> },
  { href: "/health", label: "System Health", icon: <Heart size={16} /> },
  { href: "/admin", label: "Admin", icon: <Settings size={16} /> },
  { href: "/about", label: "About", icon: <Info size={16} /> },
];

export function GlobalNav() {
  const pathname = usePathname();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [status, setStatus] = useState<"idle" | "analyzing" | "ready">("idle");

  // Close mobile menu on route change
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [pathname]);

  // Fetch system status for the indicator
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/config", { cache: "no-store" });
        if (res.ok) {
          const config = await res.json();
          setStatus(config.last_analysis_id ? "ready" : "idle");
        }
      } catch {
        // silent
      }
    };
    fetchStatus();
  }, []);

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname?.startsWith(href) ?? false;
  };

  return (
    <nav className="border-b border-slate-800 bg-slate-900/95 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6">
        <div className="flex items-center justify-between h-14">
          {/* Logo/Title */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-emerald-500 flex items-center justify-center">
              <Activity size={18} className="text-white" />
            </div>
            <div className="hidden sm:block">
              <h1 className="text-sm font-bold text-white leading-none">
                Sentiment Trading Alpha
              </h1>
              <p className="text-[10px] text-slate-500 leading-none mt-0.5">
                Geopolitical Sentiment Pipeline
              </p>
            </div>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-1">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`
                  flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all
                  ${isActive(item.href)
                    ? "bg-slate-800 text-white shadow-sm"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                  }
                `}
              >
                {item.icon}
                <span>{item.label}</span>
                {item.badge && (
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-bold bg-blue-500/20 text-blue-400 rounded">
                    {item.badge}
                  </span>
                )}
              </Link>
            ))}
          </div>

          {/* Status Indicator + Mobile Menu Toggle */}
          <div className="flex items-center gap-3">
            {/* Status Badge */}
            <div className="hidden sm:flex items-center gap-1.5">
              <div className={`
                w-2 h-2 rounded-full
                ${status === "analyzing" ? "bg-yellow-400 animate-pulse" : ""}
                ${status === "ready" ? "bg-emerald-400" : ""}
                ${status === "idle" ? "bg-slate-500" : ""}
              `} />
              <span className={`
                text-[10px] font-medium
                ${status === "analyzing" ? "text-yellow-400" : ""}
                ${status === "ready" ? "text-emerald-400" : ""}
                ${status === "idle" ? "text-slate-500" : ""}
              `}>
                {status === "analyzing" ? "Analyzing" : status === "ready" ? "Ready" : "Idle"}
              </span>
            </div>

            {/* Mobile Menu Toggle */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
              aria-label="Toggle menu"
            >
              {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Menu */}
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-slate-800 py-2 pb-4">
            <div className="space-y-1">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`
                    flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-all
                    ${isActive(item.href)
                      ? "bg-slate-800 text-white"
                      : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                    }
                  `}
                >
                  {item.icon}
                  <span>{item.label}</span>
                  {item.badge && (
                    <span className="ml-auto px-1.5 py-0.5 text-[10px] font-bold bg-blue-500/20 text-blue-400 rounded">
                      {item.badge}
                    </span>
                  )}
                </Link>
              ))}
            </div>

            {/* Mobile Status */}
            <div className="flex items-center gap-2 mt-4 px-4 py-2 bg-slate-800/50 rounded-lg">
              <div className={`
                w-2 h-2 rounded-full
                ${status === "analyzing" ? "bg-yellow-400 animate-pulse" : ""}
                ${status === "ready" ? "bg-emerald-400" : ""}
                ${status === "idle" ? "bg-slate-500" : ""}
              `} />
              <span className="text-xs text-slate-400">
                Status: <span className="font-medium">{status === "analyzing" ? "Analyzing" : status === "ready" ? "Ready" : "Idle"}</span>
              </span>
            </div>
          </div>
        )}
      </div>
    </nav>
  );
}
