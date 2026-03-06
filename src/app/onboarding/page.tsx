import Link from "next/link";

export default function OnboardingPage() {
  return (
    <div className="min-h-screen bg-[#0B1F3A] flex items-center justify-center px-4 py-12">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute inset-x-0 top-0 h-px bg-[#1e3a52]/70" />
      </div>

      <div className="relative z-10 w-full max-w-2xl">
        <div className="rounded-2xl border border-[#1e3a52] bg-[#0F172A]/80 p-8 md:p-10 shadow-2xl">
          <div className="text-center mb-10">
            <h1 className="text-3xl md:text-4xl font-semibold text-slate-100 mb-3">
              Welcome to AutoAI Builder
            </h1>
            <p className="text-base md:text-lg text-slate-400">
              Train production-ready ML models in minutes.
            </p>
          </div>

          <div className="mb-10 space-y-4">
            <div className="flex items-center gap-3 rounded-lg border border-[#1e3a52] bg-[#0B1F3A]/40 px-4 py-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-md border border-[#1e3a52] bg-[#0F172A] text-slate-300">
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 15.5A3.5 3.5 0 017.5 12H8a4 4 0 017.75-1.3A3.5 3.5 0 0116.5 18H8.5" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 12v8m0 0l-3-3m3 3l3-3" />
                </svg>
              </div>
              <p className="text-slate-200">Upload your dataset</p>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-4 py-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-700 bg-slate-900 text-slate-300">
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 6h16M4 12h10M4 18h7" />
                  <circle cx="18" cy="12" r="3" strokeWidth={1.8} />
                </svg>
              </div>
              <p className="text-slate-200">Select target column</p>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-[#1e3a52] bg-[#0B1F3A]/40 px-4 py-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-md border border-[#1e3a52] bg-[#0F172A] text-slate-300">
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 17l5-5 4 4 7-8" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M20 9V4h-5" />
                </svg>
              </div>
              <p className="text-slate-200">AutoAI trains &amp; compares models</p>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <Link
              href="/new-project"
              className="inline-flex items-center justify-center rounded-lg bg-[#3BB273] px-6 py-3 font-medium text-white transition-all hover:bg-[#2FA565] shadow-[0_0_20px_rgba(59,178,115,0.3)]"
            >
              Create Your First Project
            </Link>
            <Link
              href="/new-project?sample=true"
              className="inline-flex items-center justify-center rounded-lg border border-[#1e3a52] bg-[#0F172A] px-6 py-3 font-medium text-slate-200 transition-colors hover:bg-[#123C66]"
            >
              Try Sample Dataset
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
