import SeverityBadge from "./SeverityBadge";
import { severityColor } from "../severity";

export default function ThreatTable({ threats, onSelectUser, loading, error }) {
  if (loading) {
    return (
      <div className="panel-state">
        <span className="scanner-ring" />
        Scanning threats&hellip;
      </div>
    );
  }
  if (error) {
    return <div className="panel-state panel-state--error">Failed to load threats: {error}</div>;
  }
  if (!threats.length) {
    return <div className="panel-state">No flagged user-days match the current filters.</div>;
  }

  return (
    <div className="table-wrap">
      <table className="threat-table">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Score</th>
            <th>User</th>
            <th>Date</th>
            <th>Role</th>
            <th>Department</th>
            <th>Reasons</th>
          </tr>
        </thead>
        <tbody>
          {threats.map((t, i) => {
            const reasonsText = t.drift_flag
              ? t.reasons === "No specific rule triggered"
                ? t.drift_reasons
                : `${t.reasons}; ${t.drift_reasons}`
              : t.reasons;

            return (
              <tr
                key={`${t.user}-${t.day}`}
                className="threat-row"
                onClick={() => onSelectUser(t.user)}
                style={{
                  "--sev-color": severityColor(t.severity),
                  "--row-delay": `${Math.min(i, 24) * 18}ms`,
                }}
              >
                <td>
                  <SeverityBadge severity={t.severity} />
                  {t.drift_flag && (
                    <span
                      className="drift-badge"
                      title="Rated Normal by severity alone, but caught by a sustained trend"
                    >
                      Trend
                    </span>
                  )}
                </td>
                <td className="mono score-cell">{t.severity_score.toFixed(1)}</td>
                <td className="mono user-cell">{t.user}</td>
                <td className="mono">{t.day}</td>
                <td>{t.role}</td>
                <td>{t.department}</td>
                <td className="reasons-cell" title={reasonsText}>
                  {reasonsText}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
