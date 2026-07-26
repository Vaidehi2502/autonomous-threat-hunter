import { useEffect, useMemo, useState } from "react";
import StatBar from "./components/StatBar";
import Filters from "./components/Filters";
import ThreatTable from "./components/ThreatTable";
import UserDetail from "./components/UserDetail";
import ApiKeyGate from "./components/ApiKeyGate";
import { API_BASE, getStats, getThreats, getApiKey, setApiKey, onAuthRequired } from "./api";

export default function App() {
  const [stats, setStats] = useState(null);
  const [statsError, setStatsError] = useState(null);

  const [minSeverity, setMinSeverity] = useState("Medium");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [includeDrift, setIncludeDrift] = useState(false);

  const [threats, setThreats] = useState([]);
  const [totalMatching, setTotalMatching] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [selectedUser, setSelectedUser] = useState(null);

  // Bumped whenever the analyst supplies a (possibly new) API key, to retrigger
  // every fetch below. needsKey/keyRejected drive the login gate; they're set
  // from onAuthRequired so it fires no matter which request hit a 401 first.
  const [authVersion, setAuthVersion] = useState(0);
  const [needsKey, setNeedsKey] = useState(false);
  const [keyRejected, setKeyRejected] = useState(false);

  useEffect(() => {
    return onAuthRequired(() => {
      setKeyRejected(Boolean(getApiKey()));
      setNeedsKey(true);
    });
  }, []);

  function handleKeySubmit(value) {
    setApiKey(value);
    setNeedsKey(false);
    setKeyRejected(false);
    setAuthVersion((v) => v + 1);
  }

  useEffect(() => {
    getStats()
      .then((res) => {
        setStats(res);
        setStatsError(null);
      })
      .catch((err) => {
        if (err.status !== 401) setStatsError(err.message);
      });
  }, [authVersion]);

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(handle);
  }, [search]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getThreats({ minSeverity, includeDrift, user: debouncedSearch, limit: 500 })
      .then((res) => {
        setThreats(res.results);
        setTotalMatching(res.total_matching);
      })
      .catch((err) => {
        if (err.status !== 401) setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [minSeverity, includeDrift, debouncedSearch, authVersion]);

  const subtitle = useMemo(() => {
    if (totalMatching == null) return "";
    if (totalMatching > threats.length) {
      return `Showing top ${threats.length.toLocaleString()} of ${totalMatching.toLocaleString()} by severity score`;
    }
    return `${totalMatching.toLocaleString()} result${totalMatching === 1 ? "" : "s"}`;
  }, [totalMatching, threats.length]);

  return (
    <div className="app">
      {needsKey && <ApiKeyGate onSubmit={handleKeySubmit} rejected={keyRejected} />}
      <header className="app-header">
        <div className="app-title">
          <span className="live-dot" aria-hidden="true" />
          Autonomous Threat Hunter
        </div>
        <div className="app-subtitle">Insider Threat Detection &mdash; SOC Analyst Console</div>
        {getApiKey() && (
          <button
            className="api-key-change-button"
            onClick={() => {
              setApiKey("");
              setNeedsKey(true);
              setKeyRejected(false);
            }}
          >
            Change API key
          </button>
        )}
      </header>

      {statsError && (
        <div className="panel-state panel-state--error">
          Could not reach the API at {API_BASE} ({statsError}). If it is hosted on a
          free tier it may be waking from sleep &mdash; retry in a few seconds.
        </div>
      )}
      <StatBar stats={stats} />

      {selectedUser ? (
        <div className="view-fade" key={`detail-${selectedUser}`}>
          <UserDetail userId={selectedUser} onBack={() => setSelectedUser(null)} authVersion={authVersion} />
        </div>
      ) : (
        <div className="view-fade" key="table">
          <Filters
            minSeverity={minSeverity}
            onMinSeverityChange={setMinSeverity}
            search={search}
            onSearchChange={setSearch}
            includeDrift={includeDrift}
            onIncludeDriftChange={setIncludeDrift}
            resultCount={totalMatching}
          />
          <div className="result-subtitle">{subtitle}</div>
          <ThreatTable
            threats={threats}
            onSelectUser={setSelectedUser}
            loading={loading}
            error={error}
          />
        </div>
      )}
    </div>
  );
}
