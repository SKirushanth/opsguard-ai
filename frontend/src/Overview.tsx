import {
  Activity,
  Boxes,
  CircleCheck,
  TriangleAlert,
  CircleX,
  ScanSearch,
  RotateCcw,
  HeartPulse,
  FlaskConical,
} from "lucide-react";
import type { Application, Service } from "./api";

export const clock = (date: string) =>
  new Date(date).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
export function Status({ value }: { value: string }) {
  return (
    <span className={"badge " + value}>
      <i />
      {value}
    </span>
  );
}
const failures: Record<string, string> = {
  auth: "token_validation_failure",
  orders: "database_pool_exhausted",
  notifications: "smtp_timeout",
};
type Props = {
  app: Application;
  services: Service[];
  investigate: (s: Service) => void;
  details: (s: Service) => void;
  simulate: (id: string, scenario?: string) => void;
  operator: boolean;
  busy: boolean;
};

export function Overview(p: Props) {
  const stats = [
    {
      label: "Monitored services",
      icon: Boxes,
      value: p.services.length,
      kind: "total",
    },
    ...[
      { label: "Healthy", icon: CircleCheck, kind: "healthy" },
      { label: "Degraded", icon: TriangleAlert, kind: "degraded" },
      { label: "Down", icon: CircleX, kind: "down" },
    ].map((s) => ({
      ...s,
      value: p.services.filter((v) => v.status === s.kind).length,
    })),
  ];
  const entries = p.services
    .flatMap((s) =>
      (s.logs.data?.logs ?? []).map((l) => ({ ...l, name: s.name, id: s.id })),
    )
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
    .slice(0, 5);
  return (
    <>
      <div className="stats">
        {stats.map(({ label, icon: Icon, value, kind }) => (
          <div className={"stat " + kind} key={kind}>
            <span>
              <Icon size={17} />
              {label}
            </span>
            <strong>{value.toString().padStart(2, "0")}</strong>
          </div>
        ))}
      </div>
      <section className="panel">
        <header>
          <div>
            <h2>Service health</h2>
            <p>
              {p.app.name} · {p.services.length} monitored
            </p>
          </div>
          {p.operator && p.app.id === "demo" && (
            <button disabled={p.busy} onClick={() => p.simulate("")}>
              <RotateCcw />
              Reset all to healthy
            </button>
          )}
        </header>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                {[
                  "Service",
                  "Status",
                  "Avg. latency",
                  "Error rate",
                  "Last checked",
                  "",
                ].map((k) => (
                  <th key={k}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {p.services.map((s) => (
                <tr key={s.id}>
                  <td>
                    <button className="service" onClick={() => p.details(s)}>
                      <span className="serviceIcon">
                        <HeartPulse />
                      </span>
                      {s.name}
                    </button>
                  </td>
                  <td>
                    <Status value={s.status} />
                  </td>
                  <td>
                    {s.metrics.data
                      ? s.metrics.data.metrics.avg_latency_ms.toLocaleString() +
                        " ms"
                      : "Unavailable"}
                  </td>
                  <td>
                    {s.metrics.data
                      ? s.metrics.data.metrics.error_rate_percent + "%"
                      : "Unavailable"}
                  </td>
                  <td>{s.checkedAt ? clock(s.checkedAt) : "—"}</td>
                  <td>
                    <button className="quiet" onClick={() => p.investigate(s)}>
                      <ScanSearch />
                      Investigate
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <header>
          <div>
            <h2>Recent logs</h2>
            <p>Latest available entries across this application</p>
          </div>
          <Activity size={20} />
        </header>
        {entries.map((l, i) => (
          <div className="log" key={i}>
            <time title={l.timestamp}>{clock(l.timestamp)}</time>
            <b className={"level " + l.level}>{l.level}</b>
            <span>{l.message}</span>
            <small>{l.name}</small>
          </div>
        ))}
        {!entries.length && <p className="empty">No log entries available.</p>}
        {p.services
          .filter((s) => s.logs.error)
          .map((s) => (
            <p className="empty" key={s.id}>
              {s.name} · {s.logs.error}
            </p>
          ))}
      </section>
    </>
  );
}

export function Details(p: Props & { service: Service }) {
  const s = p.service,
    m = s.metrics.data?.metrics;
  return (
    <>
      <section className="panel">
        <header>
          <div>
            <Status value={s.status} />
            <h2>{s.name}</h2>
            <p>{s.health.data?.message ?? s.health.error}</p>
          </div>
          <button className="primary" onClick={() => p.investigate(s)}>
            <ScanSearch />
            Investigate
          </button>
        </header>
        <dl className="facts">
          <div>
            <dt>Service ID</dt>
            <dd>{s.id}</dd>
          </div>
          <div>
            <dt>Health response</dt>
            <dd>
              {s.health.data
                ? "HTTP " + s.health.data.http_status
                : "Unavailable"}
            </dd>
          </div>
          <div>
            <dt>Last checked</dt>
            <dd>{s.checkedAt ? clock(s.checkedAt) : "Unavailable"}</dd>
          </div>
        </dl>
      </section>
      <section className="panel">
        <header>
          <h2>Performance metrics</h2>
        </header>
        {m ? (
          <dl className="facts">
            <div>
              <dt>Average latency</dt>
              <dd>{m.avg_latency_ms} ms</dd>
            </div>
            <div>
              <dt>Error rate</dt>
              <dd>{m.error_rate_percent}%</dd>
            </div>
            <div>
              <dt>Requests / 5 min</dt>
              <dd>{m.requests_last_5m}</dd>
            </div>
            {m.active_db_connections !== undefined && (
              <div>
                <dt>Active connections</dt>
                <dd>
                  {m.active_db_connections} / {m.db_connection_limit ?? "—"}
                </dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="empty">{s.metrics.error}</p>
        )}
      </section>
      <section className="panel">
        <header>
          <h2>Service logs</h2>
        </header>
        {s.logs.data?.logs.map((l, i) => (
          <div className="log" key={i}>
            <time>{clock(l.timestamp)}</time>
            <b className={"level " + l.level}>{l.level}</b>
            <span>{l.message}</span>
          </div>
        ))}
        {!s.logs.data?.logs.length && (
          <p className="empty">{s.logs.error || "No recent entries."}</p>
        )}
      </section>
      {p.app.id === "demo" && (
        <section className="panel">
          <header>
            <div>
              <h2>Failure simulation</h2>
              <p>
                {p.operator
                  ? "Scenario changes affect the shared demo."
                  : "Sign in as operator to change the demo."}
              </p>
            </div>
          </header>
          <div className="actions">
            <button
              disabled={!p.operator || p.busy}
              onClick={() => p.simulate(s.id, failures[s.id])}
            >
              <FlaskConical />
              Simulate failure
            </button>
            <button
              disabled={!p.operator || p.busy}
              onClick={() => p.simulate(s.id)}
            >
              <RotateCcw />
              Recover service
            </button>
          </div>
        </section>
      )}
    </>
  );
}
