"use client";

import { useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";

type MetricKey = "accuracy" | "f1";

export interface ModelMetric {
  name: string;
  accuracy: number;
  f1: number;
  trainingTime: number;
}

interface ModelComparisonProps {
  data: ModelMetric[];
  bestModelName?: string;
}

const metricLabels: Record<MetricKey, string> = {
  accuracy: "Accuracy",
  f1: "F1 Score",
};

function DarkTooltip({ active, payload, label }: { active?: boolean; payload?: any[]; label?: string }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-xl border border-[#1e3a52] bg-[#0F172A]/95 px-3 py-2 shadow-[0_12px_30px_rgba(11,31,58,0.5)]">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      {payload.map((item) => (
        <div key={item.dataKey} className="flex items-center justify-between gap-4">
          <span className="text-xs text-slate-300">{item.name}</span>
          <span className="text-xs font-semibold text-[#5EDC8A]">
            {typeof item.value === "number" ? item.value.toFixed(3) : item.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function ModelComparison({ data, bestModelName }: ModelComparisonProps) {
  const [metric, setMetric] = useState<MetricKey>("accuracy");

  const bestName = useMemo(() => {
    if (bestModelName) return bestModelName;
    if (!data.length) return "";
    const sorted = [...data].sort((a, b) => b[metric] - a[metric]);
    return sorted[0]?.name ?? "";
  }, [bestModelName, data, metric]);

  return (
    <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_30px_rgba(11,31,58,0.6)] p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-tight text-slate-100">Model Comparison</h3>
          <p className="text-xs text-slate-500 mt-1">Compare models by performance and training time</p>
        </div>
        <div className="flex items-center gap-2">
          {(["accuracy", "f1"] as MetricKey[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setMetric(key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${
                metric === key
                  ? "text-[#5EDC8A] border-[#3BB273]/40 bg-[#3BB273]/10 shadow-[0_0_12px_rgba(59,178,115,0.25)]"
                  : "text-slate-400 border-[#1e3a52] hover:border-[#2c5278] hover:text-slate-200"
              }`}
            >
              {metricLabels[key]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <defs>
                <linearGradient id="metricGradient" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#3BB273" />
                  <stop offset="100%" stopColor="#5EDC8A" />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1e3a52" strokeDasharray="3 6" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
              <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
              <Bar
                dataKey={metric}
                name={metricLabels[metric]}
                fill="url(#metricGradient)"
                radius={[8, 8, 6, 6]}
                isAnimationActive
                animationDuration={800}
              >
                {data.map((entry) => (
                  <Cell
                    key={entry.name}
                    fill={entry.name === bestName ? "#3BB273" : "url(#metricGradient)"}
                    stroke={entry.name === bestName ? "#5EDC8A" : "transparent"}
                    strokeWidth={entry.name === bestName ? 1.2 : 0}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <defs>
                <linearGradient id="timeGradient" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#123C66" />
                  <stop offset="100%" stopColor="#3BB273" />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1e3a52" strokeDasharray="3 6" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
              <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
              <Bar
                dataKey="trainingTime"
                name="Training Time"
                fill="url(#timeGradient)"
                radius={[8, 8, 6, 6]}
                isAnimationActive
                animationDuration={800}
              >
                {data.map((entry) => (
                  <Cell
                    key={entry.name}
                    fill={entry.name === bestName ? "#123C66" : "url(#timeGradient)"}
                    stroke={entry.name === bestName ? "#1e3a52" : "transparent"}
                    strokeWidth={entry.name === bestName ? 1.2 : 0}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
