"use client";

import Link from "next/link";
import { useState } from "react";

export default function RegisterPage() {
  const [form, setForm] = useState({ name: "", email: "", password: "" });

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setForm({ ...form, [e.target.name]: e.target.value });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    window.location.href = "/dashboard";
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
          <h1 className="text-[30px] leading-tight font-semibold tracking-tight text-slate-900">Create your account</h1>
          <p className="text-sm text-slate-500 mt-2">Start training ML models in minutes</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-[0_10px_30px_rgba(15,23,42,0.08)] border border-slate-200/80 p-8">
          <form onSubmit={handleSubmit} className="space-y-5.5">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-slate-700 mb-2">
                Full name
              </label>
              <input
                id="name"
                name="name"
                type="text"
                required
                value={form.name}
                onChange={handleChange}
                placeholder="Jane Doe"
                className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-400 outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-shadow"
              />
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-2">
                Email address
              </label>
              <input
                id="email"
                name="email"
                type="email"
                required
                value={form.email}
                onChange={handleChange}
                placeholder="you@example.com"
                className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-400 outline-none focus:ring-2 focus:ring-[#3BB273]/70 focus:border-transparent transition-shadow"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-2">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                required
                value={form.password}
                onChange={handleChange}
                placeholder="Min. 8 characters"
                className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder-slate-400 outline-none focus:ring-2 focus:ring-[#3BB273]/70 focus:border-transparent transition-shadow"
              />
            </div>

            <button
              type="submit"
              className="w-full bg-[#3BB273] hover:bg-[#2FA565] text-white font-medium text-sm py-2.5 rounded-xl shadow-sm shadow-[#3BB273]/40 transition-colors"
            >
              Create account
            </button>
          </form>

          <p className="text-center text-sm text-slate-500 mt-6.5">
            Already have an account?{" "}
            <Link href="/login" className="text-[#3BB273] hover:text-[#2FA565] font-medium">
              Sign in
            </Link>
          </p>
        </div>

        <p className="text-xs text-slate-400 text-center mt-6.5 leading-relaxed">
          By creating an account, you agree to our{" "}
          <a href="#" className="underline hover:text-slate-600">Terms of Service</a>
          {" "}and{" "}
          <a href="#" className="underline hover:text-slate-600">Privacy Policy</a>.
        </p>
      </div>
    </div>
  );
}
