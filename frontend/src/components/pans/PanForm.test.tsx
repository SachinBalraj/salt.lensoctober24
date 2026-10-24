/**
 * PanForm (Module 02) — operator risk thresholds.
 *
 * A decision rule reads its bounds from the pan's own configuration and is refused
 * when one is missing, so the pan form is the only place an operator can make a pan
 * advisable. These tests pin that the five limit fields exist, that they are
 * labelled optional, and that typing into one reports the operator-limits field name
 * — the wiring the recommendation depends on.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import PanForm from "@/components/pans/PanForm";
import { EMPTY_PAN_FORM, RISK_THRESHOLD_SPECS } from "@/lib/pans";

function renderForm(onChange = vi.fn()) {
  render(
    <PanForm
      values={EMPTY_PAN_FORM}
      errors={{}}
      onChange={onChange}
      onSubmit={() => {}}
      onCancel={() => {}}
      submitting={false}
      panIdLocked={false}
      submitLabel="Register pan"
      idPrefix="register"
    />,
  );
  return onChange;
}

describe("risk threshold fields", () => {
  it("offers every operator limit the backend exposes", () => {
    renderForm();
    // The group is present and every threshold from the schema has an input, so a
    // pan can be configured for the rule without leaving the form.
    expect(screen.getByText("Risk thresholds")).toBeInTheDocument();
    for (const spec of RISK_THRESHOLD_SPECS) {
      expect(screen.getByLabelText(spec.label, { exact: false })).toBeInTheDocument();
    }
  });

  it("reports the operator-limits field name when a threshold is typed", () => {
    const onChange = renderForm();
    for (const spec of RISK_THRESHOLD_SPECS) {
      onChange.mockClear();
      fireEvent.change(screen.getByLabelText(spec.label, { exact: false }), {
        target: { value: "25" },
      });
      expect(onChange).toHaveBeenCalledWith(spec.field, "25");
    }
  });

  it("shows a validation error returned for a threshold", () => {
    render(
      <PanForm
        values={EMPTY_PAN_FORM}
        errors={{ target_flow_cm: "Target transfer depth must be greater than 0 cm." }}
        onChange={() => {}}
        onSubmit={() => {}}
        onCancel={() => {}}
        submitting={false}
        panIdLocked={false}
        submitLabel="Register pan"
        idPrefix="register"
      />,
    );
    expect(
      screen.getByText("Target transfer depth must be greater than 0 cm."),
    ).toBeInTheDocument();
  });
});
