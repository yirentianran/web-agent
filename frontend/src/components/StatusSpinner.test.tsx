import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import StatusSpinner, { formatElapsed } from "./StatusSpinner";

describe("formatElapsed", () => {
  it("shows seconds only for small values", () => {
    expect(formatElapsed(0)).toBe("0秒");
    expect(formatElapsed(5)).toBe("5秒");
    expect(formatElapsed(59)).toBe("59秒");
  });

  it("shows minutes and seconds for values over 60s", () => {
    expect(formatElapsed(65)).toBe("1分5秒");
    expect(formatElapsed(90)).toBe("1分30秒");
    expect(formatElapsed(120)).toBe("2分");
    expect(formatElapsed(3599)).toBe("59分59秒");
  });

  it("shows hours, minutes and seconds for values over 3600s", () => {
    expect(formatElapsed(3600)).toBe("1时");
    expect(formatElapsed(3661)).toBe("1时1分1秒");
    expect(formatElapsed(7200)).toBe("2时");
    expect(formatElapsed(3665)).toBe("1时1分5秒");
  });

  it("omits zero-value units", () => {
    expect(formatElapsed(3600)).toBe("1时");
    expect(formatElapsed(60)).toBe("1分");
    expect(formatElapsed(0)).toBe("0秒");
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
    // Agent started 5 seconds ago
    const startTime = fakeNow - 5000;

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const elapsedEl = screen.getByTestId("elapsed");

    // Should show ~5 seconds immediately, NOT "0秒"
    expect(elapsedEl).toHaveTextContent("5秒");
  });

  it("updates elapsed time as time passes", () => {
    const startTime = fakeNow - 10000; // 10 seconds ago

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    // Initially 10 seconds
    const elapsedEl = screen.getByTestId("elapsed");
    expect(elapsedEl).toHaveTextContent("10秒");

    // Advance 5 seconds
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(elapsedEl).toHaveTextContent("15秒");
  });

  it("resets elapsed time correctly when startTime changes", () => {
    const startTime1 = fakeNow - 10000; // 10 seconds ago

    const { rerender } = render(
      <StatusSpinner text="Agent is working" startTime={startTime1} />,
    );

    const elapsedEl = screen.getByTestId("elapsed");
    expect(elapsedEl).toHaveTextContent("10秒");

    // Advance 5 seconds, now 15 seconds elapsed
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(elapsedEl).toHaveTextContent("15秒");

    // New startTime — agent restarted 3 seconds ago (relative to current time)
    const startTime2 = fakeNow + 5000 - 3000;
    act(() => {
      vi.advanceTimersByTime(1); // Small tick to flush state
    });
    rerender(<StatusSpinner text="Agent is working" startTime={startTime2} />);

    // Should show 3 seconds immediately, not 0
    expect(elapsedEl).toHaveTextContent("3秒");
  });

  it("shows stale styling after 30 seconds", () => {
    const startTime = fakeNow - 35000; // 35 seconds ago

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const container = screen.getByText("Agent is working").parentElement!;
    expect(container).toHaveClass("status-spinner--stale");
  });

  it("does not show stale styling for fresh sessions", () => {
    const startTime = fakeNow - 10000; // 10 seconds ago

    render(<StatusSpinner text="Agent is working" startTime={startTime} />);

    const container = screen.getByText("Agent is working").parentElement!;
    expect(container).not.toHaveClass("status-spinner--stale");
  });

  it("does not show elapsed time when startTime is undefined", () => {
    render(<StatusSpinner text="Agent is working" />);

    expect(screen.queryByTestId("elapsed")).not.toBeInTheDocument();
  });
});
