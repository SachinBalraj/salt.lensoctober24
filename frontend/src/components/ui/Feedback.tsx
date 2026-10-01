import type { ReactNode } from "react";

const TONES = {
  info: "border-accent-line bg-accent-soft text-ink-soft",
  danger: "border-danger-line bg-danger-soft text-danger",
  warning: "border-caution-line bg-caution-soft text-caution",
  success: "border-active-line bg-active-soft text-active",
  demo: "border-demo-line bg-demo-soft text-demo",
} as const;

export function Alert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: keyof typeof TONES;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={`flex flex-wrap items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${TONES[tone]}`}
    >
      <div className="min-w-0">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className="text-[13px] leading-relaxed">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div
        aria-hidden
        className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-inset text-muted"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        >
          <path d="M3 15c2.5 0 2.5 2 5 2s2.5-2 5-2 2.5 2 5 2 2.5-2 3-2" />
          <path d="M3 9c2.5 0 2.5 2 5 2s2.5-2 5-2 2.5 2 5 2 2.5-2 3-2" />
          <path d="M4 4h16v16H4z" opacity="0.35" />
        </svg>
      </div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-muted">
        {description}
      </p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded bg-line/70 ${className}`}
    />
  );
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2.5 p-5" aria-label="Loading" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-4">
          <Skeleton className="h-3.5 w-24" />
          <Skeleton className="h-3.5 flex-1" />
          <Skeleton className="h-3.5 w-20" />
        </div>
      ))}
      <span className="sr-only">Loading salt pans…</span>
    </div>
  );
}
