"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/Primitives";
import type { PanView } from "@/lib/demoPans";
import {
  formatArea,
  formatCoordinate,
  formatMeasurement,
} from "@/lib/pans";

export function PanStatusBadge({
  isActive,
  origin,
}: {
  isActive: boolean;
  origin: PanView["origin"];
}) {
  if (origin === "demo") {
    return <Badge tone="demo">Demo data</Badge>;
  }
  return isActive ? (
    <Badge tone="active">Active</Badge>
  ) : (
    <Badge tone="inactive">Deactivated</Badge>
  );
}

export default function PanTable({
  views,
  hrefFor,
}: {
  views: PanView[];
  hrefFor: (panId: string) => string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[52rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            {[
              ["Pan ID", "w-[9rem]"],
              ["Pan name", ""],
              ["Location", "w-[12rem]"],
              ["Coordinates", "w-[13rem]"],
              ["Area", "w-[7rem]"],
              ["Normal depth", "w-[7.5rem]"],
              ["Target salinity", "w-[8rem]"],
              ["Status", "w-[9rem]"],
            ].map(([label, width]) => (
              <th
                key={label}
                scope="col"
                className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted ${width}`}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {views.map(({ pan, origin }) => (
            <tr
              key={`${origin}-${pan.pan_id}`}
              className="border-b border-line/70 transition-colors last:border-b-0 hover:bg-inset"
            >
              <td className="px-4 py-3">
                <Link
                  href={hrefFor(pan.pan_id)}
                  className="font-mono text-[13px] font-medium text-accent hover:underline"
                >
                  {pan.pan_id}
                </Link>
              </td>
              <td className="max-w-[18rem] truncate px-4 py-3 text-sm text-ink">
                {pan.pan_name}
              </td>
              <td className="max-w-[12rem] truncate px-4 py-3 text-[13px] text-muted">
                {pan.location}
              </td>
              <td className="tabular px-4 py-3 font-mono text-[12px] text-ink-soft">
                {pan.latitude === null || pan.longitude === null ? (
                  <span className="text-faint">Not geolocated</span>
                ) : (
                  `${formatCoordinate(pan.latitude, "lat")}, ${formatCoordinate(pan.longitude, "lon")}`
                )}
              </td>
              <td className="tabular px-4 py-3 text-[13px] text-ink-soft">
                {formatArea(pan.area_m2)}
              </td>
              <td className="tabular px-4 py-3 text-[13px] text-ink-soft">
                {formatMeasurement(pan.normal_depth_cm, "cm")}
              </td>
              <td className="tabular px-4 py-3 text-[13px] text-ink-soft">
                {formatMeasurement(pan.target_harvest_salinity_g_l, "g/L")}
              </td>
              <td className="px-4 py-3">
                <PanStatusBadge isActive={pan.is_active} origin={origin} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
