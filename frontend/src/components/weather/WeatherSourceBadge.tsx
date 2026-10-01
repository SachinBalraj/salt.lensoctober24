"use client";

import { Badge } from "@/components/ui/Primitives";
import { isDemonstrationSource } from "@/lib/demonstration";
import {
  describeWeatherSource,
  explainWeatherSource,
  isAutomatedSource,
  type WeatherSource,
} from "@/lib/weather";

/**
 * The provenance of one weather record, stated where it cannot be missed.
 *
 * A hand-entered record is the common case on this prototype, and it has to read
 * differently from an automated one. The badge is the short label; the sentence
 * underneath spells out where the numbers came from, because "Manual entry" and
 * "Weather API" sitting next to each other in a list is not enough to stop
 * someone later quoting the wrong one.
 *
 * A demonstration gets the project's demonstration tone rather than either of
 * those. Filing it as `active` would put a fabricated rainfall figure in the same
 * visual class as a real reading, and filing it as `neutral` would make it look
 * machine-retrieved — which is the one claim about it that is false. The
 * explanation underneath is the one from `lib/demonstration`, so the badge and the
 * sentence cannot say different things about the same record.
 */
export function WeatherSourceBadge({
  source,
  tone = "default",
}: {
  source: WeatherSource;
  tone?: "default" | "inline";
}) {
  const automated = isAutomatedSource(source);
  const demonstration = isDemonstrationSource(source);
  const badgeTone = demonstration ? "demo" : automated ? "neutral" : "active";

  if (tone === "inline") {
    return (
      <span className="text-[11px] font-medium text-muted">
        {describeWeatherSource(source)}
      </span>
    );
  }

  return (
    <span className="flex flex-col items-end gap-1 text-right">
      <Badge tone={badgeTone}>{describeWeatherSource(source)}</Badge>
      <span className="max-w-[15rem] text-[10px] leading-snug text-muted">
        {explainWeatherSource(source)}
      </span>
    </span>
  );
}
