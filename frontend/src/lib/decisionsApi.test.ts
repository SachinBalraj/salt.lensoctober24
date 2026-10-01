import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  adviceCanRetry,
  advise,
  adviceLog,
  adviceUnavailableMessage,
  latestAdvice,
} from "@/lib/decisionsApi";
import { ADVICE_REFUSAL_MESSAGES } from "@/lib/decisionsRefusals";

const MINIMAL_ADVICE = {
  recommendation: "WAIT AND RECHECK",
  ml_result: null,
  basis: "physics_and_rules",
};

type FetchFn = (input: string, init: RequestInit) => Promise<unknown>;

/**
 * Stub `fetch` with a recorded mock.
 *
 * The parameters are typed rather than left implicit: an untyped `vi.fn` has an
 * empty parameter tuple, so reading the URL or the body off a recorded call is a
 * type error rather than a lookup -- which is the point, since these tests exist to
 * assert on exactly those two things.
 */
function mockFetch(
  response: Partial<Response> & { json?: () => Promise<unknown> },
) {
  const fetchMock = vi.fn<FetchFn>(async () => ({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    text: async () =>
      JSON.stringify(response.json ? await response.json() : MINIMAL_ADVICE),
    ...response,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(
  fetchMock: ReturnType<typeof mockFetch>,
): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("no request was made");
  return call;
}

function lastUrl(fetchMock: ReturnType<typeof mockFetch>): string {
  return lastCall(fetchMock)[0];
}

function lastBody(
  fetchMock: ReturnType<typeof mockFetch>,
): Record<string, unknown> {
  return JSON.parse(lastCall(fetchMock)[1].body as string);
}

describe("advise", () => {
  it("sends only the pan when nothing else is asked for", () => {
    // There is deliberately no way to send features, an action or a model. A
    // recommendation built from caller-chosen values would be indistinguishable
    // from one built from the twin and the written rule.
    const fetchMock = mockFetch({ ok: true });
    void advise({ panId: "kzn-2026-b01" });
    expect(lastBody(fetchMock)).toEqual({ pan_id: "kzn-2026-b01" });
  });

  it("sends the instant, the rule and the what-if depth when given", () => {
    const fetchMock = mockFetch({ ok: true });
    void advise({
      panId: "kzn-2026-b01",
      asOf: "2026-03-10T18:00:00Z",
      ruleId: "decrule-1",
      rainfallMm: 40,
    });
    expect(lastBody(fetchMock)).toEqual({
      pan_id: "kzn-2026-b01",
      as_of: "2026-03-10T18:00:00Z",
      rule_id: "decrule-1",
      rainfall_mm: 40,
    });
  });

  it("sends a rainfall depth of zero rather than dropping it", () => {
    // Zero is a real what-if: the "nothing happens" reference every other depth is
    // compared against. Treating it as an absence would quietly answer a different
    // question.
    const fetchMock = mockFetch({ ok: true });
    void advise({ panId: "kzn-2026-b01", rainfallMm: 0 });
    expect(lastBody(fetchMock)).toHaveProperty("rainfall_mm", 0);
  });

  it("never sends features, an action or a model", () => {
    const fetchMock = mockFetch({ ok: true });
    void advise({ panId: "kzn-2026-b01" });
    const body = lastBody(fetchMock);
    for (const forbidden of ["features", "action", "model", "salinity_g_l"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("asks the advise route", () => {
    const fetchMock = mockFetch({ ok: true });
    void advise({ panId: "kzn-2026-b01" });
    expect(lastUrl(fetchMock)).toContain("/api/decisions/advise");
  });
});

describe("latestAdvice", () => {
  it("asks the pan's latest route", () => {
    const fetchMock = mockFetch({ ok: true });
    void latestAdvice("kzn-2026-b01");
    expect(lastUrl(fetchMock)).toContain("/api/decisions/kzn-2026-b01/latest");
  });

  it("keeps the found flag rather than resolving to a bare recommendation", async () => {
    // The server answers `{ found, advice }` so that a pan nobody has asked about
    // is a documented state. A client that unwrapped this to `DecisionAdvice | null`
    // would type-check while handing callers a truthy object for `found: false`.
    mockFetch({ ok: true, json: () => Promise.resolve({ found: false, advice: null }) });
    const result = await latestAdvice("kzn-2026-b01");
    expect(result).toEqual({ found: false, advice: null });
    expect(result.advice).toBeNull();
  });

  it("carries the advice through when one is on record", async () => {
    const advice = { ...MINIMAL_ADVICE, recommendation: "HARVEST NOW" };
    mockFetch({ ok: true, json: () => Promise.resolve({ found: true, advice }) });
    const result = await latestAdvice("kzn-2026-b01");
    expect(result.found).toBe(true);
    expect(result.advice?.recommendation).toBe("HARVEST NOW");
  });
});

describe("adviceLog", () => {
  it("sends no query when nothing is narrowed", () => {
    const fetchMock = mockFetch({ ok: true });
    void adviceLog();
    expect(lastUrl(fetchMock)).toContain("/api/decisions/log");
    expect(lastUrl(fetchMock)).not.toContain("?");
  });

  it("narrows by pan and action", () => {
    const fetchMock = mockFetch({ ok: true });
    void adviceLog({ panId: "kzn-2026-b01", action: "HARVEST NOW" });
    const url = lastUrl(fetchMock);
    expect(url).toContain("pan_id=kzn-2026-b01");
    expect(url).toContain("action=HARVEST+NOW");
  });
});

describe("a refusal", () => {
  it("carries the backend's code through to the error", async () => {
    mockFetch({
      ok: false,
      status: 409,
      json: async () => ({ code: "no_rule_defined", detail: "No rule is written." }),
    });
    const error = await advise({ panId: "kzn-2026-b01" }).catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("no_rule_defined");
  });

  it("reaches the panel as one fixed sentence per code", () => {
    const error = new ApiError("No decision rule has been written.", {
      status: 409,
      code: "no_rule_defined",
    });
    expect(adviceUnavailableMessage(error)).toBe(
      ADVICE_REFUSAL_MESSAGES.no_rule_defined,
    );
  });

  it("passes an unrecognised refusal through rather than flattening it", () => {
    // An unrecognised code is a fact about the system this client does not yet
    // model, and hiding it would make a new refusal look like a familiar one.
    const error = new ApiError("Something new went wrong.", {
      status: 409,
      code: "some_future_refusal",
    });
    expect(adviceUnavailableMessage(error)).toBe("Something new went wrong.");
  });
});

describe("adviceCanRetry", () => {
  it("is false for every refusal, because waiting cannot write a rule", () => {
    for (const code of Object.keys(ADVICE_REFUSAL_MESSAGES)) {
      const error = new ApiError("x", { status: 409, code });
      expect(adviceCanRetry(error)).toBe(false);
    }
  });

  it("is true for a server fault", () => {
    expect(adviceCanRetry(new ApiError("x", { status: 503 }))).toBe(true);
  });

  it("is true when the backend could not be reached", () => {
    expect(adviceCanRetry(new ApiError("x", { status: 0 }))).toBe(true);
  });

  it("is false for a bad request, which will fail the same way again", () => {
    expect(adviceCanRetry(new ApiError("x", { status: 422 }))).toBe(false);
  });
});

describe("a connection failure", () => {
  it("is reported as a reachability problem, not as a refusal", () => {
    // The fix for this is not a deployment decision, so it must not borrow the
    // wording of a missing rule.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    return advise({ panId: "kzn-2026-b01" }).then(
      () => expect.unreachable("should have thrown"),
      (error: ApiError) => {
        expect(error.status).toBe(0);
        expect(error.message).toContain("Could not reach the SaltLens API");
      },
    );
  });
});
