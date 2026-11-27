import { useState } from "react";
import Markdown from "react-markdown";
import { Bot, FilePlus2, ScanSearch, Send, UserRound } from "lucide-react";
import {
  api,
  chatReply,
  draft,
  incident,
  errorText,
  type Application,
  type Service,
  type Draft,
} from "./api";

export function ReportBody({ report }: { report: Draft }) {
  return (
    <div className="report">
      <span className={"badge " + report.severity}>{report.severity}</span>
      <h3>{report.title}</h3>
      <p>{report.description}</p>
      <h4>Supporting evidence</h4>
      <ul>
        {report.evidence.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
      <h4>Likely root cause</h4>
      <p>{report.likely_root_cause}</p>
      <h4>Recommended steps</h4>
      <ol>
        {report.recommendations.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ol>
    </div>
  );
}

export function Investigator({
  app,
  service,
  operator,
  demo,
  openHistory,
}: {
  app: Application;
  service: Service;
  operator: boolean;
  demo: boolean;
  openHistory: () => void;
}) {
  const [session] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; body: string; mode?: "demo" | "live" }[]
  >([]);
  const [input, setInput] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const [review, setReview] = useState<{ data: Draft; key: string } | null>(
      null,
    ),
    [saved, setSaved] = useState("");
  const suggestions = [
    "Why is " + service.name + " failing?",
    "Check the current health of " + service.name + ".",
    "What does the monitoring evidence suggest?",
  ];
  async function send(message: string) {
    if (!message.trim() || busy) return;
    setMessages((m) => [...m, { role: "user", body: message }]);
    setInput("");
    setError("");
    setBusy("Investigating…");
    setReview(null);
    setSaved("");
    try {
      const reply = await api(demo ? "/analysis" : "/chat", chatReply, "POST", {
        message,
        session_id: session,
        service_id: service.id,
      });
      setMessages((m) => [...m, { role: "assistant", body: reply.response, mode: reply.mode }]);
    } catch (e) {
      setError(errorText(e));
      setInput(message);
    } finally {
      setBusy("");
    }
  }
  async function prepare() {
    setBusy("Preparing draft…");
    setError("");
    try {
      const data = await api("/incidents/draft", draft, "POST", {
        application_id: app.id,
        service_id: service.id,
        session_id: session,
        investigation: messages.at(-1)?.body.slice(0, 4000),
      });
      setReview({ data, key: crypto.randomUUID() });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }
  async function confirm() {
    if (!review || busy) return;
    setBusy("Saving report…");
    setError("");
    try {
      const row = await api("/incidents", incident, "POST", {
        ...review.data,
        approval_id: review.key,
        confirmed: true,
      });
      setSaved(row.id);
      setReview(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="panel">
      <header>
        <div>
          <h2>
            <ScanSearch />
            {demo ? "Demo analysis" : "Live AI investigator"}
          </h2>
          <p>
            {service.name} ·{" "}
            {app.id === "demo" ? "Simulated monitoring" : "External monitoring"}
          </p>
        </div>
      </header>
      {!messages.length && (
        <div className="empty">
          <h3>Investigate {service.name}</h3>
          <p>{demo ? "Investigate your simulated service with live AI when available. Demo analysis summarizes its evidence when live AI is unavailable." : "Ask about health, recent logs or performance."}</p>
          <div className="suggestions">
            {suggestions.map((s) => (
              <button key={s} onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
      <div aria-live="polite">
        {messages.map((m, i) => (
          <article className="message" key={i}>
            <h4>
              {m.role === "user" ? <UserRound /> : <Bot />}
              {m.role === "user" ? "You" : demo ? (m.mode === "live" ? "Live AI investigation" : "Demo analysis") : "OpsGuard AI"}
            </h4>
            {m.role === "assistant" ? (
              <Markdown
                skipHtml
                components={{
                  img: () => null,
                  a: ({ children }) => <span>{children}</span>,
                }}
              >
                {m.body}
              </Markdown>
            ) : (
              <p>{m.body}</p>
            )}
          </article>
        ))}
      </div>
      {busy && (
        <p className="notice" role="status">
          {busy}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {messages.at(-1)?.role === "assistant" && !review && !saved && (
        <div className="actions">
          <button disabled={!!busy || !operator} onClick={prepare}>
            <FilePlus2 />
            Prepare incident draft
          </button>
          {!operator && (
            <small>Operator sign-in required to prepare reports.</small>
          )}
        </div>
      )}
      {review && (
        <div className="draft">
          <h2>Review incident draft</h2>
          <ReportBody report={review.data} />
          <div className="actions">
            <button className="primary" disabled={!!busy} onClick={confirm}>
              Confirm incident creation
            </button>
            <button disabled={!!busy} onClick={() => setReview(null)}>
              Discard
            </button>
          </div>
        </div>
      )}
      {saved && (
        <div role="status" className="actions">
          Saved incident {saved}
          <button onClick={openHistory}>View history</button>
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <label htmlFor="question">Investigation question</label>
        <textarea
          id="question"
          value={input}
          maxLength={4000}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this service…"
        />
        <div>
          <small>Each request runs a fresh investigation.</small>
          <button className="primary" disabled={!!busy || !input.trim()}>
            <Send />
            Send
          </button>
        </div>
      </form>
    </section>
  );
}
