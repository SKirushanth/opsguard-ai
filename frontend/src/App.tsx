import { useEffect, useState } from "react";
import { z } from "zod";
import {
  Activity,
  Boxes,
  ChevronRight,
  CircleUserRound,
  ClipboardList,
  Layers3,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ScanSearch,
  Server,
  ShieldCheck,
} from "lucide-react";
import {
  api,
  applications,
  catalog,
  errorText,
  readService,
  setToken,
  type Application,
  type Service,
} from "./api";
import { Overview, Details } from "./Overview";
import { Investigator } from "./Investigator";
import { History } from "./History";
import { Applications } from "./Applications";

const navigation = [
  { name: "Dashboard", icon: LayoutDashboard },
  { name: "Investigator", icon: ScanSearch },
  { name: "Service details", icon: Server },
  { name: "Incidents", icon: ClipboardList },
  { name: "Applications", icon: Layers3 },
] as const;
type Page = (typeof navigation)[number]["name"];

export default function App() {
  const [page, setPage] = useState<Page>("Dashboard"),
    [appId, setAppId] = useState("demo"),
    [serviceId, setServiceId] = useState("");
  const [apps, setApps] = useState<Application[]>([]),
    [services, setServices] = useState<Service[]>([]),
    [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState(""),
    [revision, setRevision] = useState(0);
  const [expiry, setExpiry] = useState(0),
    [showLogin, setShowLogin] = useState(false),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const operator = expiry > 0,
    app = apps.find((a) => a.id === appId),
    service = services.find((s) => s.id === serviceId) ?? services[0];
  useEffect(() => {
    if (!expiry) return;
    const timeout = setTimeout(
      () => {
        setToken("");
        setExpiry(0);
        setApps([]);
        setServices([]);
        setNotice("Operator session expired. Sign in again.");
      },
      Math.max(0, expiry - Date.now()),
    );
    return () => clearTimeout(timeout);
  }, [expiry]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadError("");
    async function load() {
      const [list, all] = await Promise.all([
        api("/applications", applications),
        api("/services", catalog),
      ]);
      if (!current) return;
      setApps(list);
      setNames(Object.fromEntries(all.services.map((s) => [s.id, s.name])));
      const selected = list.find((a) => a.id === appId);
      if (!selected) {
        setAppId("demo");
        return;
      }
      const snapshots = await Promise.all(
        all.services
          .filter((s) => selected.service_ids.includes(s.id))
          .map(readService),
      );
      if (current) setServices(snapshots);
    }
    load()
      .catch((e) => {
        if (current) {
          setLoadError(errorText(e));
          setServices([]);
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [appId, revision, expiry]);
  const refresh = () => setRevision((v) => v + 1);
  function navigate(p: Page) {
    setPage(p);
    setError("");
    setNotice("");
  }
  function selectApp(id: string) {
    setServices([]);
    setServiceId("");
    setAppId(id);
    navigate("Dashboard");
    refresh();
  }
  function openService(s: Service, p: Page) {
    setServiceId(s.id);
    navigate(p);
  }
  async function simulate(id: string, scenario?: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (scenario)
        await api(
          "/demo/scenario",
          z.object({ service_id: z.string(), status: z.string() }),
          "POST",
          { service_id: id, scenario },
        );
      else
        await api(
          "/demo/reset",
          z.object({
            service_ids: z.array(z.string()),
            status: z.literal("healthy"),
          }),
          "POST",
          id ? { service_id: id } : {},
        );
      setNotice(
        scenario ? "Failure scenario activated." : "Demo recovery completed.",
      );
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function login(password: string) {
    setBusy(true);
    setError("");
    try {
      const result = await api(
        "/session",
        z.object({ token: z.string(), expires_in: z.number() }),
        "POST",
        { password },
      );
      setToken(result.token);
      setExpiry(Date.now() + result.expires_in * 1000);
      setShowLogin(false);
      setNotice("Signed in as workspace operator.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    try {
      await api("/session", z.null(), "DELETE");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setToken("");
      setExpiry(0);
      setApps([]);
      setServices([]);
      setNames({});
      setBusy(false);
      setPage("Dashboard");
    }
  }
  const props = app
    ? {
        app,
        services,
        operator,
        busy: busy || loading,
        investigate: (s: Service) => openService(s, "Investigator"),
        details: (s: Service) => openService(s, "Service details"),
        simulate,
      }
    : null;
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span>
            <Activity />
          </span>
          OpsGuard <em>AI</em>
        </div>
        <div className="navLabel">Workspace</div>
        <nav aria-label="Workspace navigation">
          {navigation.map(({ name, icon: Icon }) => (
            <button
              key={name}
              aria-current={name === page ? "page" : undefined}
              onClick={() => navigate(name)}
            >
              <Icon />
              {name}
            </button>
          ))}
        </nav>
        <div className="identity">
          <CircleUserRound />
          <div>
            <strong>{operator ? "Operator" : "Guest access"}</strong>
            <small>
              {operator ? "Workspace session" : "Read-only session"}
            </small>
          </div>
        </div>
      </aside>
      <main>
        <div className="topbar">
          <div>
            <span>Workspace</span>
            <ChevronRight />
            {page}
          </div>
          <button
            disabled={busy}
            onClick={() => (operator ? void logout() : setShowLogin((v) => !v))}
          >
            {operator ? <LogOut /> : <ShieldCheck />}
            {operator ? "Sign out" : "Operator sign-in"}
          </button>
        </div>
        <div className="content">
          {showLogin && (
            <form
              className="login"
              onSubmit={(e) => {
                e.preventDefault();
                const form = e.currentTarget;
                const password = String(new FormData(form).get("password"));
                form.reset();
                void login(password);
              }}
            >
              <label>
                Operator password
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  required
                  autoFocus
                />
              </label>
              <button className="primary" disabled={busy}>
                Sign in
              </button>
              <button type="button" onClick={() => setShowLogin(false)}>
                Cancel
              </button>
            </form>
          )}
          <header className="pageHead">
            <div>
              <h1>
                {page === "Dashboard"
                  ? "Monitoring overview"
                  : page === "Investigator"
                    ? "AI Investigator"
                    : page}
              </h1>
              <p>
                {page === "Dashboard"
                  ? "Service health and performance across your application."
                  : page === "Incidents"
                    ? "Review and track confirmed incident reports."
                    : page === "Applications"
                      ? "Manage monitored services and endpoint connections."
                      : page === "Investigator"
                        ? "Investigate service behavior with monitoring evidence."
                        : "Inspect the latest evidence for a service."}
              </p>
            </div>
            <button
              onClick={refresh}
              disabled={loading}
              aria-label="Refresh monitoring data"
            >
              <RefreshCw />
              Refresh
            </button>
          </header>
          {notice && (
            <p role="status" className="notice">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {loadError && (
            <p role="alert" className="error">
              {loadError} <button onClick={refresh}>Retry</button>
            </p>
          )}
          {loading && (
            <p className="muted" role="status">
              Loading monitoring data…
            </p>
          )}
          {!["Applications", "Incidents"].includes(page) && app && (
            <div className="contextBar">
              <label>
                <Boxes />
                Application
                <select
                  aria-label="Selected application"
                  value={appId}
                  onChange={(e) => selectApp(e.target.value)}
                >
                  {apps.map((a) => (
                    <option value={a.id} key={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <span className="source">
                {app.id === "demo" ? "Simulated data" : "External API"}
              </span>
              {page !== "Dashboard" && service && (
                <label>
                  Service
                  <select
                    value={service.id}
                    onChange={(e) => setServiceId(e.target.value)}
                  >
                    {services.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          )}
          {page === "Dashboard" &&
            props &&
            !loadError &&
            (!loading || services.length > 0) && <Overview {...props} />}
          {page === "Service details" && props && service && (
            <Details {...props} service={service} />
          )}
          {app && service && (
            <div hidden={page !== "Investigator"}>
              <Investigator
                key={app.id + service.id + expiry}
                app={app}
                service={service}
                operator={operator}
                openHistory={() => navigate("Incidents")}
              />
            </div>
          )}
          {page === "Applications" && (
            <Applications
              apps={apps}
              operator={operator}
              select={selectApp}
              reload={refresh}
            />
          )}
          {page === "Incidents" && (
            <History
              key={expiry + "-" + revision}
              operator={operator}
              names={names}
            />
          )}
        </div>
      </main>
    </div>
  );
}
