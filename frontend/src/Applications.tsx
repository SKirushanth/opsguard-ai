import { useState } from "react";
import { z } from "zod";
import { Boxes, Cable } from "lucide-react";
import { api, application, health, errorText, type Application } from "./api";

type Props = {
  apps: Application[];
  operator: boolean;
  select: (id: string) => void;
  reload: () => void;
};
export function Applications({ apps, operator, select, reload }: Props) {
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [remove, setRemove] = useState("");
  async function act(fn: () => Promise<string>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await fn());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel">
        <header>
          <h2>Registered applications</h2>
        </header>
        {apps.map((a) => (
          <div className="appRow" key={a.id}>
            <Boxes />
            <div>
              <h3>{a.name}</h3>
              <p>
                {a.base_url ?? "Simulated services"} · {a.service_ids.length}{" "}
                {a.service_ids.length === 1 ? "service" : "services"}
              </p>
              {a.base_url && (
                <small>
                  Health {a.health_path} · Logs {a.logs_path ?? "unavailable"} ·
                  Metrics {a.metrics_path ?? "unavailable"}
                </small>
              )}
            </div>
            <div className="buttons">
              <button onClick={() => select(a.id)}>Open dashboard</button>
              {a.id !== "demo" && (
                <>
                  <button
                    disabled={!operator || busy}
                    onClick={() =>
                      act(async () => {
                        const h = await api(
                          "/applications/" + a.id + "/validate",
                          health,
                          "POST",
                        );
                        return (
                          a.name +
                          " health endpoint reports " +
                          h.status +
                          " (HTTP " +
                          h.http_status +
                          ")."
                        );
                      })
                    }
                  >
                    Validate
                  </button>
                  <button
                    disabled={!operator || busy}
                    onClick={() => setRemove(a.id)}
                  >
                    Remove
                  </button>
                </>
              )}
              {remove === a.id && (
                <>
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api("/applications/" + a.id, z.null(), "DELETE");
                        setRemove("");
                        reload();
                        return "Application removed.";
                      })
                    }
                  >
                    Confirm removal
                  </button>
                  <button onClick={() => setRemove("")}>Cancel</button>
                </>
              )}
            </div>
          </div>
        ))}
      </section>
      <section className="panel">
        <header>
          <div>
            <h2>
              <Cable />
              Add application
            </h2>
            <p>Connect one service on an operator-approved HTTPS origin.</p>
          </div>
        </header>
        <form
          className="fields"
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const values = Object.fromEntries(new FormData(form));
            void act(async () => {
              const a = await api("/applications", application, "POST", {
                ...values,
                logs_path: values.logs_path || null,
                metrics_path: values.metrics_path || null,
              });
              form.reset();
              reload();
              return (
                a.name +
                " registered. Validate its health endpoint to check compatibility."
              );
            });
          }}
        >
          {(
            [
              ["name", "Application name", "Library API"],
              ["service_id", "Service ID", "library-api"],
              ["base_url", "HTTPS origin", "https://api.example.com"],
              ["health_path", "Health path", "/health"],
              ["logs_path", "Logs path (optional)", "/logs"],
              ["metrics_path", "Metrics path (optional)", "/metrics"],
            ] as const
          ).map(([key, label, example]) => (
            <label key={key}>
              {label}
              <input
                name={key}
                required={!key.match(/^(logs|metrics)/)}
                placeholder={example}
              />
            </label>
          ))}
          <div className="actions">
            <button className="primary" disabled={!operator || busy}>
              Register application
            </button>
            {!operator && <small>Operator sign-in required.</small>}
          </div>
        </form>
      </section>
      {busy && <p role="status">Contacting backend…</p>}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
