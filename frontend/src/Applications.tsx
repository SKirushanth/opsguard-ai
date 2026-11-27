import { useState } from "react";
import { z } from "zod";
import { Boxes, Cable, FlaskConical, SlidersHorizontal } from "lucide-react";
import {
  api,
  application,
  connectionTestResult,
  health,
  errorText,
  type Application,
  type ConnectionTestResult,
  type CustomMapping,
} from "./api";
import { Status } from "./Overview";

type Props = {
  apps: Application[];
  operator: boolean;
  select: (id: string) => void;
  reload: () => void;
};

type MappingDraft = {
  status_field: string;
  healthy_values: string;
  down_values: string;
  degraded_values: string;
};

const EMPTY_MAPPING: MappingDraft = {
  status_field: "",
  healthy_values: "",
  down_values: "",
  degraded_values: "",
};

function parseMappingValues(raw: string): (string | boolean)[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      if (part.toLowerCase() === "true") return true;
      if (part.toLowerCase() === "false") return false;
      if (
        (part.startsWith('"') && part.endsWith('"')) ||
        (part.startsWith("'") && part.endsWith("'"))
      ) {
        return part.slice(1, -1);
      }
      return part;
    });
}

function buildMappingPayload(draft: MappingDraft): CustomMapping {
  return {
    status_field: draft.status_field.trim(),
    healthy_values: parseMappingValues(draft.healthy_values),
    down_values: parseMappingValues(draft.down_values),
    degraded_values: parseMappingValues(draft.degraded_values),
  };
}

function formatMappingValues(values: (string | boolean)[]): string {
  return values
    .map((v) =>
      typeof v === "boolean"
        ? String(v)
        : v.toLowerCase() === "true" || v.toLowerCase() === "false"
          ? `"${v}"`
          : v,
    )
    .join(", ");
}

