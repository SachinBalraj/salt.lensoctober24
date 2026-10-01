/**
 * Module 14 HTTP client tests.
 *
 * The assertions that matter are the two this client was written around: the
 * request carries no pan and no recommended action — the server reads both from
 * the stored advice — and a refusal to record is one fixed sentence, shown as the
 * state of the system it is rather than as an error in the request.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  feedbackCanRetry,
  feedbackForRecommendation,
  feedbackUnavailableMessage,
  recordFeedback,
} from "@/lib/operatorFeedbackApi";

const MINIMAL_RECORD = {
  decision_id: "dec-1",
  recommendation_id: "rec-1",
  decision: "accepted",
};

type FetchFn = (input: string, init: RequestInit) => Promise<unknown>;

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn<FetchFn>(async () => ({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    text: async () => JSON.stringify(response.json ? await response.json() : MINIMAL_RECORD),
    ...response,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(fetchMock: ReturnType<typeof mockFetch>): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("no request was made");
  return call;
}

function lastUrl(fetchMock: ReturnType<typeof mockFetch>): string {
  return lastCall(fetchMock)[0];
}

function lastBody(fetchMock: ReturnType<typeof mockFetch>): Record<string, unknown> {
  return JSON.parse(lastCall(fetchMock)[1].body as string);
}

describe("recordFeedback", () => {
  it("sends only the recommendation and the disposition", () => {
    // There is deliberately no way to send a pan or a recommended action — the
    // server reads both off the stored advice.
    const fetchMock = mockFetch({ ok: true });
    void recordFeedback({
      recommendationId: "rec-1",
      decision: "accepted",
    });
    expect(lastUrl(fetchMock)).toMatch(/^http:\/\/localhost:8000\/api\/feedback\/$/);
    expect(lastBody(fetchMock)).toEqual({
      recommendation_id: "rec-1",
      decision: "accepted",
    });
  });

  it("sends the actual action, the operator and the note as snake_case", () => {
    const fetchMock = mockFetch({ ok: true });
    void recordFeedback({
      recommendationId: "rec-1",
      decision: "modified",
      actualAction: "TRANSFER BRINE",
      operatorId: "M. Ndlovu",
      notes: "Drained early.",
    });
    expect(lastBody(fetchMock)).toEqual({
      recommendation_id: "rec-1",
      decision: "modified",
      actual_action: "TRANSFER BRINE",
      operator_id: "M. Ndlovu",
      notes: "Drained early.",
    });
  });

  it("omits the action for an acceptance, letting the server fill the recommended one", () => {
    const fetchMock = mockFetch({ ok: true });
    void recordFeedback({
      recommendationId: "rec-1",
      decision: "accepted",
    });
    expect(lastBody(fetchMock)).not.toHaveProperty("actual_action");
  });

  it("carries the refusal code through", async () => {
    const fetchMock = mockFetch({
      ok: false,
      status: 409,
      json: async () => ({
        detail: "A decision already exists for this recommendation.",
        code: "decision_already_recorded",
      }),
    });

    await expect(
      recordFeedback({ recommendationId: "rec-1", decision: "accepted" }),
    ).rejects.toMatchObject({
      status: 409,
      code: "decision_already_recorded",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("feedbackForRecommendation", () => {
  it("queries the recommendation id only", () => {
    const fetchMock = mockFetch({
      ok: true,
      json: async () => ({ count: 1, items: [MINIMAL_RECORD] }),
    });
    void feedbackForRecommendation("rec-1");
    expect(lastUrl(fetchMock)).toContain("/api/feedback/?recommendation_id=rec-1");
  });
});

describe("the refusal sentences", () => {
  it("maps each recorded code to one fixed sentence", () => {
    const error = new ApiError("a server in a log", {
      status: 409,
      code: "decision_already_recorded",
    });
    expect(feedbackUnavailableMessage(error)).toMatch(/already has a recorded decision/);
  });

  it("passes an unrecognised code through rather than flattening it", () => {
    const error = new ApiError("Something new the client has not modelled.", {
      status: 409,
      code: "some_distant_future_refusal",
    });
    expect(feedbackUnavailableMessage(error)).toBe(
      "Something new the client has not modelled.",
    );
  });

  it("offers no retry for a refusal, and one for a connectivity failure", () => {
    const recorded = new ApiError("already decided", {
      status: 409,
      code: "decision_already_recorded",
    });
    expect(feedbackCanRetry(recorded)).toBe(false);

    const offline = new ApiError("offline", { status: 0 });
    expect(feedbackCanRetry(offline)).toBe(true);
  });
});