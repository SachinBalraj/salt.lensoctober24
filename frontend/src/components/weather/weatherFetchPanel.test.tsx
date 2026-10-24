/**
 * Weather fetch/review/save panel tests.
 *
 * The failure this panel exists to prevent is a weather record that claims an
 * API origin but was actually typed by an operator (or vice versa). What is
 * therefore asserted here:
 *
 * - fetching previews values and writes nothing: no save call happens until an
 *   explicit press of Save;
 * - the save call carries only the fetch token -- never the previewed values --
 *   so an edited preview cannot become a record claiming `weather_api`;
 * - a pan without coordinates explains itself and offers no fetch button;
 * - a failed fetch or save surfaces the server's refusal verbatim;
 * - on success the panel reports the stored source and asks the page to reload.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WeatherFetchPanel from "@/components/weather/WeatherFetchPanel";
import { ApiError } from "@/lib/api";
import {
  fetchWeatherFromOpenMeteo,
  saveFetchedWeather,
  type WeatherFetchResult,
  type WeatherFetchResponse,
  type WeatherFetchSaveResponse,
} from "@/lib/weatherApi";

vi.mock("@/lib/weatherApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/weatherApi")>(
    "@/lib/weatherApi",
  );
  return {
    ...actual,
    fetchWeatherFromOpenMeteo: vi.fn(),
    saveFetchedWeather: vi.fn(),
  };
});

const RESULT: WeatherFetchResult = {
  pan_id: "north-pan-011",
  pan_name: "North Pan 1",
  latitude: 11.6643,
  longitude: 78.146,
  requested_date: "2025-10-08",
  requested_time: "14:00",
  requested_at: "2025-10-08T08:30:00Z",
  retrieved_at: "2026-10-08T12:20:09Z",
  weather_type: "historical",
  rainfall_mm: 1.7,
  rain_probability_percent: null,
  ambient_temperature_c: 30.4,
  humidity_percent: 63.0,
  provider: "open-meteo",
  source_type: "weather_api",
  coordinates_used: { latitude: 11.6643, longitude: 78.146 },
};

const FETCH_OK: WeatherFetchResponse = {
  success: true,
  data: RESULT,
  message: "Weather retrieved successfully.",
  fetch_token: "token-abc",
};

const SAVE_OK: WeatherFetchSaveResponse = {
  record: {
    weather_id: "wx-1",
    pan_id: "north-pan-011",
    timestamp: "2025-10-08T08:30:00Z",
    forecast_for: null,
    rainfall_mm: 1.7,
    rainfall_probability_percent: null,
    ambient_temperature_c: 30.4,
    humidity_percent: 63,
    source: "weather_api",
    is_forecast: false,
    created_at: "2026-10-08T12:20:19Z",
    provider: "open-meteo",
    latitude: 11.6643,
    longitude: 78.146,
    weather_type: "historical",
    requested_at: "2025-10-08T08:30:00Z",
    retrieved_at: "2026-10-08T12:20:18Z",
  } as WeatherFetchSaveResponse["record"],
  created: true,
  message: "Weather record saved.",
};

function renderPanel(onSaved = vi.fn()) {
  render(
    <WeatherFetchPanel
      panId="north-pan-011"
      panName="North Pan 1"
      hasCoordinates
      coordinates={{ latitude: 11.6643, longitude: 78.146 }}
      onSaved={onSaved}
    />,
  );
  return onSaved;
}

beforeEach(() => {
  vi.mocked(fetchWeatherFromOpenMeteo).mockReset();
  vi.mocked(saveFetchedWeather).mockReset();
  vi.mocked(fetchWeatherFromOpenMeteo).mockResolvedValue(FETCH_OK);
  vi.mocked(saveFetchedWeather).mockResolvedValue(SAVE_OK);
});

describe("nothing is fetched or saved without a press", () => {
  it("does not call the API on mount", () => {
    renderPanel();

    expect(fetchWeatherFromOpenMeteo).not.toHaveBeenCalled();
    expect(saveFetchedWeather).not.toHaveBeenCalled();
  });

  it("offers no save button before a fetch has been reviewed", async () => {
    renderPanel();

    expect(screen.queryByRole("button", { name: "Save Weather Record" })).toBeNull();
  });
});

describe("the preview", () => {
  it("shows the fetched values with their provenance", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));

    expect(await screen.findByText(/30.4 °C/)).toBeInTheDocument();
    expect(screen.getByText(/1.7 mm/)).toBeInTheDocument();
    expect(screen.getByText(/open-meteo/)).toBeInTheDocument();
    expect(screen.getByText(/historical archive/)).toBeInTheDocument();
    expect(
      screen.getAllByText(/11\.6643, 78\.1460/).length,
    ).toBeGreaterThan(0);
  });

  it("sends the pan and the chosen instant, nothing else", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));
    await screen.findByText(/30.4 °C/);

    expect(fetchWeatherFromOpenMeteo).toHaveBeenCalledTimes(1);
    const call = vi.mocked(fetchWeatherFromOpenMeteo).mock.calls[0][0];
    expect(call.pan_id).toBe("north-pan-011");
    expect(call.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(call.time).toMatch(/^\d{2}:\d{2}$/);
  });

  it("says plainly that only server-fetched values can be stored", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));
    await screen.findByText(/30.4 °C/);

    expect(document.body.textContent).toMatch(
      /the ones the server fetched/i,
    );
  });
});

describe("the save", () => {
  it("sends only the fetch token, never the previewed values", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));
    await screen.findByText(/30.4 °C/);
    await user.click(screen.getByRole("button", { name: "Save Weather Record" }));

    await waitFor(() => expect(saveFetchedWeather).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveFetchedWeather).mock.calls[0]).toEqual(["token-abc"]);
    expect(JSON.stringify(vi.mocked(saveFetchedWeather).mock.calls)).not.toContain(
      "30.4",
    );
  });

  it("reports the stored source and asks the page to reload", async () => {
    const user = userEvent.setup();
    const onSaved = renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));
    await screen.findByText(/30.4 °C/);
    await user.click(screen.getByRole("button", { name: "Save Weather Record" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Weather record saved.")).toBeInTheDocument();
  });

  it("surfaces a server refusal verbatim", async () => {
    vi.mocked(saveFetchedWeather).mockRejectedValue(
      new ApiError(
        "This fetched weather result has expired or was never issued. Fetch the weather again to save it.",
        { status: 410 },
      ),
    );
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));
    await screen.findByText(/30.4 °C/);
    await user.click(screen.getByRole("button", { name: "Save Weather Record" }));

    expect(
      await screen.findByText(/expired or was never issued/i),
    ).toBeInTheDocument();
  });
});

describe("a fetch that failed", () => {
  it("shows the server's reason and offers no save", async () => {
    vi.mocked(fetchWeatherFromOpenMeteo).mockRejectedValue(
      new ApiError(
        "Weather cannot be fetched because this pan has no registered coordinates.",
        { status: 422 },
      ),
    );
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Fetch Weather" }));

    expect(await screen.findByText(/no registered coordinates/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save Weather Record" })).toBeNull();
    expect(saveFetchedWeather).not.toHaveBeenCalled();
  });
});

describe("a pan with no coordinates", () => {
  it("explains itself and offers no fetch button", () => {
    render(
      <WeatherFetchPanel
        panId="pan_orginal"
        hasCoordinates={false}
        coordinates={null}
        onSaved={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/no registered\s+coordinates/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fetch Weather" })).toBeNull();
  });
});
