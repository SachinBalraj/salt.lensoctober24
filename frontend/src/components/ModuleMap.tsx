import Link from "next/link";

const MODULES: Array<{
  n: number;
  name: string;
  prefix: string;
  state: "live" | "scaffold";
  href?: string;
}> = [
  { n: 1, name: "Authentication", prefix: "/api/auth", state: "scaffold" },
  {
    n: 2,
    name: "Salt-pan registration",
    prefix: "/api/pans",
    state: "live",
    href: "/pans",
  },
  {
    n: 3,
    name: "Pan observation / data entry",
    prefix: "/api/observations",
    state: "live",
    href: "/pans",
  },
  {
    n: 4,
    name: "Weather observation & forecast",
    prefix: "/api/weather",
    state: "live",
    href: "/pans",
  },
  {
    n: 5,
    name: "Pan-specific digital twin",
    prefix: "/api/twin",
    state: "live",
    href: "/pans",
  },
  {
    n: 6,
    name: "Rainfall mass-balance simulator",
    prefix: "/api/simulator",
    state: "live",
    href: "/pans",
  },
  { n: 7, name: "Dataset management", prefix: "/api/datasets", state: "live" },
  { n: 8, name: "ML training pipeline", prefix: "/api/ml", state: "live" },
  {
    n: 9,
    name: "Model evaluation / comparison",
    prefix: "/api/evaluation",
    state: "live",
    href: "/evaluation",
  },
  {
    n: 10,
    name: "Risk prediction",
    prefix: "/api/risk",
    state: "live",
    href: "/pans",
  },
  {
    n: 11,
    name: "SHAP explainability",
    prefix: "/api/explain",
    state: "live",
    // The per-prediction attribution sits on the pan, beside the prediction it
    // explains; the global summary sits on the evaluation page, beside the
    // held-out score it was computed from. Neither has a page of its own,
    // because neither is a separate object in this application.
    href: "/evaluation",
  },
  {
    n: 12,
    name: "Decision-support engine",
    prefix: "/api/decisions",
    state: "live",
    // The recommendation is shown on the pan, beside the twin state, the
    // simulation and the model estimate it was made from. Those are the things
    // that make it checkable, and a page of its own would separate them.
    href: "/pans",
  },
  { n: 13, name: "Operator dashboard", prefix: "/api/dashboard", state: "scaffold" },
  { n: 14, name: "Operator feedback", prefix: "/api/feedback", state: "scaffold" },
  {
    n: 15,
    name: "Actual-outcome recording",
    prefix: "/api/outcomes",
    state: "live",
    href: "/predicted-vs-actual",
  },
  {
    n: 16,
    name: "Predicted-vs-actual validation",
    prefix: "/api/validation",
    state: "live",
    href: "/research-validation",
  },
  { n: 17, name: "Model versioning / retraining", prefix: "/api/models", state: "scaffold" },
  { n: 18, name: "Research data export", prefix: "/api/export", state: "scaffold" },
  {
    n: 19,
    name: "Research experiments",
    prefix: "/api/experiments",
    state: "live",
    href: "/experiments",
  },
  {
    n: 20,
    name: "Field validation",
    prefix: "/api/field-validation",
    state: "live",
    href: "/field-validation",
  },
];

const LIVE_COUNT = MODULES.filter((module) => module.state === "live").length;

export default function ModuleMap() {
  return (
    <section className="rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
        <h2 className="text-sm font-semibold text-ink">System modules</h2>
        <span className="tabular rounded-full border border-line bg-inset px-2.5 py-0.5 text-[11px] font-medium text-muted">
          {LIVE_COUNT} of {MODULES.length} live
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-2 p-4 sm:grid-cols-2">
        {MODULES.map((module) => {
          const body = (
            <>
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                  module.state === "live"
                    ? "bg-accent text-white"
                    : "bg-inset text-muted"
                }`}
              >
                {module.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-ink">
                  {module.name}
                </span>
                <code className="block truncate text-[11px] text-muted">
                  {module.prefix}
                </code>
              </span>
              {module.state === "live" ? (
                <span className="shrink-0 rounded-full border border-active-line bg-active-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-active">
                  Live
                </span>
              ) : (
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-faint">
                  Scaffold
                </span>
              )}
            </>
          );

          return (
            <li key={module.n}>
              {module.href ? (
                <Link
                  href={module.href}
                  className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 transition-colors hover:bg-inset"
                >
                  {body}
                </Link>
              ) : (
                <div className="flex items-center gap-2.5 rounded-lg border border-dashed border-line px-3 py-2">
                  {body}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
