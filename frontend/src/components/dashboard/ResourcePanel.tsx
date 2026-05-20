import { useState, useEffect } from "react";
import "./ResourcePanel.css";

interface ContainerInfo {
  container: {
    cpu_percent: number;
    memory_usage_mb: number;
    status: string;
  } | null;
  disk: {
    used_gb: number;
    total_gb: number;
  } | null;
  quota: Record<string, any> | null;
}

interface ResourcesData {
  [userId: string]: ContainerInfo;
}

function containerStatus(info: ContainerInfo): "normal" | "high-load" | "idle" {
  const cpu = info.container?.cpu_percent ?? 0;
  if (cpu > 80) return "high-load";
  if (cpu < 0.5) return "idle";
  return "normal";
}

function statusDot(status: string): string {
  if (status === "high-load") return "⚠";
  if (status === "idle") return "○";
  return "●";
}

export default function ResourcePanel() {
  const [data, setData] = useState<ResourcesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const token = localStorage.getItem("authToken") || "";

  useEffect(() => {
    fetch("/api/admin/resources", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d) => {
        if (d.status === "container_mode_disabled" || d.status === "error") {
          setData(null);
          setError(d.detail || "Container mode not available");
        } else {
          setData(d);
        }
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Unknown error");
        setLoading(false);
      });
  }, [token]);

  if (loading) return <div className="resource-loading">Loading resources...</div>;
  if (error) return <div className="resource-empty">Resources unavailable: {error}</div>;
  if (!data || Object.keys(data).length === 0) {
    return <div className="resource-empty">No running containers</div>;
  }

  const entries = Object.entries(data);
  const totalCpu = entries.reduce((sum, [, v]) => sum + (v.container?.cpu_percent ?? 0), 0);
  const totalMem = entries.reduce((sum, [, v]) => sum + (v.container?.memory_usage_mb ?? 0), 0);
  const totalDisk = entries.reduce((sum, [, v]) => sum + (v.disk?.used_gb ?? 0), 0);
  const totalDiskMax = entries.reduce((sum, [, v]) => sum + (v.disk?.total_gb ?? 0), 0);

  return (
    <div className="resource-panel">
      <h3 className="chart-title">Container Resources</h3>
      <div className="resource-summary">
        <span className="resource-stat">● Running: {entries.length}</span>
        <span className="resource-stat">CPU: {totalCpu.toFixed(1)}%</span>
        <span className="resource-stat">
          Mem: {(totalMem / 1024).toFixed(1)} GB
        </span>
        <span className="resource-stat">
          Disk: {totalDisk.toFixed(1)} / {totalDiskMax.toFixed(0)} GB
        </span>
      </div>
      <table className="resource-table">
        <thead>
          <tr>
            <th>User</th>
            <th>Container</th>
            <th className="right">CPU</th>
            <th className="right">Mem</th>
            <th className="right">Disk</th>
            <th className="center">St</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([userId, info]) => {
            const st = containerStatus(info);
            return (
              <tr key={userId}>
                <td>{userId}</td>
                <td className="mono">web-agent-{userId}</td>
                <td className="right">{info.container?.cpu_percent?.toFixed(1) ?? "—"}%</td>
                <td className="right">
                  {info.container?.memory_usage_mb != null
                    ? `${info.container.memory_usage_mb.toFixed(0)}MB`
                    : "—"}
                </td>
                <td className="right">
                  {info.disk ? `${info.disk.used_gb.toFixed(1)}GB` : "—"}
                </td>
                <td className={`center status-${st}`}>{statusDot(st)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
