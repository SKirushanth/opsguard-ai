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
  Info,
  Layers3,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ScanSearch,
  Server,
  ShieldCheck,
  X,
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
  const [welcome, setWelcome] = useState(true), [tour, setTour] = useState(false);
  const operator = expiry > 0 && !demo,
    app = apps.find((a) => a.id === appId),
    service = services.find((s) => s.id === serviceId) ?? services[0];
  useEffect(() => {
    if (!expiry) return;
    function endSession() {
        setToken("");
        setExpiry(0);
        setApps([]);
        setServices([]);
        setDemo(false);
        setPage("Dashboard");
        setNotice("Session expired. Starting a fresh demo.");
    }
    const timeout = setTimeout(endSession, Math.max(0, expiry - Date.now()));
    window.addEventListener("workspace-session-ended", endSession);
    return () => {
      clearTimeout(timeout);
      window.removeEventListener("workspace-session-ended", endSession);
    };
  }, [expiry]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadError("");
    async function load() {
      if (!expiry) {
        const session = await api("/public-demo/session", z.object({ token: z.string(), expires_in: z.number() }), "POST");
        if (current) {
          setToken(session.token, true);
          setWelcome(true);
          setTour(false);
          setDemo(true);
          setAppId("demo");
          setExpiry(Date.now() + session.expires_in * 1000);
        }
        return;
      }
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
      setDemo(false);
      setPage("Dashboard");
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
          {navigation.filter(({ name }) => expiry && (operator || name !== "Applications")).map(({ name, icon: Icon }) => (
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
            <span>{demo ? "Demo Mode" : "Workspace"}</span>
            <ChevronRight />
            {page}
          </div>
          <button
            className={operator ? "sessionButton active" : "sessionButton"}
            aria-label={operator ? "Sign out" : "Operator sign-in"}
            title={operator ? "Sign out" : undefined}
            disabled={busy || (!expiry && loading)}
            onClick={() => {
              if (operator) { void logout(); return; }
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

              <button type="button" onClick={() => setShowLogin(false)}>
                Cancel
              </button>
            </form>
          </OperatorDialog>
          {demo && (
            <div className="demoWelcomeCollapse" data-open={welcome} inert={!welcome}>
              <div className="demoWelcomeClip">
                <section className="demoWelcome" aria-labelledby="demo-welcome-title">
                  <Info className="demoWelcomeInfo" size={20} aria-hidden="true" />
                  <div className="demoWelcomeContent">
                    <h2 id="demo-welcome-title">You're exploring the OpsGuard demo</h2>
                    <p>
                      This is an isolated workspace with simulated services. Explore
                      monitoring, trigger failures, investigate with AI and manage
                      incidents without affecting real applications.
                    </p>
                    <div className="demoWelcomeActions">
                      <button
                        type="button"
                        className="demoWelcomePrimary"
                        aria-expanded={tour}
                        aria-controls="demo-walkthrough"
                        onClick={() => setTour(!tour)}
                      >
                        Explore the workflow
                      </button>
                      <button type="button" onClick={() => setWelcome(false)}>
                        Dismiss
                      </button>
                    </div>
                    <div id="demo-walkthrough" className="demoWelcomeCollapse" data-open={tour} inert={!tour}>
                      <div className="demoWelcomeClip">
                        <p className="demoWelcomeGuide">
                          Open Service details and simulate a failure. Investigate
                          its evidence, prepare and confirm an incident, then resolve
                          it in Incidents.
                        </p>
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="demoWelcomeClose"
                    aria-label="Close demo welcome"
                    onClick={() => setWelcome(false)}
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </section>
              </div>
            </div>
          )}
          <header className="pageHead">
            <div>
              <h1>
                {page === "Dashboard"
                  ? "Monitoring overview"
                  : page === "Investigator"
                    ? demo ? "Demo analysis" : "AI Investigator"
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
          {app && service && (
            <div hidden={page !== "Investigator"}>
              <Investigator
                key={app.id + service.id + expiry}
                app={app}
                service={service}
                demo={demo}
                operator={operator || demo}
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
              operator={operator || demo}
              names={names}
            />
          )}
        </div>
      </main>
    </div>
  );
}
