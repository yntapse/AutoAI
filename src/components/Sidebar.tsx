"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navGroups = [
  {
    label: null,
    items: [
      {
        label: "Dashboard",
        href: "/dashboard",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
        ),
        color: "cyan",
        badge: undefined as string | undefined,
      },
    ],
  },
  {
    label: "WORKSPACE",
    items: [
      {
        label: "New Project",
        href: "/new-project",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M12 4v16m8-8H4" />
          </svg>
        ),
        color: "violet",
        badge: undefined as string | undefined,
      },
      {
        label: "Projects",
        href: "/dashboard",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
        ),
        color: "blue",
        badge: undefined as string | undefined,
      },
      {
        label: "Experiments",
        href: "/experiments",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
          </svg>
        ),
        color: "purple",
        badge: "Beta",
      },
      {
        label: "Models",
        href: "/models",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M14 10l-2 1m0 0l-2-1m2 1v2.5M20 7l-2 1m2-1l-2-1m2 1v2.5M14 4l-2-1-2 1M4 7l2-1M4 7l2 1M4 7v2.5M12 21l-2-1m2 1l2-1m-2 1v-2.5M6 18l-2-1v-2.5M18 18l2-1v-2.5" />
          </svg>
        ),
        color: "emerald",
        badge: undefined as string | undefined,
      },
    ],
  },
  {
    label: "SYSTEM",
    items: [
      {
        label: "Settings",
        href: "/settings",
        icon: (
          <svg className="w-[17px] h-[17px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        ),
        color: "slate",
        badge: undefined as string | undefined,
      },
    ],
  },
];