export function Applications({ apps, operator, select, reload }: Props) {
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [remove, setRemove] = useState("");

  const [name, setName] = useState(""),
    [serviceId, setServiceId] = useState(""),
    [baseUrl, setBaseUrl] = useState(""),
    [healthPath, setHealthPath] = useState(""),
    [logsPath, setLogsPath] = useState(""),
    [metricsPath, setMetricsPath] = useState("");

  const [mappingMode, setMappingMode] = useState<"auto" | "custom">("auto");
  const [mapping, setMapping] = useState<MappingDraft>(EMPTY_MAPPING);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [previewResult, setPreviewResult] = useState<{
    status: string;
    explanation: string;
  } | null>(null);

  const [editingAppId, setEditingAppId] = useState(""),
    [editMapping, setEditMapping] = useState<MappingDraft>(EMPTY_MAPPING);

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

  async function runConnectionTest() {
    setBusy(true);
    setError("");
    setNotice("");
    setPreviewResult(null);
    try {
      const payload = {
        base_url: baseUrl.trim(),
        service_id: serviceId.trim() || "probe",
        health_path: healthPath.trim(),
        logs_path: logsPath.trim() || null,
        metrics_path: metricsPath.trim() || null,
        health_mapping:
          mappingMode === "custom" ? buildMappingPayload(mapping) : null,
      };
      const result = await api(
        "/applications/test-connection",
        connectionTestResult,
        "POST",
        payload,
      );
      setTestResult(result);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function previewCustomMapping() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api(
        "/applications/validate-mapping",
        health,
        "POST",
        {
          mapping: buildMappingPayload(mapping),
          sample_response: testResult?.sanitized_response ?? null,
          http_status: testResult?.http_status ?? 200,
        },
      );
      setPreviewResult({
        status: result.status,
        explanation: result.explanation ?? result.message,
      });
    } catch (e) {
      setPreviewResult(null);
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
              <h3>
                {a.name}{" "}
                {a.id !== "demo" && <Status value={a.last_status ?? "unknown"} />}
              </h3>
              <p>
                {a.base_url ?? "Simulated services"} · {a.service_ids.length}{" "}
                {a.service_ids.length === 1 ? "service" : "services"}
              </p>
              {a.base_url && (
                <small>
                  Health {a.health_path} · Logs {a.logs_path ?? "unavailable"} ·
                  Metrics {a.metrics_path ?? "unavailable"}
                  {a.health_mapping
                    ? ` · Mapping: ${a.health_mapping.status_field}`
                    : " · Detection: automatic"}
                </small>
              )}
              {editingAppId === a.id && (
                <div className="mappingEditor">
                  <label>
                    Status field path
                    <input
                      value={editMapping.status_field}
                      onChange={(e) =>
                        setEditMapping((m) => ({ ...m, status_field: e.target.value }))
                      }
                      placeholder="application.currentState"
                    />
                  </label>
                  <label>
                    Healthy values
                    <input
                      value={editMapping.healthy_values}
                      onChange={(e) =>
                        setEditMapping((m) => ({ ...m, healthy_values: e.target.value }))
                      }
                      placeholder="ACTIVE, true"
                    />
                  </label>
                  <label>
                    Degraded values
                    <input
                      value={editMapping.degraded_values}
                      onChange={(e) =>
                        setEditMapping((m) => ({
                          ...m,
                          degraded_values: e.target.value,
                        }))
                      }
                      placeholder="LIMITED"
                    />
                  </label>
                  <label>
                    Down values
                    <input
                      value={editMapping.down_values}
                      onChange={(e) =>
                        setEditMapping((m) => ({ ...m, down_values: e.target.value }))
                      }
                      placeholder="INACTIVE, false"
                    />
                  </label>
                  <div className="actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await api(
                            "/applications/" + a.id + "/mapping",
                            application,
                            "PUT",
                            { health_mapping: buildMappingPayload(editMapping) },
                          );
                          setEditingAppId("");
                          reload();
                          return "Custom mapping updated for " + a.name + ".";
                        })
                      }
                    >
                      Save mapping
                    </button>
                    {a.health_mapping && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            await api(
                              "/applications/" + a.id + "/mapping",
                              application,
                              "PUT",
                              { health_mapping: null },
                            );
                            setEditingAppId("");
                            reload();
                            return "Switched " + a.name + " back to automatic detection.";
                          })
                        }
                      >
                        Use automatic detection
                      </button>
                    )}
                    <button type="button" onClick={() => setEditingAppId("")}>
                      Cancel
                    </button>
                  </div>
                </div>
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
                        reload();
                        return (
                          a.name +
                          " health endpoint reports " +
                          h.status +
                          (h.http_status != null ? " (HTTP " + h.http_status + ")." : ".")
                        );
                      })
                    }
                  >
                    Validate
                  </button>
                  <button
                    disabled={!operator || busy}
                    onClick={() => {
                      setEditingAppId(a.id);
                      setEditMapping(
                        a.health_mapping
                          ? {
                              status_field: a.health_mapping.status_field,
                              healthy_values: formatMappingValues(
                                a.health_mapping.healthy_values,
                              ),
                              down_values: formatMappingValues(
                                a.health_mapping.down_values,
                              ),
                              degraded_values: formatMappingValues(
                                a.health_mapping.degraded_values,
                              ),
                            }
                          : EMPTY_MAPPING,
                      );
                    }}
                  >
                    <SlidersHorizontal />
                    Mapping
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
            void act(async () => {
              const a = await api("/applications", application, "POST", {
                name: name.trim(),
                service_id: serviceId.trim(),
                base_url: baseUrl.trim(),
                health_path: healthPath.trim(),
                logs_path: logsPath.trim() || null,
                metrics_path: metricsPath.trim() || null,
                health_mapping:
                  mappingMode === "custom" ? buildMappingPayload(mapping) : null,
              });
              setName("");
              setServiceId("");
              setBaseUrl("");
              setHealthPath("");
              setLogsPath("");
              setMetricsPath("");
              setMappingMode("auto");
              setMapping(EMPTY_MAPPING);
              setTestResult(null);
              setPreviewResult(null);
              reload();
              return (
                a.name +
                " registered. Open its dashboard or validate its health endpoint."
              );
            });
          }}
        >
          <label>
            Application name
            <input
              name="name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Library API"
            />
          </label>
          <label>
            Service ID
            <input
              name="service_id"
              required
              value={serviceId}
              onChange={(e) => setServiceId(e.target.value)}
              placeholder="library-api"
            />
          </label>
          <label>
            Backend URL
            <input
              name="base_url"
              required
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com"
            />
          </label>
          <label>
            Health endpoint
            <input
              name="health_path"
              required
              value={healthPath}
              onChange={(e) => setHealthPath(e.target.value)}
              placeholder="/health"
            />
          </label>
          <label>
            Logs endpoint (optional)
            <input
              name="logs_path"
              value={logsPath}
              onChange={(e) => setLogsPath(e.target.value)}
              placeholder="/logs"
            />
          </label>
          <label>
            Metrics endpoint (optional)
            <input
              name="metrics_path"
              value={metricsPath}
              onChange={(e) => setMetricsPath(e.target.value)}
              placeholder="/metrics"
            />
          </label>
          <div className="mappingChoice">
            <span className="fieldHeading">Response normalization</span>
            <div className="radioRow">
              <label className="inlineChoice">
                <input
                  type="radio"
                  name="mapping_mode"
                  checked={mappingMode === "auto"}
                  onChange={() => {
                    setMappingMode("auto");
                    setPreviewResult(null);
                  }}
                />
                Automatic response detection
              </label>
              <label className="inlineChoice">
                <input
                  type="radio"
                  name="mapping_mode"
                  checked={mappingMode === "custom"}
                  onChange={() => setMappingMode("custom")}
                />
                Custom response mapping
              </label>
            </div>
          </div>

          {mappingMode === "custom" && (
            <div className="mappingPanel">
              <label>
                Status field path
                <input
                  name="status_field"
                  required
                  value={mapping.status_field}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, status_field: e.target.value }))
                  }
                  placeholder="application.currentState"
                />
              </label>
              <label>
                Healthy values
                <input
                  name="healthy_values"
                  value={mapping.healthy_values}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, healthy_values: e.target.value }))
                  }
                  placeholder="ACTIVE, true"
                />
              </label>
              <label>
                Degraded values
                <input
                  name="degraded_values"
                  value={mapping.degraded_values}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, degraded_values: e.target.value }))
                  }
                  placeholder="LIMITED"
                />
              </label>
              <label>
                Down values
                <input
                  name="down_values"
                  value={mapping.down_values}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, down_values: e.target.value }))
                  }
                  placeholder="INACTIVE, false"
                />
              </label>
              <div className="actions">
                <button
                  type="button"
                  disabled={!operator || busy || !mapping.status_field.trim()}
                  onClick={() => void previewCustomMapping()}
                >
                  Preview & validate mapping
                </button>
                <small>
                  Comma-separated strings or booleans (true/false). Quote{" "}
                  <code>&quot;true&quot;</code> for literal text.
                </small>
              </div>
              {previewResult && (
                <div className="notice" role="status">
                  Preview result: <Status value={previewResult.status} />{" "}
                  {previewResult.explanation}
                </div>
              )}
            </div>
          )}

          <div className="actions">
            <button
              type="button"
              disabled={!operator || busy || !baseUrl.trim() || !healthPath.trim()}
              onClick={() => void runConnectionTest()}
            >
              <FlaskConical />
              Test Connection
            </button>
            <button className="primary" disabled={!operator || busy}>
              Register application
            </button>
            {!operator && <small>Operator sign-in required.</small>}
          </div>
        </form>

        {testResult && (
          <div className="connectionPreview" role="region" aria-label="Connection test results">
            <h3>Connection test result</h3>
            <dl className="facts">
              <div>
                <dt>Connection result</dt>
                <dd>{testResult.connection_result}</dd>
              </div>
              <div>
                <dt>Actual HTTP status</dt>
                <dd>
                  {testResult.http_status != null
                    ? "HTTP " + testResult.http_status
                    : "Unavailable"}
                </dd>
              </div>
              <div>
                <dt>Normalized health status</dt>
                <dd>
                  <Status value={testResult.normalized_status} />
                </dd>
              </div>
              <div>
                <dt>Normalization method</dt>
                <dd>{testResult.normalization_method}</dd>
              </div>
            </dl>
            <p className="explanationLine">{testResult.explanation}</p>
            {testResult.requires_mapping && (
              <div className="notice" role="status">
                <strong>Custom response mapping required:</strong>{" "}
                {testResult.reason ?? "Unrecognized health response format."}
                {mappingMode !== "custom" && (
                  <button
                    type="button"
                    onClick={() => setMappingMode("custom")}
                  >
                    Configure custom response mapping
                  </button>
                )}
              </div>
            )}
            {testResult.sanitized_response !== undefined &&
              testResult.sanitized_response !== null && (
                <div className="evidenceBox">
                  <small>Sanitized original response</small>
                  <pre>
                    {typeof testResult.sanitized_response === "string"
                      ? testResult.sanitized_response
                      : JSON.stringify(testResult.sanitized_response, null, 2)}
                  </pre>
                </div>
              )}
          </div>
        )}
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
