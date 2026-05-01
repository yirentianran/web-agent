import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import i18n from "../i18n/config";
import StatusSpinner, { formatElapsed } from "./StatusSpinner";

// Get the t function from i18n for use with formatElapsed
const t = i18n.t;

describe("formatElapsed", () => {
  // Default language in test env is English (fallbackLng)
  it("shows seconds only for small values", () => {
    expect(formatElapsed(0, t)).toBe("0s");
    expect(formatElapsed(5, t)).toBe("5s");
    expect(formatElapsed(59, t)).toBe("59s");
  });

  it("shows minutes and seconds for values over 60s", () => {
    expect(formatElapsed(65, t)).toBe("1m5s");
    expect(formatElapsed(90, t)).toBe("1m30s");
    expect(formatElapsed(120, t)).toBe("2m");
    expect(formatElapsed(3599, t)).toBe("59m59s");
  });

  it("shows hours, minutes and seconds for values over 3600s", () => {
    expect(formatElapsed(3600, t)).toBe("1h");
    expect(formatElapsed(3661, t)).toBe("1h1m1s");
    expect(formatElapsed(7200, t)).toBe("2h");
    expect(formatElapsed(3665, t)).toBe("1h1m5s");
  });

  it("omits zero-value units", () => {
    expect(formatElapsed(3600, t)).toBe("1h");
    expect(formatElapsed(60, t)).toBe("1m");
    expect(formatElapsed(0, t)).toBe("0s");
  });

  it("shows Chinese format when language is zh", async () => {
    await i18n.changeLanguage("zh");
    expect(formatElapsed(5, i18n.t)).toBe("5秒");
    expect(formatElapsed(65, i18n.t)).toBe("1分5秒");
    expect(formatElapsed(3661, i18n.t)).toBe("1时1分1秒");
    await i18n.changeLanguage("en"); // reset
  });
});

describe("StatusSpinner component", () => {
  const fakeNow = 1000000;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(fakeNow);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows correct elapsed time immediately on render", () => {
    const startTime = fakeNow - 5000;

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const elapsedEl = screen.getByTestId("elapsed");
    expect(elapsedEl).toHaveTextContent("5s");
  });

  it("updates elapsed time as time passes", () => {
    const startTime = fakeNow - 10000;

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const elapsedEl = screen.getByTestId("elapsed");
    expect(elapsedEl).toHaveTextContent("10s");

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(elapsedEl).toHaveTextContent("15s");
  });

  it("resets elapsed time correctly when startTime changes", () => {
    const startTime1 = fakeNow - 10000;

    const { rerender } = render(
      <StatusSpinner text="Agent is working" startTime={startTime1} />,
    );

    const elapsedEl = screen.getByTestId("elapsed");
    expect(elapsedEl).toHaveTextContent("10s");

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(elapsedEl).toHaveTextContent("15s");

    const startTime2 = fakeNow + 5000 - 3000;
    act(() => {
      vi.advanceTimersByTime(1);
    });
    rerender(<StatusSpinner text="Agent is working" startTime={startTime2} />);

    expect(elapsedEl).toHaveTextContent("3s");
  });

  it("shows stale styling after 30 seconds", () => {
    const startTime = fakeNow - 35000;

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const container = screen.getByText("Agent is working").parentElement!;
    expect(container).toHaveClass("status-spinner--stale");
  });

  it("does not show stale styling for fresh sessions", () => {
    const startTime = fakeNow - 10000;

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const container = screen.getByText("Agent is working").parentElement!;
    expect(container).not.toHaveClass("status-spinner--stale");
  });

  it("does not show elapsed time when startTime is undefined", () => {
    render(<StatusSpinner text="Agent is working" />);

    expect(screen.queryByTestId("elapsed")).not.toBeInTheDocument();
  });
});