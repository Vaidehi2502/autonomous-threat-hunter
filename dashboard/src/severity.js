export const SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"];

export const SEVERITY_COLORS = {
  Critical: "#f0435e",
  High: "#f5943a",
  Medium: "#e8c547",
  Low: "#4c9be8",
  Normal: "#5b6472",
};

export function severityColor(severity) {
  return SEVERITY_COLORS[severity] || SEVERITY_COLORS.Normal;
}
