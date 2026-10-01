import Sidebar from "@/components/layout/Sidebar";

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-1 flex-col lg:flex-row">
      <aside className="border-b border-line bg-surface lg:flex lg:w-64 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-2.5 px-5 py-4 lg:px-5 lg:py-5">
          <span
            aria-hidden
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-white"
          >
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            >
              <path d="M3 17c3 0 3-2.5 6-2.5S12 17 15 17s3-2.5 6-2.5" />
              <path d="M3 12c3 0 3-2.5 6-2.5S12 12 15 12s3-2.5 6-2.5" />
              <path d="M4 20h16" />
            </svg>
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight text-ink">
              SaltLens DSS
            </p>
            <p className="truncate text-[11px] text-muted">
              Salt-pan decision support
            </p>
          </div>
        </div>

        <div className="border-t border-line px-5 py-4 lg:px-3 lg:py-3">
          <Sidebar />
        </div>

        <p className="hidden border-t border-line px-5 py-4 text-[11px] leading-relaxed text-faint lg:block">
          Research prototype. Registry values are operator-entered; nothing in this
          interface is a simulated measurement.
        </p>
      </aside>

      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
