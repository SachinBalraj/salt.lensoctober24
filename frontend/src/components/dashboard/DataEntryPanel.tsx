"use client";

import { useState } from "react";
import ObservationForm from "@/components/observations/ObservationForm";
import WeatherForm from "@/components/weather/WeatherForm";
import { Alert } from "@/components/ui/Feedback";
import { DEMONSTRATION_EXPLANATION, DEMONSTRATION_NOTICE } from "@/lib/demonstration";
import type { PanObservation } from "@/lib/observations";
import type { WeatherObservation } from "@/lib/weather";

/**
 * The two record-entry forms, on the dashboard, for the pan already selected.
 *
 * They existed before this panel, on the pan's own page, and they are reused here
 * rather than rebuilt: same components, same validation, same payloads, so a
 * reading entered from the dashboard and a reading entered from the pan page are
 * the same record with the same provenance rules. A second copy of these forms
 * would be a second set of rules, and the two would drift.
 *
 * **The pan is not chosen here.** It is the one the operator selected at the top
 * of the dashboard, and a form beside a heading that names a different pan is the
 * classic way a reading ends up filed against the wrong salt pan. The forms take
 * the id as a prop and show it, so the record names its pan before it is written
 * rather than after.
 *
 * **Collapsed by default.** The dashboard's job is to show what is known; two
 * full forms above that are the loudest thing on the page and mostly unused. The
 * panel stays open across a save, because recording a reading and then
 * immediately recording the weather that explains it is the normal sequence and
 * should not need a second click.
 *
 * **This panel is not one of the six provenance categories, and does not claim to
 * be.** It is a form, and a form holds no figure: the six categories say where a
 * *number* came from, and before something is typed there is no number to place.
 * A previous version of this panel was filed under `operator_decision` on the
 * reasoning that entering a value is a person's act. That was a rationalisation
 * of a mistake, and it did damage: the dashboard asserts that a decision card
 * never contains a figure, and a decision card containing a pan id and a
 * six-hour lead time is a real breach of that promise to a reader. It is not
 * `observed` either — putting a form in the same category as the reading it
 * produces invites a reader to take the presence of the form as evidence that
 * something had been measured. A plain section makes no claim, which is the
 * honest option, and the forms inside declare provenance on their own saved
 * records, where there is a number to declare it about.
 */
export default function DataEntryPanel({
  panId,
  panName,
  sensorAvailable = false,
  weatherProvidersConnected = 0,
  onRecorded,
}: {
  panId: string;
  panName?: string;
  sensorAvailable?: boolean;
  weatherProvidersConnected?: number;
  /** Called after either form saves, so the dashboard can re-read its state. */
  onRecorded: (record: PanObservation | WeatherObservation) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"observation" | "weather">("observation");
  const [recent, setRecent] = useState<string | null>(null);

  function record(kind: "observation" | "weather") {
    return (_record: PanObservation | WeatherObservation) => {
      setRecent(
        kind === "observation"
          ? "Observation saved. The cards above have been re-read."
          : "Weather saved. The cards above have been re-read.",
      );
      onRecorded(_record);
    };
  }

  return (
    <section
      aria-labelledby="data-entry-heading"
      data-testid="data-entry-panel"
      className="overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2
            id="data-entry-heading"
            className="text-[15px] font-semibold tracking-tight text-ink"
          >
            Record a reading
          </h2>
          <p className="mt-1 text-[12px] leading-relaxed text-muted">
            An input form, not a reading. Nothing here is a figure until you type
            one, and whatever you type declares its own provenance when it is
            saved.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="data-entry-panel-body"
          data-testid="data-entry-toggle"
          className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-soft transition-colors hover:bg-inset"
        >
          {open ? "Close" : "Open"}
        </button>
      </div>
      <div className={open ? "px-5 py-4" : "px-5 py-3"}>
      {!open ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12px] leading-relaxed text-muted">
            Anything saved here is recorded against{" "}
            <span className="font-medium text-ink-soft">
              {panName ? `${panName} · ` : ""}
              {panId}
            </span>
            . The observation form opens on demonstration values, which are stored
            as demonstrations: visible on the dashboard, and never part of a
            research dataset.
          </p>
          <p className="text-[11px] leading-relaxed text-demo">{DEMONSTRATION_NOTICE}</p>
        </div>
      ) : (
        <div id="data-entry-panel-body" className="space-y-4">
          {recent ? (
            <Alert
              tone="success"
              action={
                <button
                  type="button"
                  onClick={() => setRecent(null)}
                  className="rounded-lg border border-line bg-surface px-2.5 py-1 text-[12px] text-ink-soft"
                >
                  Dismiss
                </button>
              }
            >
              {recent}
            </Alert>
          ) : null}

          <Alert
            tone="demo"
            title="The observation form opens on demonstration values"
          >
            <p>{DEMONSTRATION_EXPLANATION}</p>
            <p className="mt-1.5">
              The observation form also offers{" "}
              <strong className="font-semibold">A measurement</strong>. The weather
              form opens on a blank real reading or forecast and offers a
              demonstration of its own. Whichever you pick is stored under its own
              provenance, and the two are never mixed.
            </p>
          </Alert>

          <div
            role="tablist"
            aria-label="What to record"
            className="flex flex-wrap gap-1 border-b border-line"
          >
            {(
              [
                { id: "observation", label: "Observation" },
                { id: "weather", label: "Weather" },
              ] as const
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
                className={`-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors ${
                  tab === item.id
                    ? "border-accent text-ink"
                    : "border-transparent text-muted hover:text-ink-soft"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>

          {tab === "observation" ? (
            <div className="overflow-hidden rounded-lg border border-line">
              <ObservationForm
                panId={panId}
                sensorAvailable={sensorAvailable}
                onRecorded={record("observation")}
              />
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-line">
              <WeatherForm
                panId={panId}
                sensorAvailable={sensorAvailable}
                providersConnected={weatherProvidersConnected}
                onRecorded={record("weather")}
              />
            </div>
          )}
        </div>
      )}
      </div>
    </section>
  );
}
