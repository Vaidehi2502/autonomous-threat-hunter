import { severityColor } from "../severity";

export default function SeverityBadge({ severity }) {
  const color = severityColor(severity);
  const glowClass =
    severity === "Critical" ? "severity-badge--pulse" : severity === "High" ? "severity-badge--glow" : "";

  return (
    <span
      className={`severity-badge ${glowClass}`}
      style={{
        color,
        borderColor: color,
        backgroundColor: `${color}1a`,
        "--sev-glow": color,
      }}
    >
      {severity}
    </span>
  );
}
