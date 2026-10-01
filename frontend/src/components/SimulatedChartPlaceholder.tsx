"use client";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

const SIMULATED_SERIES = [
  { day: "D1", depth: 12.0 },
  { day: "D2", depth: 12.4 },
  { day: "D3", depth: 12.1 },
  { day: "D4", depth: 13.2 },
  { day: "D5", depth: 12.8 },
  { day: "D6", depth: 13.9 },
  { day: "D7", depth: 13.5 },
];

export default function SimulatedChartPlaceholder() {
  return (
    <section className="rounded-xl border border-line bg-surface p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Charting canvas
        </h2>
        <span className="rounded-full border border-caution-line bg-caution-soft px-2.5 py-0.5 text-xs font-semibold text-caution">
          SIMULATED DEMO — not research data
        </span>
      </div>
      <p className="mt-2 text-xs text-muted">
        Placeholder for recharts. Once Modules 3–6 land, this renders real
        observation and digital-twin time series from the backend. The curve
        below is a hard-coded UI scaffold and must never be mistaken for a
        measurement.
      </p>
      <div className="mt-4 h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={SIMULATED_SERIES} margin={{ left: -18, right: 10, top: 6 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="day" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} unit=" cm" />
            <Tooltip />
            <Line
              type="monotone"
              dataKey="depth"
              name="depth (simulated)"
              stroke="#0369a1"
              strokeWidth={2}
              dot={{ r: 2.5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}