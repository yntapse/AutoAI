import Link from "next/link";

export default function OnboardingPage() {
  return (
    <div className="onboarding-page relative min-h-screen overflow-hidden bg-[#061634] px-4 py-10 md:px-6 md:py-14">
      <div className="pointer-events-none absolute inset-0">
        <div className="aurora-pan absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(52,170,255,0.16),transparent_36%),radial-gradient(circle_at_82%_22%,rgba(70,230,190,0.16),transparent_32%),radial-gradient(circle_at_50%_80%,rgba(22,94,201,0.2),transparent_38%),linear-gradient(180deg,#061634_0%,#041026_100%)]" />

        <svg className="wireframe-drift absolute inset-0 h-full w-full opacity-45" viewBox="0 0 1440 900" fill="none" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice">
          <path d="M100 620L420 500L640 640L310 770L100 620Z" stroke="rgba(110,175,255,0.24)" />
          <path d="M420 500L830 430L1040 560L640 640L420 500Z" stroke="rgba(110,175,255,0.22)" />
          <path d="M310 770L640 640L1040 560L700 790L310 770Z" stroke="rgba(110,175,255,0.18)" />
          <path d="M880 200L1140 130L1320 250L1060 320L880 200Z" stroke="rgba(120,205,255,0.22)" />
          <path d="M880 200L880 520" stroke="rgba(120,205,255,0.12)" />
          <path d="M1060 320L1060 630" stroke="rgba(120,205,255,0.12)" />
          <path d="M1140 130L1140 430" stroke="rgba(120,205,255,0.12)" />
          <path d="M1320 250L1320 560" stroke="rgba(120,205,255,0.12)" />
          <path d="M880 520L1060 630L1320 560L1140 430L880 520Z" stroke="rgba(120,205,255,0.22)" />
        </svg>

        <div className="pulse-blob absolute left-[58%] top-[58%] h-44 w-44 rounded-full bg-cyan-400/10 blur-3xl" />
        <div className="twinkle-dot absolute left-[64%] top-[56%] h-1.5 w-1.5 rounded-full bg-cyan-200" />
        <div className="twinkle-dot delay-1 absolute left-[67%] top-[60%] h-1.5 w-1.5 rounded-full bg-emerald-200" />
        <div className="twinkle-dot delay-2 absolute left-[70%] top-[63%] h-1.5 w-1.5 rounded-full bg-sky-200" />

        <svg className="absolute left-[61%] top-[57%] h-44 w-72 opacity-70" viewBox="0 0 290 120" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M8 62C48 28 95 96 142 58C196 15 235 75 282 42" stroke="url(#spark)" strokeWidth="2" strokeLinecap="round" strokeDasharray="1 8" />
          <defs>
            <linearGradient id="spark" x1="8" y1="62" x2="282" y2="42" gradientUnits="userSpaceOnUse">
              <stop stopColor="rgba(65,220,255,0)" />
              <stop offset="0.35" stopColor="rgba(102,245,216,0.95)" />
              <stop offset="1" stopColor="rgba(65,220,255,0)" />
            </linearGradient>
          </defs>
        </svg>
      </div>

      <div className="relative z-10 mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-5xl items-center justify-center">
        <div className="panel-pop w-full max-w-2xl rounded-3xl border border-cyan-200/20 bg-[linear-gradient(135deg,rgba(72,120,184,0.28),rgba(28,56,102,0.34))] p-6 shadow-[0_20px_80px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.12)] backdrop-blur-xl md:p-8">
          <div className="mb-8 text-center md:mb-9">
            <h1 className="hero-glow mb-2 bg-[linear-gradient(90deg,#5ba9ff_0%,#68f3d2_55%,#9cf9e2_100%)] bg-clip-text text-4xl font-semibold tracking-tight text-transparent md:text-[48px] md:leading-[1.1]">
              Welcome to AutoAI Builder
            </h1>
            <p className="subtitle-rise text-base text-cyan-100/80 md:text-[26px] md:leading-none">
              Train production-ready ML models in minutes.
            </p>
          </div>

          <div className="mb-8 grid gap-3 sm:grid-cols-3 sm:gap-4 md:mb-10">
            <div className="step-card step-1 rounded-2xl border border-cyan-200/30 bg-[linear-gradient(160deg,rgba(160,220,255,0.2),rgba(20,30,58,0.55)_45%,rgba(12,20,42,0.85)_100%)] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.16),0_10px_30px_rgba(0,0,0,0.35)]">
              <p className="mb-3 text-sm text-cyan-100/80">Step 1</p>
              <div className="icon-stage mb-5 flex h-20 items-center justify-center rounded-xl border border-cyan-300/20 bg-slate-900/40">
                <svg className="h-12 w-12" fill="none" viewBox="0 0 64 64">
                  <path d="M20 39v5a4 4 0 004 4h16a4 4 0 004-4v-5" stroke="#75F0D2" strokeWidth="2.4" strokeLinecap="round" />
                  <path d="M32 16v24m0 0l-8-8m8 8l8-8" stroke="#63C7FF" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="19" cy="21" r="2" fill="#7BD8FF" />
                  <circle cx="44" cy="17" r="2" fill="#8BFFD8" />
                </svg>
              </div>
              <p className="text-xl font-medium text-slate-100">Upload</p>
            </div>

            <div className="step-card step-2 relative rounded-2xl border border-cyan-200/30 bg-[linear-gradient(160deg,rgba(160,220,255,0.2),rgba(20,30,58,0.55)_45%,rgba(12,20,42,0.85)_100%)] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.16),0_10px_30px_rgba(0,0,0,0.35)] sm:before:absolute sm:before:-left-4 sm:before:top-1/2 sm:before:h-px sm:before:w-4 sm:before:-translate-y-1/2 sm:before:bg-gradient-to-r sm:before:from-cyan-200/20 sm:before:to-cyan-100/70 sm:after:absolute sm:after:-right-4 sm:after:top-1/2 sm:after:h-px sm:after:w-4 sm:after:-translate-y-1/2 sm:after:bg-gradient-to-r sm:after:from-cyan-100/70 sm:after:to-cyan-200/20">
              <p className="mb-3 text-sm text-cyan-100/80">Step 2</p>
              <div className="icon-stage mb-5 flex h-20 items-center justify-center rounded-xl border border-cyan-300/20 bg-slate-900/40">
                <svg className="h-12 w-12" fill="none" viewBox="0 0 64 64">
                  <rect x="11" y="15" width="14" height="34" rx="3" fill="url(#barA)" />
                  <rect x="27" y="20" width="12" height="29" rx="3" fill="url(#barB)" />
                  <rect x="41" y="11" width="12" height="38" rx="3" fill="url(#barC)" />
                  <defs>
                    <linearGradient id="barA" x1="11" y1="15" x2="25" y2="49" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#7ED3FF" />
                      <stop offset="1" stopColor="#4566A8" />
                    </linearGradient>
                    <linearGradient id="barB" x1="27" y1="20" x2="39" y2="49" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#9AE2FF" />
                      <stop offset="1" stopColor="#4B6CB0" />
                    </linearGradient>
                    <linearGradient id="barC" x1="41" y1="11" x2="53" y2="49" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#94FFDA" />
                      <stop offset="1" stopColor="#4E7AA8" />
                    </linearGradient>
                  </defs>
                </svg>
              </div>
              <p className="text-xl font-medium text-slate-100">Select column</p>
            </div>

            <div className="step-card step-3 rounded-2xl border border-cyan-200/30 bg-[linear-gradient(160deg,rgba(160,220,255,0.2),rgba(20,30,58,0.55)_45%,rgba(12,20,42,0.85)_100%)] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.16),0_10px_30px_rgba(0,0,0,0.35)]">
              <p className="mb-3 text-sm text-cyan-100/80">Step 3</p>
              <div className="icon-stage mb-5 flex h-20 items-center justify-center rounded-xl border border-cyan-300/20 bg-slate-900/40">
                <svg className="h-12 w-12" fill="none" viewBox="0 0 64 64">
                  <path d="M10 45c7-9 12-11 19-16s11-3 15-8 8-9 10-11" stroke="#74D1FF" strokeWidth="2.4" strokeLinecap="round" />
                  <path d="M10 47h44" stroke="#4A6FA6" strokeWidth="2" strokeLinecap="round" />
                  <path d="M12 32c5-2 9-3 14-2" stroke="#8AFFF0" strokeWidth="2" strokeLinecap="round" />
                  <path d="M33 27c4-3 8-3 12-1" stroke="#66C7FF" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </div>
              <p className="text-xl font-medium text-slate-100">Trains &amp; compares</p>
            </div>
          </div>

          <div className="flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              href="/new-project"
              className="cta-primary shine-sweep inline-flex min-w-[250px] items-center justify-center rounded-xl border border-cyan-300/35 bg-[linear-gradient(90deg,#20bdd0,#4ed9ab)] px-6 py-3 text-lg font-semibold text-white transition-all duration-200 hover:brightness-110 shadow-[0_0_24px_rgba(56,211,190,0.5)]"
            >
              Create Your First Project
            </Link>
            <Link
              href="/new-project?sample=true"
              className="cta-secondary inline-flex min-w-[190px] items-center justify-center rounded-xl border border-slate-200/35 bg-[linear-gradient(180deg,rgba(245,248,255,0.22),rgba(173,186,210,0.22))] px-6 py-3 text-lg font-medium text-slate-100 transition-all duration-200 hover:border-slate-100/55 hover:bg-[linear-gradient(180deg,rgba(245,248,255,0.3),rgba(173,186,210,0.26))]"
            >
              Try Sample Dataset
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
