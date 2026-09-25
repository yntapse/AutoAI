"use client";

import Navbar from "@/components/Navbar";
import { useState } from "react";

export default function SettingsPage() {
  const [name, setName] = useState("Jane Doe");
  const [email, setEmail] = useState("jane@example.com");
  const [notifications, setNotifications] = useState(true);
  const [saved, setSaved] = useState(false);

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  }

  return (
    <>
      <Navbar title="Settings" />

      <main className="flex-1 px-8 py-8 overflow-auto">
        <div className="max-w-2xl mx-auto space-y-6.5">
          <div>
            <h2 className="text-[23px] font-semibold tracking-tight text-slate-100">Settings</h2>
            <p className="text-sm text-slate-400 mt-1.5">Manage your account preferences</p>
          </div>

          {/* Profile Settings */}
          <div className="bg-slate-900/70 backdrop-blur-sm rounded-2xl border border-slate-800/85 shadow-[0_0_30px_rgba(15,23,42,0.6)] p-6">
            <h3 className="text-[15px] font-semibold tracking-tight text-slate-100 mb-5">Profile</h3>
            <form onSubmit={handleSave} className="space-y-5">
              <div className="flex items-center gap-4 mb-6">
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#3BB273]/30 to-[#5EDC8A]/30 border border-[#3BB273]/25 flex items-center justify-center text-[#5EDC8A] font-bold text-xl shadow-[0_0_16px_rgba(59,178,115,0.25)]">
                  J
                </div>
                <div>
                  <p className="text-sm font-medium tracking-tight text-slate-200">Profile photo</p>
                  <button type="button" className="text-xs text-[#5EDC8A] hover:text-[#3BB273] hover:underline mt-0.5 transition-colors">
                    Change photo
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">Full name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-3.5 py-2.5 rounded-xl border border-slate-700 bg-slate-950/70 text-sm text-slate-100 outline-none focus:ring-2 focus:ring-cyan-400/60 focus:border-cyan-400/35 transition-all duration-200"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full px-3.5 py-2.5 rounded-xl border border-[#1e3a52] bg-[#0B1F3A]/70 text-sm text-slate-100 outline-none focus:ring-2 focus:ring-[#3BB273]/60 focus:border-[#3BB273]/35 transition-all duration-200"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">Company</label>
                <input
                  type="text"
                  placeholder="Acme Inc."
                  className="w-full px-3.5 py-2.5 rounded-xl border border-[#1e3a52] bg-[#0B1F3A]/70 text-sm text-slate-100 placeholder-slate-500 outline-none focus:ring-2 focus:ring-[#3BB273]/60 focus:border-[#3BB273]/35 transition-all duration-200"
                />
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="submit"
                  className="text-white font-medium text-sm px-5 py-2.5 rounded-xl bg-[#3BB273] hover:bg-[#2FA565] shadow-[0_0_24px_rgba(59,178,115,0.3)] hover:shadow-[0_0_30px_rgba(59,178,115,0.4)] transition-all duration-200"
                >
                  Save changes
                </button>
                {saved && (
                  <span className="text-sm text-[#5EDC8A] font-medium flex items-center gap-1.5">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                    Saved!
                  </span>
                )}
              </div>
            </form>
          </div>

          {/* Notifications */}
          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_30px_rgba(11,31,58,0.6)] p-6">
            <h3 className="text-[15px] font-semibold tracking-tight text-slate-100 mb-5">Notifications</h3>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-200 font-medium tracking-tight">Training complete alerts</p>
                <p className="text-xs text-slate-500 mt-0.5">Get notified via email when a training job finishes</p>
              </div>
              <button
                onClick={() => setNotifications(!notifications)}
                className={`relative w-11 h-6 rounded-full transition-colors ${notifications ? "bg-gradient-to-r from-[#3BB273] to-[#5EDC8A] shadow-[0_0_14px_rgba(59,178,115,0.35)]" : "bg-[#1e3a52]"}`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-slate-100 shadow transition-transform ${notifications ? "translate-x-5" : ""}`}
                />
              </button>
            </div>
          </div>

          {/* Danger Zone */}
          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-rose-400/25 shadow-[0_0_30px_rgba(11,31,58,0.6)] p-6">
            <h3 className="text-[15px] font-semibold tracking-tight text-rose-300 mb-4">Danger Zone</h3>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-200 font-medium tracking-tight">Delete account</p>
                <p className="text-xs text-slate-500 mt-0.5">Permanently delete your account and all data</p>
              </div>
              <button className="bg-rose-500/12 hover:bg-rose-500/18 border border-rose-400/30 text-rose-300 font-medium text-sm px-4 py-2 rounded-xl transition-all duration-200">
                Delete account
              </button>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
