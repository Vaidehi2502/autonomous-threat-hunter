import { SEVERITY_ORDER } from "../severity";

export default function Filters({
  minSeverity,
  onMinSeverityChange,
  search,
  onSearchChange,
  includeDrift,
  onIncludeDriftChange,
  resultCount,
}) {
  return (
    <div className="filters">
      <div className="filter-group">
        <label htmlFor="min-severity">Minimum severity</label>
        <select
          id="min-severity"
          value={minSeverity}
          onChange={(e) => onMinSeverityChange(e.target.value)}
        >
          {SEVERITY_ORDER.slice().reverse().map((sev) => (
            <option key={sev} value={sev}>
              {sev}+
            </option>
          ))}
        </select>
      </div>
      <div className="filter-group filter-group--search">
        <label htmlFor="user-search">Search user ID</label>
        <input
          id="user-search"
          type="text"
          placeholder="e.g. EYD2871"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </div>
      <div
        className="filter-group filter-group--checkbox"
        title="Also show days rated Normal by severity alone, but flagged for a sustained trend"
      >
        <label htmlFor="include-drift">
          <input
            id="include-drift"
            type="checkbox"
            checked={includeDrift}
            onChange={(e) => onIncludeDriftChange(e.target.checked)}
          />
          Show drift-only days
        </label>
      </div>
      <div className="filter-count">
        {resultCount != null ? `${resultCount.toLocaleString()} matching` : ""}
      </div>
    </div>
  );
}
