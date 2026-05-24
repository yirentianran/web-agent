import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import "./ResourcePanel.css";

interface ContainerInfo {
  container: {
    cpu_percent: number;
    memory_mb: number;
    memory_limit_mb: number;
    status: string;
  } | null;
  disk: {
    disk_mb: number;
    status: string;
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
  const { t } = useTranslation();
  const [data, setData] = useState<ResourcesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);
  const token = localStorage.getItem("authToken") || "";

  useEffect(() => {
    const t0 = performance.now();
    console.log('[Dashboard] ResourcePanel fetch start');
    fetch("/api/admin/resources", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        console.log(`[Dashboard] ResourcePanel response ${r.status} at ${(performance.now() - t0).toFixed(0)}ms`);
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d) => {
        console.log(`[Dashboard] ResourcePanel JSON parsed at ${(performance.now() - t0).toFixed(0)}ms`, d);
        if (d.status === "container_mode_disabled") {
          setData(null);
          setError(null);
          setEmptyMessage(t("dashboard.resources.modeDisabled"));
        } else if (d.status === "docker_unavailable") {
          setData(null);
          setError(null);
          setEmptyMessage(t("dashboard.resources.dockerUnavailable"));
        } else if (d.status === "error") {
          setData(null);
          setError(d.detail || "Container mode not available");
        } else {
          setData(d);
        }
        setLoading(false);
      })
      .catch((e: unknown) => {
        console.log(`[Dashboard] ResourcePanel error at ${(performance.now() - t0).toFixed(0)}ms:`, e);
        setError(e instanceof Error ? e.message : "Unknown error");
        setLoading(false);
      });
  }, [token]);

  if (loading) return <div className="resource-loading">{t("dashboard.resources.loading")}</div>;
  if (error) return <div className="resource-empty">{t("dashboard.resources.unavailable", { error })}</div>;
  if (emptyMessage) return <div className="resource-empty">{emptyMessage}</div>;
  if (!data || Object.keys(data).length === 0) {
    return <div className="resource-empty">{t("dashboard.resources.noContainers")}</div>;
  }

  const entries = Object.entries(data);
  const totalCpu = entries.reduce((sum, [, v]) => sum + (v.container?.cpu_percent ?? 0), 0);
  const totalMem = entries.reduce((sum, [, v]) => sum + (v.container?.memory_mb ?? 0), 0);
  const totalDisk = entries.reduce((sum, [, v]) => sum + (v.disk?.disk_mb ?? 0), 0);
  const totalDiskMax = entries.reduce((sum, [, v]) => sum + (v.quota?.details?.limits?.max_disk_mb ?? 0), 0);

  return (
    <div className="resource-panel">
      <h3 className="chart-title">{t("dashboard.resources.title")}</h3>
      <div className="resource-summary">
        <span className="resource-stat">● {t("dashboard.resources.running")}: {entries.length}</span>
        <span className="resource-stat">{t("dashboard.resources.cpu")}: {totalCpu.toFixed(1)}%</span>
        <span className="resource-stat">
          {t("dashboard.resources.mem")}: {(totalMem / 1024).toFixed(1)} GB
        </span>
        <span className="resource-stat">
          {t("dashboard.resources.disk")}: {(totalDisk / 1024).toFixed(1)} / {(totalDiskMax / 1024).toFixed(0)} GB
        </span>
      </div>
      <table className="resource-table">
        <thead>
          <tr>
            <th>{t("dashboard.resources.user")}</th>
            <th>{t("dashboard.resources.container")}</th>
            <th className="right">{t("dashboard.resources.cpu")}</th>
            <th className="right">{t("dashboard.resources.mem")}</th>
            <th className="right">{t("dashboard.resources.disk")}</th>
            <th className="center">{t("dashboard.resources.status")}</th>
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
                  {info.container?.memory_mb != null
                    ? `${(info.container.memory_mb / 1024).toFixed(1)}GB`
                    : "—"}
                </td>
                <td className="right">
                  {info.disk?.disk_mb != null ? `${(info.disk.disk_mb / 1024).toFixed(1)}GB` : "—"}
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