const colorSpec: Record<string, { icon: string; glow: string; activeBg: string; activeBorder: string }> = {
  cyan:    { icon: "text-cyan-400",    glow: "drop-shadow-[0_0_8px_rgba(34,211,238,0.9)]",   activeBg: "bg-cyan-500/10",    activeBorder: "border-cyan-400/35" },
  violet:  { icon: "text-violet-400",  glow: "drop-shadow-[0_0_8px_rgba(167,139,250,0.9)]", activeBg: "bg-violet-500/10",  activeBorder: "border-violet-400/35" },
  blue:    { icon: "text-blue-400",    glow: "drop-shadow-[0_0_8px_rgba(96,165,250,0.9)]",   activeBg: "bg-blue-500/10",    activeBorder: "border-blue-400/35" },
  purple:  { icon: "text-purple-400",  glow: "drop-shadow-[0_0_8px_rgba(192,132,252,0.9)]", activeBg: "bg-purple-500/10",  activeBorder: "border-purple-400/35" },
  emerald: { icon: "text-emerald-400", glow: "drop-shadow-[0_0_8px_rgba(52,211,153,0.9)]",  activeBg: "bg-emerald-500/10", activeBorder: "border-emerald-400/35" },
  slate:   { icon: "text-slate-400",   glow: "drop-shadow-[0_0_6px_rgba(148,163,184,0.7)]", activeBg: "bg-slate-500/8",    activeBorder: "border-slate-400/25" },
};

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="relative flex min-h-screen w-[215px] flex-shrink-0 flex-col overflow-hidden border-r border-white/[0.05] bg-[linear-gradient(180deg,rgba(7,11,26,0.97)_0%,rgba(5,8,20,0.98)_100%)] shadow-[1px_0_30px_rgba(0,0,0,0.6)]">
      {/* Ambient background */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-20 -left-12 h-64 w-64 rounded-full bg-indigo-700/10 blur-[80px]" />
        <div className="absolute top-1/2 right-0 h-48 w-40 rounded-full bg-cyan-600/6 blur-[60px]" />
        <div className="absolute bottom-20 left-4 h-36 w-36 rounded-full bg-violet-700/7 blur-[55px]" />
        {/* Subtle grid */}
        <div className="absolute inset-0 opacity-[0.025] bg-[linear-gradient(rgba(180,200,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(180,200,255,1)_1px,transparent_1px)] [background-size:30px_30px]" />
        {/* Starfield */}
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 215 800" preserveAspectRatio="none">
          <circle cx="18" cy="58" r="0.8" fill="white" opacity="0.45"/>
          <circle cx="90" cy="82" r="0.6" fill="white" opacity="0.35"/>
          <circle cx="52" cy="138" r="0.9" fill="white" opacity="0.5"/>
          <circle cx="182" cy="108" r="0.7" fill="white" opacity="0.4"/>
          <circle cx="146" cy="170" r="0.8" fill="white" opacity="0.45"/>
          <circle cx="30" cy="215" r="0.6" fill="white" opacity="0.3"/>
          <circle cx="105" cy="252" r="1.0" fill="white" opacity="0.4"/>
          <circle cx="198" cy="285" r="0.7" fill="white" opacity="0.38"/>
          <circle cx="65" cy="318" r="0.8" fill="white" opacity="0.42"/>
          <circle cx="158" cy="352" r="0.6" fill="white" opacity="0.32"/>
          <circle cx="20" cy="388" r="0.9" fill="white" opacity="0.48"/>
          <circle cx="132" cy="418" r="0.7" fill="white" opacity="0.35"/>
          <circle cx="75" cy="465" r="0.8" fill="white" opacity="0.4"/>
          <circle cx="205" cy="485" r="0.6" fill="white" opacity="0.3"/>
          <circle cx="44" cy="528" r="0.9" fill="white" opacity="0.45"/>
          <circle cx="170" cy="556" r="0.7" fill="white" opacity="0.38"/>
          <circle cx="95" cy="608" r="0.8" fill="white" opacity="0.42"/>
          <circle cx="192" cy="645" r="0.6" fill="white" opacity="0.32"/>
          <circle cx="36" cy="688" r="0.9" fill="white" opacity="0.44"/>
          <circle cx="152" cy="722" r="0.7" fill="white" opacity="0.36"/>
          <circle cx="10" cy="182" r="0.6" fill="white" opacity="0.28"/>
          <circle cx="172" cy="238" r="0.8" fill="white" opacity="0.38"/>
          <circle cx="118" cy="372" r="0.7" fill="white" opacity="0.34"/>
          <circle cx="202" cy="145" r="0.6" fill="white" opacity="0.3"/>
          <circle cx="56" cy="502" r="0.8" fill="white" opacity="0.4"/>
        </svg>
      </div>

      {/* Logo / Branding */}
      <div className="relative border-b border-white/[0.05] px-4 py-5">
        <Link href="/dashboard" className="group flex items-center gap-3">
          <div className="relative flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 via-blue-500 to-violet-600 shadow-[0_0_22px_rgba(99,179,255,0.5)] transition-all duration-300 group-hover:shadow-[0_0_32px_rgba(99,179,255,0.7)]">
            <svg className="h-[18px] w-[18px] text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
            </svg>
            <div className="absolute inset-0 rounded-xl bg-white/15 opacity-0 transition group-hover:opacity-100" />
          </div>
          <div>
            <div className="text-[15px] font-bold tracking-tight text-white">PyrunAI</div>
            <div className="text-[10px] font-semibold tracking-[0.13em] text-slate-500">AUTONOMOUS ML</div>
          </div>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="relative flex-1 overflow-y-auto px-2.5 py-4">
        {navGroups.map((group, gi) => (
          <div key={gi} className={gi > 0 ? "mt-5" : ""}>
            {group.label && (
              <p className="mb-2 px-2.5 text-[10.5px] font-bold tracking-[0.15em] text-slate-600 uppercase select-none">
                {group.label}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = pathname === item.href;
                const c = colorSpec[item.color] ?? colorSpec.slate;
                return (
                  <Link
                    key={item.label}
                    href={item.href}
                    className={`group flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-[13px] font-medium border transition-all duration-200 ${
                      isActive
                        ? `${c.activeBg} ${c.activeBorder} text-white shadow-sm`
                        : "border-transparent text-slate-400 hover:bg-white/[0.035] hover:text-slate-100"
                    }`}
                  >
                    <span
                      className={`flex-shrink-0 transition-all duration-200 ${
                        isActive
                          ? `${c.icon} ${c.glow}`
                          : "text-slate-500 group-hover:text-slate-300"
                      }`}
                    >
                      {item.icon}
                    </span>
                    <span className="flex-1">{item.label}</span>
                    {item.badge && (
                      <span className="rounded-full bg-violet-500/20 border border-violet-500/30 px-1.5 py-[1px] text-[10px] font-semibold text-violet-300">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Status + User */}
      <div className="relative border-t border-white/[0.05] px-3 pb-4 pt-3">
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.06] px-3 py-2">
          <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)] animate-pulse" />
          <span className="text-[11px] font-semibold text-emerald-400">System Online</span>
        </div>
        <button className="w-full flex items-center gap-2.5 rounded-xl px-2 py-2 transition-colors hover:bg-white/[0.04] text-left">
          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-cyan-400 text-[11px] font-bold text-white shadow-[0_0_12px_rgba(139,92,246,0.45)]">
            J
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-semibold text-slate-200 truncate">Jane Doe</div>
            <div className="text-[10px] text-slate-500">Pro Plan</div>
          </div>
          <svg className="h-3.5 w-3.5 text-slate-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l4-4 4 4m0 6l-4 4-4-4" />
          </svg>
        </button>
      </div>
    </aside>
  );
}
