/**
 * The retry rule, tested directly.
 *
 * Both the risk panel and the explanation panel decide whether to offer a "Try
 * again" button from this one function, so the rule is tested once here rather than
 * inferred from whichever panel happens to render in a given test.
 *
 * The rule exists because both obvious alternatives are wrong somewhere. A retry
 * offered for a refusal invites an operator to press a button whose answer cannot
 * change. A retry withheld for a transport failure denies the one case where
 * pressing again might work, and implies a decision has been made when in fact the
 * request never arrived.
 */

import { describe, expect, it } from "vitest";
import { ApiError, canRetry } from "@/lib/api";

describe("canRetry", () => {
  it("offers a retry when the request never reached the server", () => {
    // Status 0 is a transport failure: nothing has been decided, so asking again
    // is a reasonable act rather than a way of pretending something will change.
    expect(canRetry(new ApiError("unreachable", { status: 0 }))).toBe(true);
  });

  it("offers a retry for a gateway or service failure", () => {
    expect(canRetry(new ApiError("bad gateway", { status: 502 }))).toBe(true);
    expect(canRetry(new ApiError("unavailable", { status: 503 }))).toBe(true);
    expect(canRetry(new ApiError("gateway timeout", { status: 504 }))).toBe(true);
    expect(canRetry(new ApiError("server error", { status: 500 }))).toBe(true);
  });

  it("offers no retry for a refusal about the state of the system", () => {
    // Each of these is a standing fact. What is missing is a deployment, an
    // artifact or a probability -- none of which pressing a button supplies.
    for (const code of [
      "no_deployed_model",
      "model_reports_no_probability",
      "artifact_unavailable",
      "input_snapshot_mismatch",
      "explanation_not_stored",
    ]) {
      expect(canRetry(new ApiError(code, { status: 409, code }))).toBe(false);
    }
  });

  it("offers no retry when the request was refused as invalid", () => {
    // A 422 is the caller's own input being wrong. Asking again sends the same
    // wrong input and gets the same answer.
    expect(canRetry(new ApiError("invalid", { status: 422 }))).toBe(false);
  });

  it("offers no retry for a missing record", () => {
    expect(canRetry(new ApiError("not found", { status: 404 }))).toBe(false);
  });
});
