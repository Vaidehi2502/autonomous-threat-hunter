import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getUserTimeline } from "../api";
import { severityColor } from "../severity";
import SeverityBadge from "./SeverityBadge";

function CustomTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-date mono">{d.day}</div>
      <div className="chart-tooltip-row">
        <SeverityBadge severity={d.severity} />
        <span className="mono">score {d.severity_score.toFixed(1)}</span>
      </div>
      {d.severity !== "Normal" && <div className="chart-tooltip-reasons">{d.reasons}</div>}
    </div>
  );
}

export default function UserDetail({ userId, onBack, authVersion }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getUserTimeline(userId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        // A 401 is handled by the app-level login gate, not shown here.
        if (!cancelled && err.status !== 401) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId, authVersion]);

  return (
    <div className="user-detail">
      <button className="back-button" onClick={onBack}>
        &larr; Back to threat list
      </button>

      {loading && (
        <div className="panel-state">
          <span className="scanner-ring" />
          Loading timeline for {userId}&hellip;
        </div>
      )}
      {error && <div className="panel-state panel-state--error">Failed to load timeline: {error}</div>}

      {data && (
        <>
          <div className="user-detail-header">
            <div>
              <h2 className="mono">{data.user}</h2>
              <div className="user-meta">
                {data.role} &middot; {data.department} &middot; {data.team}
              </div>
            </div>
            <div className="user-detail-stats">
              <div className="mini-stat">
                <div className="mini-stat-value">{data.total_days}</div>
                <div className="mini-stat-label">days tracked</div>
              </div>
              <div className="mini-stat">
                <div className="mini-stat-value mini-stat-value--flagged">{data.flagged_days}</div>
                <div className="mini-stat-label">flagged days</div>
              </div>
              {["Critical", "High", "Medium", "Low"].map((sev) => (
                <div className="mini-stat" key={sev}>
                  <div className="mini-stat-value" style={{ color: severityColor(sev) }}>
                    {data.severity_breakdown[sev] || 0}
                  </div>
                  <div className="mini-stat-label">{sev.toLowerCase()}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="chart-card">
            <div className="chart-title">Daily severity score over time</div>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={data.timeline} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#232b36" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "#7c8798", fontSize: 11 }}
                  tickFormatter={(d) => d.slice(5)}
                  interval={Math.max(0, Math.floor(data.timeline.length / 12))}
                  axisLine={{ stroke: "#2a323d" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#7c8798", fontSize: 11 }}
                  axisLine={{ stroke: "#2a323d" }}
                  tickLine={false}
                  width={28}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                <Bar dataKey="severity_score" radius={[2, 2, 0, 0]}>
                  {data.timeline.map((entry) => (
                    <Cell
                      key={entry.day}
                      fill={severityColor(entry.severity)}
                      opacity={entry.severity === "Normal" ? 0.35 : 1}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {data.timeline.some((d) => d.drift_flag) && (
            <div className="chart-card">
              <div className="chart-title">
                Behavioral drift &mdash; sustained trend, not a single-day spike
              </div>
              <div className="drift-strip">
                {data.timeline.map((d) => (
                  <div
                    key={d.day}
                    className={`drift-tick${d.drift_flag ? " drift-tick--active" : ""}`}
                    title={d.drift_flag ? `${d.day} — ${d.drift_reasons}` : d.day}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="reasons-panel">
            <div className="chart-title">Flagged days &mdash; reasons</div>
            {data.timeline
              .filter((d) => d.severity !== "Normal" || d.drift_flag)
              .slice()
              .reverse()
              .map((d, i) => (
                <div
                  className="reason-row"
                  key={d.day}
                  style={{
                    "--sev-color": severityColor(d.severity),
                    "--row-delay": `${Math.min(i, 20) * 20}ms`,
                  }}
                >
                  <SeverityBadge severity={d.severity} />
                  {d.drift_flag && <span className="drift-badge">Trend</span>}
                  <span className="mono reason-date">{d.day}</span>
                  <span className="mono reason-score">{d.severity_score.toFixed(1)}</span>
                  <span className="reason-text">
                    {d.severity !== "Normal" && d.drift_flag
                      ? `${d.reasons}; ${d.drift_reasons}`
                      : d.severity !== "Normal"
                      ? d.reasons
                      : d.drift_reasons}
                  </span>
                </div>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
