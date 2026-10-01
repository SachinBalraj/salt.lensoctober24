"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  href: string;
  label: string;
  description: string;
};

const NAV: NavItem[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    description: "One pan: evidence, simulation, model and recommendation",
  },
  {
    href: "/",
    label: "Overview",
    description: "System health and module map",
  },
  {
    href: "/pans",
    label: "Salt pans",
    description: "Register and manage production units",
  },
  {
    href: "/evaluation",
    label: "Model evaluation",
    description: "Score a classifier against held-out rows",
  },
  {
    href: "/predicted-vs-actual",
    label: "Predicted vs actual",
    description: "Model error against recorded outcomes",
  },
  {
    href: "/research-validation",
    label: "Research validation",
    description: "Stored predictions graded against what happened",
  },
  {
    href: "/experiments",
    label: "Research experiments",
    description: "The append-only ledger of completed runs",
  },
  {
    href: "/field-validation",
    label: "Field validation",
    description: "Reported post-event readings against the rainfall replay",
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="flex flex-col gap-1">
      <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
        Workspace
      </p>
      <div className="flex flex-col gap-1 sm:flex-row lg:flex-col">
        {NAV.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex-1 rounded-lg px-3 py-2 transition-colors ${
                active
                  ? "bg-accent-soft text-accent ring-1 ring-accent-line"
                  : "text-ink-soft hover:bg-inset"
              }`}
            >
              <span className="block text-[13px] font-semibold">{item.label}</span>
              <span
                className={`mt-0.5 hidden text-[11px] leading-snug sm:block lg:block ${
                  active ? "text-accent/80" : "text-muted"
                }`}
              >
                {item.description}
              </span>
            </Link>
          );
        })}
      </div>

      <div className="hidden lg:block">
        <p className="px-3 pb-1.5 pt-5 text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Remaining modules
        </p>
        <p className="rounded-lg border border-dashed border-line-strong px-3 py-2.5 text-[11px] leading-relaxed text-muted">
          Training, datasets and export are wired as backend routers but not yet
          built. The dashboard, registry, evaluation and outcome screens are
          surfaced above.
        </p>
      </div>
    </nav>
  );
}
