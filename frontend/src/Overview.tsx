import {
  Activity,
  Network,
  CircleCheck,
  CircleHelp,
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
  demo: boolean;
  busy: boolean;
};

export function Overview(p: Props) {
  const stats = [
    {
      label: "Monitored services",
      icon: Network,
      value: p.services.length,
      kind: "total",
    },
    ...[
      { label: "Healthy", icon: CircleCheck, kind: "healthy" },
      { label: "Degraded", icon: TriangleAlert, kind: "degraded" },
      { label: "Down", icon: CircleX, kind: "down" },
      { label: "Unknown", icon: CircleHelp, kind: "unknown" },
    ].map((s) => ({
      ...s,
      value: p.services.filter((v) => v.status === s.kind).length,
    })),
  ];
  const entries = p.services
    .flatMap((s) =>
      (s.logs.data?.logs ?? []).map((l) => ({ ...l, name: s.name, id: s.id })),
    )
    .sort(
      (a, b) =>
        (b.timestamp ? Date.parse(b.timestamp) : 0) -
        (a.timestamp ? Date.parse(a.timestamp) : 0),
    )
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
          {(p.operator || p.demo) && p.app.id === "demo" && (
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
              {p.services.map((s) => {
                const avgLatency = s.metrics.data?.metrics.avg_latency_ms;
                const errorRate = s.metrics.data?.metrics.error_rate_percent;
                return (
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
                    <td className={avgLatency != null ? undefined : "unavailable"}>
                      {avgLatency != null
                        ? avgLatency.toLocaleString() + " ms"
                        : "Unavailable"}
                    </td>
                    <td className={errorRate != null ? undefined : "unavailable"}>
                      {errorRate != null ? errorRate + "%" : "Unavailable"}
                    </td>
                    <td>{s.checkedAt ? clock(s.checkedAt) : "—"}</td>
                    <td>
                      <button className="quiet" disabled={p.demo} title={p.demo ? "AI investigations require operator sign-in" : undefined} onClick={() => p.investigate(s)}>
                        <ScanSearch />
                        Investigate
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <header>
          <div>
            <h2>Recent logs</h2>
            <p>Latest available entries across this application.</p>
          </div>
          <Activity size={20} />
        </header>
        {entries.map((l, i) => (
          <div className="log" key={i}>
            <time title={l.timestamp ?? undefined}>
              {l.timestamp ? clock(l.timestamp) : "—"}
            </time>
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
    h = s.health.data,
    m = s.metrics.data?.metrics;
  return (
    <>
      <section className="panel">
        <header>
          <div>
            <Status value={s.status} />
            <h2>{s.name}</h2>
            <p>{h?.explanation ?? h?.message ?? s.health.error}</p>
          </div>
          <button className="primary" disabled={p.demo} title={p.demo ? "AI investigations require operator sign-in" : undefined} onClick={() => p.investigate(s)}>
            <ScanSearch />
            Investigate
          </button>
        </header>
        {h?.requires_mapping && (
          <p className="notice" role="status">
            Health response format is unrecognized. Configure a custom response
            mapping on the Applications page.
          </p>
        )}
        <dl className="facts">
          <div>
            <dt>Service ID</dt>
            <dd>{s.id}</dd>
          </div>
          <div>
            <dt>Health response</dt>
            <dd>
              {h?.http_status != null ? "HTTP " + h.http_status : "Unavailable"}
            </dd>
          </div>
          {h?.normalization_method && (
            <div>
              <dt>Normalization</dt>
              <dd>{h.normalization_method}</dd>
            </div>
          )}
          <div>
            <dt>Last checked</dt>
            <dd>{s.checkedAt ? clock(s.checkedAt) : "Unavailable"}</dd>
          </div>
        </dl>
        {h?.evidence !== undefined && h?.evidence !== null && (
          <div className="evidenceBox">
            <small>Sanitized health evidence</small>
            <pre>
              {typeof h.evidence === "string"
                ? h.evidence
                : JSON.stringify(h.evidence, null, 2)}
            </pre>
          </div>
        )}
      </section>
      <section className="panel">
        <header>
          <h2>Performance metrics</h2>
        </header>
        {m && s.metrics.data?.available !== false ? (
          <dl className="facts">
            <div>
              <dt>Average latency</dt>
              <dd>
                {m.avg_latency_ms != null
                  ? m.avg_latency_ms + " ms"
                  : "Unavailable"}
              </dd>
            </div>
            <div>
              <dt>Error rate</dt>
              <dd>
                {m.error_rate_percent != null
                  ? m.error_rate_percent + "%"
                  : "Unavailable"}
              </dd>
            </div>
            <div>
              <dt>Requests / 5 min</dt>
              <dd>
                {m.requests_last_5m != null
                  ? m.requests_last_5m
                  : "Unavailable"}
              </dd>
            </div>
            {m.active_db_connections != null && (
              <div>
                <dt>Active connections</dt>
                <dd>
                  {m.active_db_connections} / {m.db_connection_limit ?? "—"}
                </dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="empty">
            {s.metrics.error ||
              s.metrics.data?.explanation ||
              "Metrics unavailable."}
          </p>
        )}
      </section>
      <section className="panel">
        <header>
          <h2>Service logs</h2>
        </header>
        {s.logs.data?.logs.map((l, i) => (
          <div className="log" key={i}>
            <time title={l.timestamp ?? undefined}>
              {l.timestamp ? clock(l.timestamp) : "—"}
            </time>
            <b className={"level " + l.level}>{l.level}</b>
            <span>{l.message}</span>
          </div>
        ))}
        {!s.logs.data?.logs.length && s.logs.data?.raw_evidence != null && (
          <div className="evidenceBox">
            <small>Sanitized log evidence</small>
            <pre>
              {typeof s.logs.data.raw_evidence === "string"
                ? s.logs.data.raw_evidence
                : JSON.stringify(s.logs.data.raw_evidence, null, 2)}
            </pre>
          </div>
        )}
        {!s.logs.data?.logs.length && s.logs.data?.raw_evidence == null && (
          <p className="empty">{s.logs.error || "No recent entries."}</p>
        )}
      </section>
      {p.app.id === "demo" && (
        <section className="panel">
          <header>
            <div>
              <h2>Failure simulation</h2>
              <p>
                {p.demo ? "Changes apply only to your demo session." : p.operator
                  ? "Scenario changes affect the shared demo."
                  : "Sign in as operator to change the demo."}
              </p>
            </div>
          </header>
          <div className="actions">
            <button
              disabled={(!p.operator && !p.demo) || p.busy}
              onClick={() => p.simulate(s.id, failures[s.id])}
            >
              <FlaskConical />
              Simulate failure
            </button>
            <button
              disabled={(!p.operator && !p.demo) || p.busy}
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
