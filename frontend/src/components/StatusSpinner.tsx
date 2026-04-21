import { useEffect, useState } from "react";
import "./StatusSpinner.css";

const STALE_THRESHOLD_SEC = 30;

export function formatElapsed(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  const parts: string[] = [];
  if (h > 0) parts.push(`${h}时`);
  if (m > 0) parts.push(`${m}分`);
  if (s > 0 || parts.length === 0) parts.push(`${s}秒`);
  return parts.join("");
}

interface StatusSpinnerProps {
  text?: string;
  detail?: string;
  variant?: "default" | "hook" | "agent";
  startTime?: number;
}

export default function StatusSpinner({
  text,
  detail,
  variant = "default",
  startTime,
}: StatusSpinnerProps) {
  const displayText = text || "Working...";
  const [elapsed, setElapsed] = useState(() =>
    startTime ? Date.now() - startTime : 0,
  );

  useEffect(() => {
    if (!startTime) return;
    setElapsed(Date.now() - startTime);
    const interval = setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 1000);
    return () => clearInterval(interval);
  }, [startTime]);

  const isStale = elapsed > STALE_THRESHOLD_SEC * 1000;
  const elapsedSec = Math.floor(elapsed / 1000);

  return (
    <div
      className={`status-spinner status-spinner--${variant}${isStale ? " status-spinner--stale" : ""}`}
    >
      <div className="status-spinner__dots">
        <span />
        <span />
        <span />
      </div>
      <span className="status-spinner__text">
        {displayText}
        {detail && <strong className="status-spinner__detail">{detail}</strong>}
        {startTime !== undefined && (
          <span
            className="status-spinner__elapsed"
            data-testid="elapsed"
            data-stale={isStale}
          >
            {formatElapsed(elapsedSec)}
          </span>
        )}
      </span>
    </div>
  );
}
