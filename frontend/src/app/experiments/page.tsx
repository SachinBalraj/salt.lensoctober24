import type { Metadata } from "next";
import ExperimentsClient from "@/components/experiments/ExperimentsClient";

export const metadata: Metadata = {
  title: "Research experiments",
  description:
    "The append-only ledger of completed research runs, with the results and the reproducibility manifest for each.",
};

/**
 * Module 19: the research experiment ledger.
 *
 * A server component with no dynamic segment and no search parameters, so
 * nothing here needs to suspend. Every row on the page comes from a recorded
 * experiment in the backend ledger; this page only asks and renders.
 */
export default function ExperimentsPage() {
  return <ExperimentsClient />;
}