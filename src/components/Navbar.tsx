"use client";

import { useState } from "react";
import Link from "next/link";

interface NavbarProps {
  title?: string;
}

export default function Navbar({ title = "Dashboard" }: NavbarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 flex h-[58px] flex-shrink-0 items-center justify-between border-b border-white/[0.05] bg-[rgba(5,9,20,0.88)] px-6 backdrop-blur-xl shadow-[0_1px_0_rgba(255,255,255,0.03)]">
      {/* Left: breadcrumb / title */}
      <div className="flex items-center gap-2.5">
        <svg className="h-[15px] w-[15px] text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
        </svg>
        <svg className="h-3 w-3 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <h1 className="text-[14px] font-semibold tracking-tight text-slate-200">{title}</h1>
      </div>

      {/* Right: actions */}
      <div className="flex items-center gap-2.5">
        {/* New Project CTA */}
        <Link
          href="/new-project"
          className="group relative flex items-center gap-1.5 overflow-hidden rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-3.5 py-1.5 text-[13px] font-semibold text-white shadow-[0_0_18px_rgba(6,182,212,0.35)] transition-all duration-300 hover:shadow-[0_0_26px_rgba(6,182,212,0.55)] hover:from-cyan-500 hover:to-blue-500"
        >
          <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
          <svg className="relative h-3.5 w-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
          </svg>
          <span className="relative">New Project</span>
        </Link>

        {/* Notifications */}
        <button className="relative flex h-8 w-8 items-center justify-center rounded-xl border border-white/[0.06] bg-white/[0.04] text-slate-400 transition-all hover:bg-white/[0.08] hover:text-slate-200">
          <svg className="h-[16px] w-[16px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)]" />
        </button>

        {/* Profile dropdown */}
        <div className="relative">
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.04] py-1 pl-1.5 pr-2.5 transition-all hover:bg-white/[0.08]"
          >
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-cyan-400 text-[10px] font-bold text-white shadow-[0_0_10px_rgba(139,92,246,0.4)] flex-shrink-0">
              J
            </div>
            <span className="text-[13px] font-medium text-slate-300">Jane Doe</span>
            <svg className={`h-3 w-3 text-slate-500 transition-transform duration-200 ${dropdownOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {dropdownOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setDropdownOpen(false)} />
              <div className="absolute right-0 z-20 mt-2 w-48 overflow-hidden rounded-xl border border-white/[0.08] bg-[rgba(9,14,30,0.97)] shadow-[0_12px_40px_rgba(0,0,0,0.5)] backdrop-blur-xl">
                <div className="border-b border-white/[0.06] px-4 py-3">
                  <p className="text-[13px] font-semibold text-slate-200">Jane Doe</p>
                  <p className="text-[11px] text-slate-500">jane@example.com</p>
                </div>
                <div className="p-1.5">
                  <Link
                    href="/settings"
                    className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-slate-300 hover:bg-white/[0.06] transition-colors"
                    onClick={() => setDropdownOpen(false)}
                  >
                    <svg className="h-4 w-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                    Settings
                  </Link>
                  <Link
                    href="/login"
                    className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-rose-400 hover:bg-rose-500/[0.08] transition-colors"
                    onClick={() => setDropdownOpen(false)}
                  >
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                    </svg>
                    Sign out
                  </Link>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

