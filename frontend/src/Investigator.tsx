import { useRef, useState } from "react";
import Markdown from "react-markdown";
import { Bot, Clock, FilePlus2, Info, RefreshCw, ScanSearch, Send, ShieldAlert, UserRound, X } from "lucide-react";
import {
  api,
  ApiError,
  chatReply,
  draft,
  incident,
  errorText,
  type Application,
  type Service,
  type Draft,
} from "./api";
import type { z } from "zod";

type InvestigationReply = z.infer<typeof chatReply>;
const failureContent = {
  unavailable: {
    icon: Info,
    title: "AI temporarily unavailable",
    description: "The AI investigator couldn't complete your request. Your monitoring data remains available. Please try again.",
  },
  quota: {
    icon: Clock,
    title: "AI usage limit reached",
    description: "You've reached the available AI investigation allowance. You can continue exploring service health, logs, metrics and incidents.",
  },
  validation: {
    icon: ShieldAlert,
    title: "Investigation could not be verified",
    description: "The AI response could not be verified against the available monitoring evidence. Please try again.",
  },
};
type Failure = keyof typeof failureContent;
type Message = {
  id: string;
  role: "user" | "assistant";
  body: string;
  answered?: boolean;
  failure?: Failure;
  dismissed?: boolean;
  evidence?: InvestigationReply["evidence"];
};

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
  const [messages, setMessages] = useState<Message[]>([]);
  const sending = useRef(false);
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
  async function send(message: string, retryId?: string) {
    if (!message.trim() || busy || sending.current) return;
    sending.current = true;
    const id = retryId ?? crypto.randomUUID();
    const earlier = retryId ? messages.slice(0, messages.findIndex((m) => m.id === retryId)) : messages;
    if (!retryId) {
      setMessages((rows) => [...rows, { id, role: "user", body: message }]);
      setInput("");
    }
    setError("");
    setBusy("Investigating…");
    setReview(null);
    setSaved("");
    try {
      const reply = await api(demo ? "/analysis" : "/chat", chatReply, "POST", {
        message,
        session_id: session,
        service_id: service.id,
        conversational: true,
        previous_questions: earlier
          .filter((m) => m.role === "user" && m.answered)
          .slice(-3)
          .map((m) => m.body.slice(0, 500)),
      });
      if (reply.mode !== "live") {
        throw new ApiError(502, "Unverified investigation.", "validation");
      }
      setMessages((rows) => rows.flatMap((row): Message[] => row.id === id ? [
        { ...row, answered: true, failure: undefined },
        { id: id + "-reply", role: "assistant", body: reply.response, evidence: reply.evidence },
      ] : [row]));
    } catch (e) {
      const failure: Failure = e instanceof ApiError && (e.category === "quota" || e.status === 429)
        ? "quota" : e instanceof ApiError && e.category === "validation" ? "validation" : "unavailable";
      setMessages((rows) => rows.map((row) => row.id === id ? { ...row, failure, dismissed: false } : row));
    } finally {
      sending.current = false;
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
          <p>{demo ? "Use live AI to investigate this simulated service." : "Ask about health, recent logs or performance."}</p>
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
        {messages.map((m) => (
          <article className="message" key={m.id}>
            <h4>
              {m.role === "user" ? <UserRound /> : <Bot />}
              {m.role === "user" ? "You" : "Live AI investigation"}
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
            {!!m.evidence?.length && (
              <details className="investigationEvidence">
                <summary>Supporting evidence ({m.evidence.length})</summary>
                <ul>
                  {m.evidence.map(({ reference, text }) => (
                    <li key={reference}><code>{reference}</code> {text}</li>
                  ))}
                </ul>
              </details>
            )}
            {m.failure && (
              <InvestigationStatus
                failure={m.failure}
                dismissed={!!m.dismissed}
                busy={!!busy}
                retry={() => void send(m.body, m.id)}
                dismiss={() => setMessages((rows) => rows.map((row) =>
                  row.id === m.id ? { ...row, dismissed: true } : row))}
              />
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
          <small>Follow-ups use this conversation and refresh the evidence.</small>
          <button className="primary" disabled={!!busy || !input.trim()}>
            <Send />
            Send
          </button>
        </div>
      </form>
    </section>
  );
}

function InvestigationStatus({ failure, dismissed, busy, retry, dismiss }: {
  failure: Failure; dismissed: boolean; busy: boolean; retry: () => void; dismiss: () => void;
}) {
  const { icon: Icon, title, description } = failureContent[failure];
  return (
    <div className="demoWelcomeCollapse investigationStatusCollapse" data-open={!dismissed} inert={dismissed}>
      <div className="demoWelcomeClip">
        <div className="investigationStatus" role="status">
          <Icon className="investigationStatusIcon" size={20} aria-hidden="true" />
          <div className="investigationStatusContent">
            <strong>{title}</strong>
            <p>{description}</p>
            {failure !== "quota" && (
              <button type="button" className="investigationRetry" disabled={busy} onClick={retry}>
                <RefreshCw size={14} aria-hidden="true" /> Retry investigation
              </button>
            )}
          </div>
          <button type="button" className="investigationClose" aria-label="Dismiss investigation status" onClick={dismiss}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
