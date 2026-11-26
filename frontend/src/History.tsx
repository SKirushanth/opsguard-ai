import { useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import { api, incident, errorText, type Incident } from "./api";
import { ReportBody } from "./Investigator";
import { Status } from "./Overview";

export function History({
  operator,
  names,
}: {
  operator: boolean;
  names: Record<string, string>;
}) {
  const [rows, setRows] = useState<Incident[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0),
    [saving, setSaving] = useState("");
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    api("/incidents", incident.array())
      .then((r) => {
        if (current) setRows(r);
      })
      .catch((e) => {
        if (current) setError(errorText(e));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [revision]);
  async function change(row: Incident) {
    setSaving(row.id);
    setError("");
    try {
      const result = await api("/incidents/" + row.id, incident, "PATCH", {
        status: row.status === "open" ? "resolved" : "open",
      });
      setRows((old) => old.map((i) => (i.id === result.id ? result : i)));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving("");
    }
  }
  return (
    <section className="panel">
      <header>
        <div>
          <h2>Incident history</h2>
          <p>Approved reports across all applications</p>
        </div>
        <button onClick={() => setRevision((v) => v + 1)}>Refresh</button>
      </header>
      {loading ? (
        <p className="empty" role="status">
          Loading reports…
        </p>
      ) : error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : !rows.length ? (
        <div className="empty">
          <ClipboardList />
          <h3>No incidents recorded</h3>
          <p>Confirmed investigation reports will appear here.</p>
        </div>
      ) : null}
      {!loading &&
        rows.map((r) => (
          <details key={r.id}>
            <summary>
              <strong>{r.title}</strong>
              <span className={"badge " + r.severity}>{r.severity}</span>
              <Status value={r.status} />
              <small>
                {names[r.service_id] ?? r.service_id} ·{" "}
                {new Date(r.created_at).toLocaleString()}
              </small>
            </summary>
            <ReportBody report={r} />
            <div className="actions">
              <small>
                ID {r.id}
                {r.resolved_at &&
                  " · Resolved " + new Date(r.resolved_at).toLocaleString()}
              </small>
              <button
                disabled={!operator || !!saving}
                onClick={() => change(r)}
              >
                {r.status === "open" ? "Resolve incident" : "Reopen incident"}
              </button>
            </div>
          </details>
        ))}
    </section>
  );
}
