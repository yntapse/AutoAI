"use client";

import Link from "next/link";
import { useState } from "react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Mock: redirect to onboarding first
    window.location.href = "/onboarding";
  }

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-9">
          <div className="inline-flex items-center gap-2.5 mb-4">
            <div className="w-10 h-10 rounded-xl bg-[#3BB273] flex items-center justify-center shadow-sm shadow-[#3BB273]/30">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <span className="text-xl font-semibold tracking-tight text-slate-900">AutoAI Builder</span>
          </div>
          <h1 className="text-[30px] leading-tight font-semibold tracking-tight text-slate-900">Welcome back</h1>
          <p className="text-sm text-slate-500 mt-2">Sign in to your account to continue</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-[0_10px_30px_rgba(15,23,42,0.08)] border border-slate-200/80 p-8">
          <form onSubmit={handleSubmit} className="space-y-5.5">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-2">
                Email address
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-400 outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-shadow"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                  Password
                </label>
                <a href="#" className="text-xs text-[#3BB273] hover:text-[#2FA565] font-medium">
                  Forgot password?
                </a>
              </div>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-400 outline-none focus:ring-2 focus:ring-[#3BB273]/70 focus:border-transparent transition-shadow"
              />
            </div>

            <button
              type="submit"
              className="w-full bg-[#3BB273] hover:bg-[#2FA565] text-white font-medium text-sm py-2.5 rounded-xl shadow-sm shadow-[#3BB273]/40 transition-colors"
            >
              Sign in
            </button>
          </form>

          <p className="text-center text-sm text-slate-500 mt-6.5">
            Don&apos;t have an account?{" "}
            <Link href="/register" className="text-[#3BB273] hover:text-[#2FA565] font-medium">
              Create one
            </Link>
          </p>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6.5">
          &copy; {new Date().getFullYear()} AutoAI Builder. All rights reserved.
        </p>
      </div>
    </div>
  );
}
