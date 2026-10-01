import type { Metadata } from "next";
import PredictedVsActualClient from "@/components/outcomes/PredictedVsActualClient";

export const metadata: Metadata = {
  title: "Predicted vs actual",
  description:
    "Measured error between the mass-balance prediction and the recorded outcome of each rainfall event.",
};

/**
 * Module 15 across every pan: what the model predicted, what the pans actually
 * did, and how far apart the two were.
 *
 * A server component with no dynamic segment and no search parameters, so
 * nothing here needs to suspend.
 */
export default function PredictedVsActualPage() {
  return <PredictedVsActualClient />;
}
