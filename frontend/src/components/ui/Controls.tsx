"use client";

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-white border-accent hover:bg-accent-hover hover:border-accent-hover disabled:bg-faint disabled:border-faint",
  secondary:
    "bg-surface text-ink-soft border-line-strong hover:bg-inset disabled:text-faint",
  ghost:
    "bg-transparent text-muted border-transparent hover:bg-inset hover:text-ink disabled:text-faint",
  danger:
    "bg-danger text-white border-danger hover:opacity-90 disabled:bg-faint disabled:border-faint",
};

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border px-3.5 py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed";

type ButtonProps = ComponentProps<"button"> & {
  variant?: Variant;
};

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`${BASE} ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

type ButtonLinkProps = ComponentProps<typeof Link> & {
  variant?: Variant;
};

export function ButtonLink({
  variant = "secondary",
  className = "",
  ...props
}: ButtonLinkProps) {
  return (
    <Link className={`${BASE} ${VARIANTS[variant]} ${className}`} {...props} />
  );
}

export function Spinner({ label = "Working" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
      />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export function ButtonRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

/**
 * A two-or-more-way exclusive choice, rendered as one control.
 *
 * Native radio inputs rather than a row of buttons, because a row of buttons
 * cannot express "one of these" to assistive technology or to a keyboard: it
 * reads as several actions, and tab moves between them rather than arrows. The
 * inputs are visually hidden but not `display: none`, so they keep focus, and
 * the visible labels carry the styling, which means the focus ring lands on
 * something the reader can see.
 *
 * Used for the one decision this application cannot infer: whether a value is a
 * measurement or a demonstration. That decision is worth a control that states
 * both options in full rather than a checkbox whose checked state has to be
 * remembered to be read.
 */
export function SegmentedControl<T extends string>({
  legend,
  value,
  onChange,
  options,
  disabled = false,
  name,
  className = "",
}: {
  /** Names the group. Rendered as the legend, so it is read before the options. */
  legend: string;
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; description?: string }>;
  disabled?: boolean;
  /** Shared `name` for the radio group. Required: two groups on one page would
   * otherwise be one group to the browser and a single arrow-key selection. */
  name: string;
  className?: string;
}) {
  return (
    <fieldset
      disabled={disabled}
      className={`min-w-0 ${className}`}
      data-testid={`${name}-group`}
    >
      <legend className="text-[13px] font-medium text-ink-soft">{legend}</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <label
              key={option.value}
              className={`relative flex min-w-0 flex-1 cursor-pointer flex-col rounded-lg border px-3 py-2 transition-colors ${
                selected
                  ? "border-accent bg-accent-soft"
                  : "border-line-strong bg-surface hover:bg-inset"
              } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={selected}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              <span
                className={`text-[13px] font-medium ${
                  selected ? "text-ink" : "text-ink-soft"
                }`}
              >
                {option.label}
              </span>
              {option.description ? (
                <span className="mt-0.5 text-[11px] leading-relaxed text-muted">
                  {option.description}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
