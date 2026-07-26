import { SEVERITY_ORDER, severityColor } from "../severity";
import AnimatedNumber from "./AnimatedNumber";

export default function StatBar({ stats }) {
  if (!stats) return null;

  const { total_users, total_user_days, total_flagged, drift_flagged, severity_breakdown, date_range } = stats;

  return (
    <div className="stat-bar">
      <div className="stat-card stat-card--primary" style={{ "--accent": "#4c9be8" }}>
        <div className="stat-value">
          <AnimatedNumber value={total_users} />
        </div>
        <div className="stat-label">Users Monitored</div>
      </div>
      <div className="stat-card" style={{ "--accent": "#7c8798" }}>
        <div className="stat-value">
          <AnimatedNumber value={total_user_days} />
        </div>
        <div className="stat-label">User-Days Analyzed</div>
      </div>
      <div className="stat-card stat-card--flagged" style={{ "--accent": "#f5943a" }}>
        <div className="stat-value">
          <AnimatedNumber value={total_flagged} />
        </div>
        <div className="stat-label">Flagged Events</div>
      </div>
      <div
        className="stat-card"
        style={{ "--accent": "#a78bfa" }}
        title="Rated Normal by severity alone, but caught by a sustained trend a single day's score can't see"
      >
        <div className="stat-value" style={{ color: "#a78bfa" }}>
          <AnimatedNumber value={drift_flagged ?? 0} />
        </div>
        <div className="stat-label">Drift Detected</div>
      </div>
      {SEVERITY_ORDER.slice().reverse().map((sev) => (
        <div
          className="stat-card stat-card--severity"
          key={sev}
          style={{ "--accent": severityColor(sev) }}
        >
          <div className="stat-value" style={{ color: severityColor(sev) }}>
            <AnimatedNumber value={severity_breakdown[sev] || 0} />
          </div>
          <div className="stat-label">{sev}</div>
        </div>
      ))}
      <div className="stat-card stat-card--range" style={{ "--accent": "#3ddc84" }}>
        <div className="stat-value stat-value--small">
          {date_range.start} &rarr; {date_range.end}
        </div>
        <div className="stat-label">Monitoring Window</div>
      </div>
    </div>
  );
}
