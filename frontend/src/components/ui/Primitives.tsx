import type { ReactNode } from "react";

/** Small uppercase label used above groups and inside stat tiles. */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
      {children}
    </p>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-xs text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/**
 * The badge's tone vocabulary, exported so a `Record` keyed by a domain value
 * can be checked against it.
 *
 * Typed as the shape rather than the list of keys, so a domain map written
 * against it fails to compile the moment a tone is added and one of the sources
 * is left unmapped — which is the case where a record would render as a bare
 * unstyled pill.
 */
export const TONE_STYLES = {
  neutral: "border-line bg-inset text-ink-soft",
  active: "border-active-line bg-active-soft text-active",
  inactive: "border-line bg-inset text-muted",
  caution: "border-caution-line bg-caution-soft text-caution",
  danger: "border-danger-line bg-danger-soft text-danger",
  demo: "border-demo-line bg-demo-soft text-demo",
} as const;

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: keyof typeof TONE_STYLES;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${TONE_STYLES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function ActiveDot({ active }: { active: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-1.5 w-1.5 rounded-full ${active ? "bg-active" : "bg-faint"}`}
    />
  );
}

/** A label/value pair. Values use tabular figures so columns line up. */
export function DataPoint({
  label,
  value,
  unit,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </dt>
      <dd
        className={`mt-1 truncate text-sm text-ink ${mono ? "font-mono text-[13px]" : "tabular"}`}
      >
        {value}
        {value !== "—" && unit ? (
          <span className="ml-1 text-xs font-normal text-muted">{unit}</span>
        ) : null}
      </dd>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <Card className="px-4 py-3.5">
      <Eyebrow>{label}</Eyebrow>
      <p className="tabular mt-1.5 text-2xl font-semibold text-ink">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
    </Card>
  );
}
