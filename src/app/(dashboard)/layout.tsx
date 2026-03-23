import Sidebar from "@/components/Sidebar";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen text-slate-100 bg-[radial-gradient(circle_at_15%_8%,rgba(40,55,140,0.15),transparent_32%),radial-gradient(circle_at_80%_12%,rgba(30,130,120,0.1),transparent_28%),radial-gradient(circle_at_50%_85%,rgba(15,25,55,0.25),transparent_45%),linear-gradient(180deg,#050c1c_0%,#030812_100%)]">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {children}
        <div className="shrink-0 py-2 text-center text-[11px] text-slate-600">
          Developed by Yash Tapase
        </div>
      </div>
    </div>
  );
}
