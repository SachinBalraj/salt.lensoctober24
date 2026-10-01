import type { Metadata } from "next";
import DashboardClient from "@/components/dashboard/DashboardClient";

export const metadata: Metadata = {
  title: "Dashboard · SaltLens DSS",
  description:
    "One salt pan's recorded condition, weather forecast, rainfall simulation, model risk estimate and the rule-backed operational recommendation.",
};

export default function DashboardPage() {
  return (
    <div className="mx-auto w-full max-w-7xl px-5 py-7 lg:px-8 lg:py-9">
      <DashboardClient />
    </div>
  );
}
