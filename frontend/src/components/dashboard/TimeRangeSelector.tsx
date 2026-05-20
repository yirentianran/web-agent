import { useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDate, formatDatetime } from "../../lib/dates";
import "./TimeRangeSelector.css";

function today(): Date {
  return new Date();
}

function daysAgo(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

export type TimePreset = "today" | "7d" | "30d" | "custom";

interface TimeRangeSelectorProps {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}

export default function TimeRangeSelector({ from, to, onChange }: TimeRangeSelectorProps) {
  const { t } = useTranslation();
  const [activePreset, setActivePreset] = useState<TimePreset>("30d");
  const [showCustom, setShowCustom] = useState(false);
  const [customFrom, setCustomFrom] = useState(from);
  const [customTo, setCustomTo] = useState(to);

  const PRESETS: { key: TimePreset; label: string; from: () => Date; to: () => Date }[] = [
    { key: "today", label: t("dashboard.time.today"), from: today, to: today },
    { key: "7d", label: t("dashboard.time.7days"), from: () => daysAgo(7), to: today },
    { key: "30d", label: t("dashboard.time.30days"), from: () => daysAgo(30), to: today },
  ];

  function applyPreset(preset: TimePreset) {
    setActivePreset(preset);
    setShowCustom(false);
    const presetDef = PRESETS.find((p) => p.key === preset);
    if (presetDef) {
      const newFrom = formatDate(presetDef.from());
      const newTo = formatDate(presetDef.to());
      onChange(newFrom, newTo);
    }
  }

  function handleCustomClick() {
    const opening = !showCustom;
    setShowCustom(opening);
    if (opening) {
      setCustomFrom(formatDatetime(new Date(from.includes('T') ? from : `${from}T00:00`)));
      setCustomTo(formatDatetime(new Date(to.includes('T') ? to : `${to}T23:59`)));
    }
  }

  function applyCustom() {
    if (customFrom && customTo) {
      setActivePreset("custom");
      onChange(customFrom, customTo);
    }
  }

  return (
    <div className="time-range-selector">
      <div className="time-range-presets">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            className={`time-range-btn ${activePreset === p.key ? "active" : ""}`}
            onClick={() => applyPreset(p.key)}
          >
            {p.label}
          </button>
        ))}
        <button
          className={`time-range-btn ${activePreset === "custom" ? "active" : ""}`}
          onClick={handleCustomClick}
        >
          {t("dashboard.time.custom")}
        </button>
      </div>
      {showCustom && (
        <div className="time-range-custom">
          <label>
            {t("dashboard.time.from")}
            <input
              type="datetime-local"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </label>
          <label>
            {t("dashboard.time.to")}
            <input
              type="datetime-local"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </label>
          <button className="time-range-btn apply" onClick={applyCustom}>
            {t("dashboard.time.apply")}
          </button>
        </div>
      )}
    </div>
  );
}
