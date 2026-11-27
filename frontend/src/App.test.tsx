// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import App from "./App";
import { setToken } from "./api";

afterEach(() => {
  cleanup();
  setToken("");
  vi.unstubAllGlobals();
});
const ok = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
const demo = {
  id: "demo",
  name: "Built-in demo",
  service_ids: ["orders"],
  base_url: null,
  health_path: null,
  metrics_path: null,
  logs_path: null,
};
const report = {
  application_id: "demo",
  service_id: "orders",
  title: "Order database timeout",
  description: "Requests fail.",
  severity: "high",
  evidence: ["HTTP 503"],
  likely_root_cause: "Connection pool exhaustion",
  recommendations: ["Inspect active connections"],
};

it("shows the loading state while the backend is pending", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  render(<App />);
  expect(screen.getByRole("status").textContent).toMatch(
    /Loading monitoring data/,
  );
});
it.each([
  ["network", () => Promise.reject(new Error("offline")), "Could not reach"],
  ["invalid JSON", () => Promise.resolve(ok({})), "invalid response"],
])("shows a retry for %s", async (_case, fetcher, message) => {
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  expect((await screen.findByRole("alert")).textContent).toContain(message);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
});
it("investigates, reviews a draft, and saves only after explicit confirmation", async () => {
  const writes: unknown[] = [];
  const saved = {
    ...report,
    id: "incident-123",
    created_at: "2026-11-24T10:00:00Z",
    resolved_at: null,
    status: "open",
  };
  const fetcher = vi.fn(async (input: string, init: RequestInit) => {
    const path = new URL(input).pathname.replace("/public-demo/", "/");
    if (path === "/api/session")
      return ok({ token: "session-test", expires_in: 3600 });
    if (path === "/api/applications") return ok([demo]);
    if (path === "/api/services")
      return ok({
        services: [{ id: "orders", name: "Order Service", status: "down" }],
      });
    if (path.endsWith("/health"))
      return ok({
        service_id: "orders",
        status: "down",
        http_status: 503,
        message: "Pool exhausted",
      });
    if (path.endsWith("/logs")) return ok({ service_id: "orders", logs: [] });
    if (path.endsWith("/metrics"))
      return ok({
        service_id: "orders",
        metrics: {
          avg_latency_ms: 2400,
          error_rate_percent: 42,
          requests_last_5m: 120,
        },
      });
    if (path === "/api/chat")
      return ok({ response: "Observed **HTTP 503**.", session_id: "chat" });
    if (path.endsWith("/draft")) return ok(report);
    if (path === "/api/incidents" && init.method === "POST") {
      writes.push(JSON.parse(String(init.body)));
      return ok(saved, 201);
    }
    if (path === "/api/incidents") return ok([saved]);
    throw Error("Unexpected route " + path);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  expect(await screen.findByText("2,400 ms")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Operator sign-in" }));
  fireEvent.change(screen.getByLabelText("Operator password"), {
    target: { value: "test" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Sign in$/ }));
  await screen.findByText("Signed in as workspace operator.");
  fireEvent.click(screen.getByRole("button", { name: /^Investigate$/ }));
  fireEvent.click(
    screen.getByRole("button", { name: "Why is Order Service failing?" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Prepare incident draft" }),
  );
  expect(
    await screen.findByRole("heading", { name: "Review incident draft" }),
  ).toBeTruthy();
  expect(writes).toHaveLength(0);
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm incident creation" }),
  );
  expect(await screen.findByText(/Saved incident incident-123/)).toBeTruthy();
  expect(writes).toMatchObject([
    { confirmed: true, service_id: "orders", approval_id: expect.any(String) },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "View history" }));
  expect(
    await within(
      screen.getByText("Incident history").closest("section")!,
    ).findByText("Order database timeout", { selector: "strong" }),
  ).toBeTruthy();
});

it("supports unknown status on dashboard and custom mapping workflow in Applications", async () => {
  const customApp = {
    id: "ext-abc",
    name: "ERP API",
    service_ids: ["ext-abc-erp"],
    base_url: "https://monitor.example.com",
    health_path: "/health",
    logs_path: null,
    metrics_path: null,
    last_status: "unknown",
    health_mapping: null,
  };
  let appsList = [demo, customApp];
  const registeredPayloads: unknown[] = [];

  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const path = new URL(input).pathname.replace("/public-demo/", "/");
    if (path === "/api/session")
      return ok({ token: "session-test", expires_in: 3600 });
    if (path === "/api/applications" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      registeredPayloads.push(body);
      const created = {
        ...customApp,
        id: "ext-new",
        name: body.name,
        service_ids: ["ext-new-" + body.service_id],
        health_mapping: body.health_mapping ?? null,
        last_status: "unknown",
      };
      appsList = [...appsList, created];
      return ok(created, 201);
    }
    if (path === "/api/applications/test-connection") {
      return ok({
        connected: true,
        connection_result: "Connected (HTTP 200)",
        http_status: 200,
        sanitized_response: { application: { currentState: "ACTIVE" } },
        normalized_status: "unknown",
        normalization_method: "unrecognized",
        explanation:
          "Automatic detection did not find a recognized health indicator in the HTTP 200 response.",
        reason: "Unrecognized health response format",
        requires_mapping: true,
        error_category: "unrecognized_format",
        checked_at: "2026-11-26T10:00:00Z",
      });
    }
    if (path === "/api/applications/validate-mapping") {
      return ok({
        service_id: "preview",
        status: "healthy",
        http_status: 200,
        message: "Health endpoint reports 'ACTIVE' (mapped to healthy).",
        normalization_method: "custom_mapping",
        explanation:
          "Matched custom field 'application.currentState' value 'ACTIVE' as 'healthy'.",
        requires_mapping: false,
      });
    }
    if (path === "/api/applications") return ok(appsList);
    if (path === "/api/services")
      return ok({
        services: [
          { id: "orders", name: "Order Service", status: "unknown" },
          { id: "ext-abc-erp", name: "ERP API", status: "unknown" },
        ],
      });
    if (path.endsWith("/health"))
      return ok({
        service_id: "orders",
        status: "unknown",
        http_status: 200,
        message: "Unrecognized health response format",
        reason: "Unrecognized health response format",
        requires_mapping: true,
        normalization_method: "unrecognized",
        explanation: "Automatic detection failed.",
        last_checked_at: "2026-11-26T10:00:00Z",
      });
    if (path.endsWith("/logs")) return ok({ service_id: "orders", logs: [] });
    if (path.endsWith("/metrics"))
      return ok({
        service_id: "orders",
        metrics: {},
        available: false,
        explanation: "Metrics unavailable.",
      });
    throw Error("Unexpected route " + path);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  // Unknown count & Unavailable metrics appear cleanly on dashboard
  expect(await screen.findByText("Unknown")).toBeTruthy();
  expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(2);

  // Sign in and navigate to Applications
  fireEvent.click(screen.getByRole("button", { name: "Operator sign-in" }));
  fireEvent.change(screen.getByLabelText("Operator password"), {
    target: { value: "test" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Sign in$/ }));
  await screen.findByText("Signed in as workspace operator.");

  fireEvent.click(screen.getByRole("button", { name: "Applications" }));

  // Custom mapping fields are hidden by default
  expect(screen.queryByPlaceholderText("application.currentState")).toBeNull();

  // Fill Add Application and run Test Connection
  fireEvent.change(screen.getByLabelText("Application name"), {
    target: { value: "Billing API" },
  });
  fireEvent.change(screen.getByLabelText("Service ID"), {
    target: { value: "billing-api" },
  });
  fireEvent.change(screen.getByLabelText("Backend URL"), {
    target: { value: "https://monitor.example.com" },
  });
  fireEvent.change(screen.getByLabelText("Health endpoint"), {
    target: { value: "/health" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));

  expect(await screen.findByText("Connected (HTTP 200)")).toBeTruthy();
  expect(screen.getByText(/Custom response mapping required/i)).toBeTruthy();

  // Reveal custom mapping, preview, and register
  fireEvent.click(
    screen.getByRole("button", { name: "Configure custom response mapping" }),
  );
  fireEvent.change(screen.getByLabelText("Status field path"), {
    target: { value: "application.currentState" },
  });
  fireEvent.change(screen.getByLabelText("Healthy values"), {
    target: { value: "ACTIVE" },
  });
  fireEvent.change(screen.getByLabelText("Down values"), {
    target: { value: "INACTIVE" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Preview & validate mapping" }),
  );
  expect(await screen.findByText(/Preview result:/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Register application" }));
  expect(await screen.findByText(/Billing API registered/)).toBeTruthy();
  expect(registeredPayloads).toMatchObject([
    {
      name: "Billing API",
      service_id: "billing-api",
      health_mapping: {
        status_field: "application.currentState",
        healthy_values: ["ACTIVE"],
        down_values: ["INACTIVE"],
        degraded_values: [],
      },
    },
  ]);
});

it("opens a private demo from sign-in and returns to operator sign-in", async () => {
  const fetcher = vi.fn(async (input: string, init: RequestInit) => {
    const path = new URL(input).pathname;
    if (path === "/api/public-demo/session") return ok({token: "visitor", expires_in: 1800});
    if (!path.startsWith("/api/public-demo/")) return ok({detail: "Sign in"}, 401);
    expect(init.headers).toMatchObject({Authorization: "Bearer visitor"});
    if (path.endsWith("/applications")) return ok([demo]);
    if (path.endsWith("/services")) return ok({services: [{id: "orders", name: "Order Service", status: "down"}]});
    if (path.endsWith("/health")) return ok({service_id:"orders", status:"down", http_status:503, message:"Pool exhausted"});
    if (path.endsWith("/logs")) return ok({service_id:"orders", logs:[]});
    if (path.endsWith("/metrics")) return ok({service_id:"orders", metrics:{}});
    throw Error("Unexpected demo request");
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findAllByText("Demo Mode");
  expect(fetcher.mock.calls.every(([input]) => new URL(input).pathname.startsWith("/api/public-demo/"))).toBe(true);
  await screen.findByText("Order Service");
  expect(screen.queryByRole("button", {name:"Applications"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Operator sign-in"}));
  expect(screen.getByLabelText("Operator password")).toBeTruthy();
});
