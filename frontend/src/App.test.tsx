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
    const path = new URL(input).pathname;
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
