"use client";

import { useId } from "react";

const CONTROL_BASE =
  "w-full rounded-lg border bg-surface px-3 py-2 text-sm text-ink placeholder:text-faint transition-colors";

function controlClasses(invalid: boolean, extra?: string): string {
  return [
    CONTROL_BASE,
    invalid ? "border-danger bg-danger-soft/40" : "border-line-strong",
    extra ?? "",
  ].join(" ");
}

function FieldShell({
  id,
  label,
  unit,
  hint,
  error,
  required,
  children,
}: {
  id: string;
  label: string;
  unit?: string;
  hint?: string;
  error?: string | undefined;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="flex items-baseline gap-1.5 text-[13px] font-medium text-ink-soft">
        {label}
        {required ? (
          <span className="text-[11px] font-normal text-faint">required</span>
        ) : (
          <span className="text-[11px] font-normal text-faint">optional</span>
        )}
        {unit ? (
          <span className="ml-auto font-mono text-[11px] text-muted">{unit}</span>
        ) : null}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  unit,
  hint,
  error,
  placeholder,
  maxLength,
  disabled,
  readOnly,
  name,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  unit?: string;
  hint?: string;
  error?: string | undefined;
  placeholder?: string;
  maxLength?: number;
  disabled?: boolean;
  readOnly?: boolean;
  name?: string;
  autoComplete?: string;
}) {
  const generated = useId();
  const id = name ?? generated;
  return (
    <FieldShell
      id={id}
      label={label}
      unit={unit}
      hint={hint}
      error={error}
      required={false}
    >
      <input
        id={id}
        name={name}
        type="text"
        value={value}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={disabled}
        readOnly={readOnly}
        autoComplete={autoComplete}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange?.(event.target.value)}
        className={controlClasses(Boolean(error), readOnly ? "bg-inset text-muted" : "")}
      />
    </FieldShell>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  unit,
  hint,
  error,
  placeholder,
  step,
  min,
  max,
  disabled,
  readOnly,
  name,
  required = true,
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  unit?: string;
  hint?: string;
  error?: string | undefined;
  placeholder?: string;
  step?: number | "any";
  min?: number;
  max?: number;
  disabled?: boolean;
  readOnly?: boolean;
  name?: string;
  required?: boolean;
}) {
  const generated = useId();
  const id = name ?? generated;
  return (
    <FieldShell
      id={id}
      label={label}
      unit={unit}
      hint={hint}
      error={error}
      required={required}
    >
      <input
        id={id}
        name={name}
        type="number"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        readOnly={readOnly}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange?.(event.target.value)}
        className={`${controlClasses(Boolean(error), "tabular font-mono text-[13px]")} ${
          readOnly ? "bg-inset text-muted" : ""
        }`}
      />
    </FieldShell>
  );
}

export function TextAreaField({
  label,
  value,
  onChange,
  hint,
  error,
  placeholder,
  maxLength,
  rows = 3,
  disabled,
  name,
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  hint?: string;
  error?: string | undefined;
  placeholder?: string;
  maxLength?: number;
  rows?: number;
  disabled?: boolean;
  name?: string;
}) {
  const generated = useId();
  const id = name ?? generated;
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      error={error}
      required={false}
    >
      <textarea
        id={id}
        name={name}
        rows={rows}
        value={value}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        onChange={(event) => onChange?.(event.target.value)}
        className={`${controlClasses(Boolean(error), "resize-y text-[13px] leading-relaxed")} ${
          disabled ? "bg-inset text-muted" : ""
        }`}
      />
    </FieldShell>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
  error,
  name,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
  hint?: string;
  error?: string | undefined;
  name?: string;
  disabled?: boolean;
}) {
  const generated = useId();
  const id = name ?? generated;
  return (
    <FieldShell id={id} label={label} hint={hint} error={error} required={false}>
      <select
        id={id}
        name={name}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={controlClasses(Boolean(error), disabled ? "bg-inset text-muted" : "")}
      >
        {options.map((option) => (
          <option
            key={option.value}
            value={option.value}
            disabled={option.disabled}
          >
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}
