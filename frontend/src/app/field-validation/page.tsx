import type { Metadata } from "next";
import FieldValidationClient from "@/components/fieldValidation/FieldValidationClient";

/**
 * Field validation (Module 20).
 *
 * This page exists so the numbers North Pan actually had at the end of a rainfall
 * event can be compared with the numbers a rainfall-only mass balance predicts.
 * The comparison is what validation means here: it measures a gap, it never
 * certifies. Every figure on the page is either a reported figure or a figure the
 * backend recomputed from stored reports on read; there is no fabricated number.
 */

export const metadata: Metadata = {
  title: "Field validation",
  description:
    "Compare rainfall-only mass-balance predictions against reported post-event field measurements.",
};

export const dynamic = "force-dynamic";

export default function FieldValidationPage() {
  return <FieldValidationClient />;
}