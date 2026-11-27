import { useEffect, useState } from "react";
import { z } from "zod";
import {
  Activity,
  Network,
  CircleCheck,
  LoaderCircle,
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
import { Dropdown } from "./Dropdown";
import { OperatorDialog } from "./OperatorDialog";
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
  const [demo, setDemo] = useState(false),
    [expiry, setExpiry] = useState(0),
    [showLogin, setShowLogin] = useState(false),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const operator = expiry > 0 && !demo,
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
        setDemo(false);
        setPage("Dashboard");
        setNotice("Session expired. Choose a new session to continue.");
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
  async function login(password?: string) {
    setBusy(true);
    setError("");
    try {
      const result = await api(
        password === undefined ? "/public-demo/session" : "/session",
        z.object({ token: z.string(), expires_in: z.number() }),
        "POST",
        password === undefined ? undefined : { password },
      );
      setToken(result.token, password === undefined);
      setDemo(password === undefined);
      if (password === undefined) {
        setAppId("demo");
        setPage("Dashboard");
        setApps([]);
        setServices([]);
        setNames({});
      }
      setExpiry(Date.now() + result.expires_in * 1000);
      setShowLogin(false);
      setNotice(password === undefined ? "Your private demo is ready." : "Signed in as workspace operator.");
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
        demo,
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
          {navigation.filter(({ name }) => !demo || name === "Dashboard" || name === "Service details").map(({ name, icon: Icon }) => (
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
        <div className="identity" key={operator ? "operator" : "guest"}>
          <CircleUserRound />
          <div>
            <strong>{demo ? "Demo Mode" : operator ? "Operator Mode" : "Guest access"}</strong>
            <small>
              {demo ? "Private simulation" : operator ? "Workspace session" : "Read-only session"}
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
            className={operator ? "sessionButton active" : "sessionButton"}
            aria-label={operator ? "Sign out" : "Operator sign-in"}
            title={operator ? "Sign out" : undefined}
            disabled={busy}
            onClick={() => {
              if (operator) { void logout(); return; }
              if (demo) {
                setToken("");
                setDemo(false);
                setExpiry(0);
                setApps([]);
                setServices([]);
                setPage("Dashboard");
                setNotice("");
              }
              setShowLogin((v) => !v);
            }}
          >
            <ShieldCheck />
            {operator ? (
              <>
                Operator <LogOut />
              </>
            ) : (
              "Operator sign-in"
            )}
          </button>
        </div>
        <div className="content">
          <OperatorDialog open={showLogin} dismiss={() => setShowLogin(false)}>
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
              <span className="dialogIcon">
                <ShieldCheck />
              </span>
              <h2 id="operator-title">Operator sign-in</h2>
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
              {error && (
                <p role="alert" className="error">
                  {error}
                </p>
              )}
              <button className="primary" disabled={busy || !showLogin}>
                {busy ? (
                  <>
                    <LoaderCircle className="spin" /> Verifying operator
                    credentials...
                  </>
                ) : (
                  "Sign in"
                )}
              </button>
              <button type="button" disabled={busy || !showLogin} onClick={() => void login()}>Explore live demo</button>
              <button type="button" onClick={() => setShowLogin(false)}>
                Cancel
              </button>
            </form>
          </OperatorDialog>
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
          </header>
          {operator && notice === "Signed in as workspace operator." ? (
            <p className="operatorToast" role="status">
              <CircleCheck />
              Operator session active<span className="sr-only">{notice}</span>
            </p>
          ) : (
            notice && (
              <p role="status" className="notice">
                {notice}
              </p>
            )
          )}
          {error && !showLogin && (
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
          <div className="controlBar">
            {!["Applications", "Incidents"].includes(page) && app && (
              <div className="contextBar">
                <Dropdown
                  label="Application"
                  icon={<Network />}
                  options={apps}
                  value={appId}
                  onChange={selectApp}
                />
                <span className="source">
                  {app.id === "demo" ? "Simulated data" : "External API"}
                </span>
                {page !== "Dashboard" && service && (
                  <Dropdown
                    label="Service"
                    options={services}
                    value={service.id}
                    onChange={setServiceId}
                  />
                )}
              </div>
            )}
            <button
              className="refresh"
              onClick={refresh}
              disabled={loading}
              aria-label="Refresh monitoring data"
            >
              <RefreshCw />
              Refresh
            </button>
          </div>
          {page === "Dashboard" &&
            props &&
            !loadError &&
            (!loading || services.length > 0) && <Overview {...props} />}
          {page === "Service details" && props && service && (
            <Details {...props} service={service} />
          )}
          {!demo && app && service && (
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
