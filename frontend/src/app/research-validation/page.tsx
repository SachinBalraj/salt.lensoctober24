import type { Metadata } from "next";
import ValidationClient from "@/components/validation/ValidationClient";

export const metadata: Metadata = {
  title: "Research validation",
  description:
    "Stored predictions graded against recorded outcomes, with every metric beside the events that produced it.",
};

/**
 * Module 16: the research-validation dashboard.
 *
 * A server component with no dynamic segment and no search parameters, so
 * nothing here needs to suspend. Every figure on the page is computed by the
 * backend from recorded outcomes; this page only asks and renders.
 */
export default function ResearchValidationPage() {
  return <ValidationClient />;
}