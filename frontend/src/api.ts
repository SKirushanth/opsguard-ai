import { z } from "zod";

export const base = (
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8001"
).replace(/\/$/, "");
let token = "";
let demoSession = false;
export function setToken(value: string, demo = false) {
  token = value;
  demoSession = demo;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public category?: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const sentToken = token;
  let response: Response;
  try {
    response = await fetch(base + (demoSession && path !== "/session" ? "/api/public-demo" : "/api") + path, {
      method,
      headers: {
        ...(body !== undefined && { "Content-Type": "application/json" }),
        ...(token && { Authorization: "Bearer " + token }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(75000),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Could not reach the monitoring backend. Check the connection and retry.",
    );
  }
  const data =
    response.status === 204
      ? null
      : await response.json().catch(() => undefined);
  if (response.status === 401 && sentToken && sentToken === token && path !== "/session") {
    window.dispatchEvent(new Event("workspace-session-ended"));
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e) => e.loc.slice(1).join(".") + " " + e.msg).join(", ")
          : typeof detail?.message === "string" ? detail.message
            : "Request failed (HTTP " + response.status + ").",
      typeof detail?.category === "string" ? detail.category : undefined,
    );
  }
  const parsed = schema.safeParse(data);
  if (!parsed.success)
    throw new ApiError(502, "The backend returned an invalid response.", "validation");
  return parsed.data;
}

const status = z.enum(["healthy", "degraded", "down", "unknown"]);
const mappingValue = z.union([z.string(), z.boolean()]);
export const customMapping = z.object({
  status_field: z.string(),
  healthy_values: z.array(mappingValue),
  down_values: z.array(mappingValue),
  degraded_values: z.array(mappingValue),
});
export type CustomMapping = z.infer<typeof customMapping>;

export const catalog = z.object({
  services: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      status,
    }),
  ),
});
export const health = z.object({
  service_id: z.string(),
  status,
  http_status: z.number().nullable(),
  message: z.string(),
  reason: z.string().nullable().optional(),
  requires_mapping: z.boolean().optional(),
  normalization_method: z.string().optional(),
  explanation: z.string().nullable().optional(),
  evidence: z.unknown().optional(),
  last_checked_at: z.string().nullable().optional(),
  error_category: z.string().nullable().optional(),
});
export const logs = z.object({
  service_id: z.string(),
  logs: z.array(
    z.object({
      timestamp: z.iso.datetime({ offset: true }).nullable().optional(),
      level: z.string(),
      message: z.string(),
    }),
  ),
  format: z.enum(["json", "text", "unrecognized"]).nullable().optional(),
  available: z.boolean().nullable().optional(),
  raw_evidence: z.unknown().optional(),
  explanation: z.string().nullable().optional(),
});
export const metrics = z.object({
  service_id: z.string(),
  metrics: z.object({
    avg_latency_ms: z.number().nullable().optional(),
    error_rate_percent: z.number().nullable().optional(),
    requests_last_5m: z.number().nullable().optional(),
    active_db_connections: z.number().nullable().optional(),
    db_connection_limit: z.number().nullable().optional(),
  }),
  available: z.boolean().nullable().optional(),
  unavailable_fields: z.array(z.string()).nullable().optional(),
  unrecognized_fields: z.array(z.string()).nullable().optional(),
  raw_evidence: z.unknown().optional(),
  explanation: z.string().nullable().optional(),
});
export const application = z.object({
  id: z.string(),
  name: z.string(),
  service_ids: z.array(z.string()),
  base_url: z.string().nullable(),
  health_path: z.string().nullable(),
  logs_path: z.string().nullable(),
  metrics_path: z.string().nullable(),
  last_status: status.optional(),
  health_mapping: customMapping.nullable().optional(),
});
export const applications = z.array(application);
export type Application = z.infer<typeof application>;

export const connectionTestResult = z.object({
  connected: z.boolean(),
  connection_result: z.string(),
  http_status: z.number().nullable(),
  sanitized_response: z.unknown().nullable().optional(),
  normalized_status: status,
  normalization_method: z.string(),
  explanation: z.string(),
  reason: z.string().nullable().optional(),
  requires_mapping: z.boolean(),
  error_category: z.string().nullable().optional(),
  checked_at: z.string(),
});
export type ConnectionTestResult = z.infer<typeof connectionTestResult>;

export const draft = z.object({
  application_id: z.string(),
  service_id: z.string(),
  title: z.string(),
  description: z.string(),
  severity: z.enum(["low", "medium", "high", "critical"]),
  evidence: z.array(z.string()).min(1),
  likely_root_cause: z.string(),
  recommendations: z.array(z.string()).min(1),
});
export type Draft = z.infer<typeof draft>;
export const incident = draft.extend({
  id: z.string(),
  status: z.enum(["open", "resolved"]),
  created_at: z.string(),
  resolved_at: z.string().nullable(),
});
export type Incident = z.infer<typeof incident>;
export const chatReply = z.object({
  mode: z.enum(["demo", "live", "monitoring"]).optional(),
  evidence: z.array(z.object({ reference: z.string(), text: z.string() })).optional(),
  response: z.string().min(1),
  session_id: z.string(),
});
export function errorText(e: unknown) {
  return e instanceof Error ? e.message : "Request failed.";
}

async function resource<T>(path: string, schema: z.ZodType<T>) {
  try {
    return { data: await api(path, schema), error: "" };
  } catch (e) {
    return { data: undefined, error: errorText(e) };
  }
}

export async function readService(
  service: z.infer<typeof catalog>["services"][number],
) {
  const path = "/services/" + encodeURIComponent(service.id);
  const [h, l, m] = await Promise.all([
    resource(path + "/health", health),
    resource(path + "/logs", logs),
    resource(path + "/metrics", metrics),
  ]);
  return {
    ...service,
    status: h.data?.status ?? ("unknown" as const),
    health: h,
    logs: l,
    metrics: m,
    checkedAt: h.data?.last_checked_at ?? (h.data ? new Date().toISOString() : null),
  };
}
export type Service = Awaited<ReturnType<typeof readService>>;
