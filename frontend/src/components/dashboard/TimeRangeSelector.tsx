import { useState } from "react";
import "./TimeRangeSelector.css";

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

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

const PRESETS: { key: TimePreset; label: string; from: () => Date; to: () => Date }[] = [
  { key: "today", label: "Today", from: today, to: today },
  { key: "7d", label: "7 Days", from: () => daysAgo(7), to: today },
  { key: "30d", label: "30 Days", from: () => daysAgo(30), to: today },
];

export default function TimeRangeSelector({ from, to, onChange }: TimeRangeSelectorProps) {
  const [activePreset, setActivePreset] = useState<TimePreset>("30d");
  const [showCustom, setShowCustom] = useState(false);
  const [customFrom, setCustomFrom] = useState(from);
  const [customTo, setCustomTo] = useState(to);

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
          onClick={() => setShowCustom(!showCustom)}
        >
          Custom
        </button>
      </div>
      {showCustom && (
        <div className="time-range-custom">
          <label>
            From:
            <input
              type="date"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </label>
          <label>
            To:
            <input
              type="date"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </label>
          <button className="time-range-btn apply" onClick={applyCustom}>
            Apply
          </button>
        </div>
      )}
    </div>
  );
}
