import { useState } from "react";

export default function ApiKeyGate({ onSubmit, rejected }) {
  const [value, setValue] = useState("");

  return (
    <div className="api-key-gate-backdrop">
      <form
        className="api-key-gate"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(value);
        }}
      >
        <div className="api-key-gate-title">API key required</div>
        <p className="api-key-gate-copy">
          This deployment requires an API key to view employee data. Enter the
          key you were given &mdash; it stays in this browser tab only and is
          never built into the app.
        </p>
        {rejected && <div className="api-key-gate-error">That key was rejected. Try again.</div>}
        <input
          type="password"
          className="api-key-gate-input mono"
          placeholder="X-API-Key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        <button type="submit" className="api-key-gate-submit">
          Continue
        </button>
      </form>
    </div>
  );
}
