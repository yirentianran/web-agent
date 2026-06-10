import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import "./StatusSpinner.css";

export function formatElapsed(seconds: number, t: TFunction): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  const parts: string[] = [];
  if (h > 0) parts.push(t('spinner.timeHours', { count: h }));
  if (m > 0) parts.push(t('spinner.timeMinutes', { count: m }));
  if (s > 0 || parts.length === 0) parts.push(t('spinner.timeSeconds', { count: s }));
  return parts.join("");
}

interface StatusSpinnerProps {
  text?: string;
  detail?: string;
  variant?: "default" | "hook" | "agent";
  startTime?: number;
  label?: string;
}

export default function StatusSpinner({
  text,
  detail,
  variant = "default",
  startTime,
  label,
}: StatusSpinnerProps) {
  const { t } = useTranslation();
  const displayText = text || t('chat.workingDefault');
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

  const elapsedSec = Math.floor(elapsed / 1000);

  return (
    <div
      className={`status-spinner status-spinner--${variant}`}
    >
      <div className="status-spinner__dots">
        <span />
        <span />
        <span />
      </div>
      <span className="status-spinner__text">
        {label || displayText}
        {detail && <strong className="status-spinner__detail">{detail}</strong>}
        {startTime !== undefined && (
          <span
            className="status-spinner__elapsed"
            data-testid="elapsed"
          >
            {formatElapsed(elapsedSec, t)}
          </span>
        )}
      </span>
    </div>
  );
}