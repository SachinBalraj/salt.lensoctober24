import type { Metadata } from "next";
import EvaluationPanel from "@/components/evaluation/EvaluationPanel";

export const metadata: Metadata = {
  title: "Model evaluation",
  description:
    "Score a trained classifier against the rows it was held out on, and compare it with the alternatives that were fitted beside it.",
};

/**
 * Module 09: evaluate a registered classifier.
 *
 * A server component with no dynamic segment and no search parameters, so nothing
 * here needs to suspend and the panel fetches on the client.
 */
export default function EvaluationPage() {
  return <EvaluationPanel />;
}
