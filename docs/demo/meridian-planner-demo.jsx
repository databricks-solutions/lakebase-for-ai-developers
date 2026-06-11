import React, { useState, useMemo, useRef, useEffect } from "react";
import {
  Activity, Database, Brain, Layers, Lock, Check, X, Minus, Plus, ArrowRight,
  Boxes, FileSearch, Cpu, GitBranch, ShieldCheck, AlertTriangle, Sparkles,
  Clock, Coins, Wrench, User, Zap, ChevronRight, Code2, LayoutGrid,
  GitCommitHorizontal, CornerDownRight,
} from "lucide-react";

/* ================================================================== *
 *  Meridian Supply Chain Planner — end-to-end agent demo
 *  Pages: Overview · Review (HITL) · Lakebase (state) · Trace (MLflow)
 *  Live through-line: committing on Review writes the rows that then
 *  appear in Lakebase and activate the commit spans in the Trace.
 * ================================================================== */

const C = {
  ink: "#0E1726", inkRaise: "#172339", inkRaise2: "#1F2D47",
  inkLine: "rgba(255,255,255,0.08)", inkSub: "#8B97AC", inkText: "#E7ECF4",
  surface: "#F4F6F9", card: "#FFFFFF", line: "#E3E7EE",
  text: "#131A26", sub: "#63707F",
  amber: "#DD8208", amberSoft: "#FBEFD8", amberLine: "#F0D49A",
  teal: "#0B9A82", tealSoft: "#DBF2EC", tealLine: "#A9E0D4",
  blue: "#3D6BDC", blueSoft: "#E4EBFB", red: "#D5384A", redSoft: "#FBE3E5",
  violet: "#7A5AF0", violetSoft: "#ECE7FD", pink: "#CC3F8E", pinkSoft: "#FBE5F1",
};
const UNIT = 312;
const fmt = (n) => n.toLocaleString("en-US");
const usd = (n) => (n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : `$${Math.round(n / 1000)}K`);

const KIND = {
  AGENT: { c: C.blue, s: C.blueSoft, icon: GitBranch, label: "AGENT" },
  CHAIN: { c: C.blue, s: C.blueSoft, icon: Layers, label: "CHAIN" },
  LLM: { c: C.violet, s: C.violetSoft, icon: Cpu, label: "LLM" },
  RETRIEVER: { c: C.teal, s: C.tealSoft, icon: FileSearch, label: "RETRIEVER" },
  TOOL: { c: C.amber, s: C.amberSoft, icon: Wrench, label: "TOOL" },
  EMBEDDING: { c: C.pink, s: C.pinkSoft, icon: Brain, label: "EMBEDDING" },
};

function Style() {
  return (
    <style>{`
      @import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
      * { box-sizing: border-box; }
      .ap { font-family:'Inter',system-ui,sans-serif; }
      .disp { font-family:'Archivo',sans-serif; }
      .mono { font-family:'IBM Plex Mono',monospace; }
      .ap button { font-family:inherit; cursor:pointer; }
      .ap button:focus-visible, .ap input:focus-visible, .ap textarea:focus-visible { outline:2px solid ${C.blue}; outline-offset:2px; }
      .lbl { font-family:'Archivo',sans-serif; text-transform:uppercase; letter-spacing:.12em; }
      .ap table { border-collapse: collapse; width:100%; }
      .ap th,.ap td { text-align:left; white-space:nowrap; }
      .scr::-webkit-scrollbar { width:8px; height:8px; }
      .scr::-webkit-scrollbar-thumb { background:rgba(120,135,160,.32); border-radius:8px; }
      .scrd::-webkit-scrollbar-thumb { background:rgba(255,255,255,.15); border-radius:8px; }
      @keyframes pls { 0%,100%{opacity:1} 50%{opacity:.4} }
      @keyframes inn { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }
      @keyframes spn { to { transform: rotate(360deg) } }
      .pls{animation:pls 1.6s ease-in-out infinite} .inn{animation:inn .3s ease both} .spn{animation:spn .9s linear infinite}
      @media (prefers-reduced-motion: reduce){ .pls,.inn,.spn{animation:none!important} }
      @media (max-width: 900px){
        .shell{ grid-template-columns:1fr !important; }
        .navside{ position:sticky; top:0; z-index:20; flex-direction:row !important; height:auto !important; overflow-x:auto; }
        .navside .navfoot{ display:none !important; }
        .navitem{ flex-direction:column; gap:3px !important; padding:8px 12px !important; }
        .navitem span.nl{ font-size:10px !important; }
        .two{ grid-template-columns:1fr !important; }
      }
    `}</style>
  );
}

/* ----------------------------- atoms ----------------------------- */
function Pill({ children, bg, fg, line, dot }) {
  return (
    <span className="lbl" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 10, fontWeight: 700, padding: "4px 9px", borderRadius: 999, background: bg, color: fg, border: `1px solid ${line || "transparent"}`, whiteSpace: "nowrap" }}>
      {dot && <span className="pls" style={{ width: 6, height: 6, borderRadius: 9, background: fg }} />}
      {children}
    </span>
  );
}
function Stat({ label, value, tone }) {
  return (
    <div style={{ flex: 1, minWidth: 80 }}>
      <div className="lbl" style={{ fontSize: 9, color: C.sub, fontWeight: 700, marginBottom: 3 }}>{label}</div>
      <div className="disp" style={{ fontSize: 18, fontWeight: 700, color: tone || C.text, letterSpacing: "-0.01em" }}>{value}</div>
    </div>
  );
}
function PageHead({ kicker, title, sub, right }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 14, marginBottom: 18, flexWrap: "wrap" }}>
      <div>
        <div className="lbl" style={{ fontSize: 9.5, color: C.sub, fontWeight: 700, marginBottom: 4 }}>{kicker}</div>
        <h1 className="disp" style={{ fontSize: 22, fontWeight: 700, margin: 0, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{title}</h1>
        {sub && <p style={{ fontSize: 12.5, color: C.sub, margin: "6px 0 0", maxWidth: 560, lineHeight: 1.5 }}>{sub}</p>}
      </div>
      <div style={{ marginLeft: "auto" }}>{right}</div>
    </div>
  );
}

/* ============================ DATA ============================ */
const EVIDENCE = [
  { id: "data", tag: "Data agent", via: "Genie · SQL", icon: Database, tone: C.blue, bg: C.blueSoft, head: "Stockout projected in 18 days", body: "On-hand 4,200 against rising use (~1,950/wk). Open PO-44817 (6,000) not due for 5 weeks.", chips: ["INV-7 line", "3,100 FG", "$9.4M exposed"] },
  { id: "rag", tag: "RAG agent", via: "3 sources", icon: FileSearch, tone: C.amber, bg: C.amberSoft, head: "Primary supplier on force majeure", body: "NovaSemi filed a typhoon fab slowdown — 3-wk delay on PO-44817. MSA §7.4 permits 12% expedite. Kyoto qualified on AVL-C.", chips: ["NovaSemi notice", "MSA §7.4", "AVL-C alt"] },
  { id: "memory", tag: "Memory agent", via: "pgvector · 0.91", icon: Brain, tone: C.teal, bg: C.tealSoft, head: "Prior decision retrieved", body: "Oct 2025 near-identical IGBT shortage resolved by A. Miller: 60/40 split, 6-wk SS bump, expedite ≤15%. Held 98.6%.", chips: ["decision 6602", "sim 0.91", "outcome: held"] },
];
const SPANS = [
  { id: "supervisor", name: "supervisor.orchestrate", kind: "AGENT", depth: 0, start: 0, dur: 4820, phase: 1, input: "Exception: PM-IG1200 shortage", output: "Routed → gather, planner", attrs: [["graph", "LangGraph"], ["nodes", "6"], ["checkpointer", "lakebase"]] },
  { id: "gather", name: "gather.fanout", kind: "CHAIN", depth: 1, start: 120, dur: 2240, phase: 1, input: "shared state → 3 agents", output: "evidence bundle", attrs: [["mode", "parallel"], ["branches", "3"], ["wall", "2.24s"]] },
  { id: "data", name: "data_agent.genie", kind: "TOOL", depth: 2, start: 180, dur: 1160, phase: 1, input: "shortage-risk question", output: "stockout 18d · $9.4M", attrs: [["surface", "Genie/SQL"], ["rows", "312"]] },
  { id: "rag", name: "rag_agent.retrieve", kind: "RETRIEVER", depth: 2, start: 180, dur: 1800, phase: 1, input: "supplier risk + terms", output: "3 sources", attrs: [["index", "vector_search"], ["k", "8→3"]] },
  { id: "memory", name: "memory_agent.recall", kind: "RETRIEVER", depth: 2, start: 180, dur: 720, phase: 1, input: "exception embedding", output: "6602 @ 0.91", attrs: [["store", "Lakebase pgvector"], ["op", "embedding <=> :q"]] },
  { id: "planner", name: "planner.reason", kind: "LLM", depth: 1, start: 2420, dur: 2340, phase: 1, input: "evidence + precedent 6602", output: "4 actions + cited rationale", attrs: [["model", "opus-4.8"], ["tok_in", "12,140"], ["tok_out", "1,420"], ["cited", "memory:6602"]] },
  { id: "resume", name: "resume.commit", kind: "CHAIN", depth: 0, start: 5120, dur: 1080, phase: 2, input: "Command(resume = decisions)", output: "committed · memory embedded", attrs: [["resumed", "ckpt_4f2a…e2"], ["writes", "4"]] },
  { id: "write_txn", name: "lakebase.write_txn", kind: "TOOL", depth: 1, start: 5180, dur: 540, phase: 2, input: "approved actions + override", output: "BEGIN; 4 rows; COMMIT", attrs: [["tables", "params, actions, constraints"], ["rows", "4"]] },
  { id: "embed", name: "memory.embed", kind: "EMBEDDING", depth: 1, start: 5860, dur: 300, phase: 2, input: "decision 6843 + rationale", output: "vector(1024) → agent_memory", attrs: [["model", "gte-large"], ["dims", "1024"]] },
];
const T_TOTAL = 6200, GAP = [4820, 5120];
const pctT = (v) => (v / T_TOTAL) * 100;
const msf = (d) => (d >= 1000 ? `${(d / 1000).toFixed(2)}s` : `${d}ms`);

/* ============================ NAV SHELL ============================ */
const NAV = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "review", label: "Review", icon: ShieldCheck },
  { id: "state", label: "Lakebase", icon: Database },
  { id: "trace", label: "Trace", icon: Activity },
];
function NavSide({ page, setPage, committed, reset }) {
  return (
    <aside className="navside scrd" style={{ background: C.ink, color: C.inkText, display: "flex", flexDirection: "column", height: "100vh", position: "sticky", top: 0, padding: "16px 12px", borderRight: `1px solid ${C.inkLine}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "4px 8px 16px" }}>
        <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 8, background: C.amber, color: "#1A1206", flexShrink: 0 }}><Activity size={17} /></span>
        <div className="navfoot" style={{ minWidth: 0 }}>
          <div className="disp" style={{ fontSize: 13.5, fontWeight: 700, lineHeight: 1.1 }}>Meridian SC</div>
          <div className="mono" style={{ fontSize: 9.5, color: C.inkSub }}>Planner agent</div>
        </div>
      </div>
      <nav style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {NAV.map((n) => {
          const Icon = n.icon; const on = page === n.id;
          return (
            <button key={n.id} className="navitem" onClick={() => setPage(n.id)} style={{ display: "flex", alignItems: "center", gap: 10, border: "none", borderRadius: 9, padding: "9px 11px", background: on ? C.inkRaise2 : "transparent", color: on ? "#fff" : C.inkSub, textAlign: "left" }}>
              <Icon size={16} style={{ flexShrink: 0 }} />
              <span className="nl disp" style={{ fontSize: 13, fontWeight: 600 }}>{n.label}</span>
              {on && <ChevronRight size={13} style={{ marginLeft: "auto" }} />}
            </button>
          );
        })}
      </nav>
      <div className="navfoot" style={{ marginTop: "auto", paddingTop: 14 }}>
        <div style={{ padding: 11, borderRadius: 10, background: C.inkRaise, border: `1px solid ${C.inkLine}`, marginBottom: 10 }}>
          <div className="lbl" style={{ fontSize: 8.5, color: C.inkSub, fontWeight: 700, marginBottom: 6 }}>Active run</div>
          <div className="mono" style={{ fontSize: 10.5, color: C.inkText, lineHeight: 1.7 }}>run_8841<br />planner: a.miller</div>
          <div style={{ marginTop: 8 }}>
            {committed
              ? <Pill bg="rgba(11,154,130,.18)" fg="#4FD1B5" line="rgba(11,154,130,.4)">Committed</Pill>
              : <Pill bg="rgba(221,130,8,.18)" fg="#F4B860" line="rgba(221,130,8,.4)" dot>Awaiting approval</Pill>}
          </div>
        </div>
        <button onClick={reset} style={{ width: "100%", border: `1px solid ${C.inkLine}`, background: "transparent", color: C.inkSub, fontSize: 11, fontWeight: 600, padding: "8px", borderRadius: 8 }}>Reset demo</button>
      </div>
    </aside>
  );
}

/* ============================ OVERVIEW ============================ */
const FLOW = [
  { id: "supervisor", label: "Supervisor", icon: GitBranch, sub: "orchestrates" },
  { id: "gather", label: "Gather", icon: Boxes, sub: "data · rag · memory" },
  { id: "planner", label: "Planner", icon: Cpu, sub: "proposes plan" },
  { id: "hitl", label: "Human review", icon: ShieldCheck, sub: "approve / edit" },
  { id: "commit", label: "Lakebase", icon: Database, sub: "durable + memory" },
];
function Overview({ setPage, committed }) {
  return (
    <div className="inn">
      <PageHead kicker="Mission control" title="An IGBT shortage just hit the EV inverter line."
        sub="The planner agent has analyzed it and proposed a fix. It's holding for a human decision — nothing is written until you approve."
        right={committed
          ? <Pill bg={C.tealSoft} fg={C.teal} line={C.tealLine}>Run committed</Pill>
          : <Pill bg={C.amberSoft} fg={C.amber} line={C.amberLine} dot>Exception · critical</Pill>} />

      {/* exception KPIs */}
      <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 14, padding: 18, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <AlertTriangle size={15} color={C.red} />
          <span className="lbl" style={{ fontSize: 10, fontWeight: 700, color: C.red }}>IGBT Power Module · PM-IG1200</span>
        </div>
        <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
          <Stat label="Stockout in" value="18 days" tone={C.red} />
          <Stat label="Revenue at risk" value="$9.4M" />
          <Stat label="Finished goods" value="3,100 u" />
          <Stat label="Proposed actions" value="4" tone={C.amber} />
        </div>
      </div>

      {/* agent graph */}
      <div style={{ background: C.ink, color: C.inkText, borderRadius: 14, padding: "18px 18px 20px", marginBottom: 16 }}>
        <div className="lbl" style={{ fontSize: 9.5, color: C.inkSub, fontWeight: 700, marginBottom: 16 }}>Agent graph · LangGraph on Lakebase</div>
        <div className="scr" style={{ display: "flex", alignItems: "stretch", gap: 0, overflowX: "auto" }}>
          {FLOW.map((f, i) => {
            const Icon = f.icon; const done = committed || i < 3; const active = !committed && i === 3;
            const tone = active ? C.amber : done ? C.teal : C.inkSub;
            return (
              <React.Fragment key={f.id}>
                <div style={{ flex: 1, minWidth: 108, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 7 }}>
                  <span style={{ display: "grid", placeItems: "center", width: 40, height: 40, borderRadius: 11, background: active ? C.amber : C.inkRaise, color: active ? "#1A1206" : tone, border: `1px solid ${active ? C.amber : C.inkLine}` }}>
                    <Icon size={18} />
                  </span>
                  <div>
                    <div className="disp" style={{ fontSize: 12, fontWeight: 600 }}>{f.label}</div>
                    <div className="mono" style={{ fontSize: 9, color: C.inkSub }}>{f.sub}</div>
                  </div>
                  {active && <Pill bg="rgba(221,130,8,.18)" fg="#F4B860" dot>here</Pill>}
                </div>
                {i < FLOW.length - 1 && <ArrowRight size={15} color={C.inkSub} style={{ alignSelf: "flex-start", marginTop: 12, flexShrink: 0 }} />}
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {/* demo flow links */}
      <div className="lbl" style={{ fontSize: 9.5, color: C.sub, fontWeight: 700, marginBottom: 10 }}>Walk the demo</div>
      <div className="two" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 8 }}>
        {[
          { id: "review", n: "01", icon: ShieldCheck, t: "Make the call", d: "Approve, edit, or hold each action and commit to Lakebase." },
          { id: "state", n: "02", icon: Database, t: "See what landed", d: "The exact Postgres rows + pgvector memory, in two lenses." },
          { id: "trace", n: "03", icon: Activity, t: "Prove it ran", d: "MLflow waterfall, span detail, and the eval gate." },
        ].map((s) => {
          const Icon = s.icon;
          return (
            <button key={s.id} onClick={() => setPage(s.id)} style={{ textAlign: "left", background: C.card, border: `1px solid ${C.line}`, borderRadius: 13, padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="mono" style={{ fontSize: 11, color: C.sub }}>{s.n}</span>
                <Icon size={15} color={C.amber} style={{ marginLeft: "auto" }} />
              </div>
              <div className="disp" style={{ fontSize: 14.5, fontWeight: 700 }}>{s.t}</div>
              <div style={{ fontSize: 11.5, color: C.sub, lineHeight: 1.5 }}>{s.d}</div>
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11.5, color: C.blue, fontWeight: 600, marginTop: 2 }}>Open <ArrowRight size={12} /></span>
            </button>
          );
        })}
      </div>

      <button onClick={() => setPage("review")} style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8, border: "none", borderRadius: 10, padding: "12px 20px", fontSize: 13.5, fontWeight: 700, fontFamily: "Archivo, sans-serif", background: C.ink, color: "#fff" }}>
        <ShieldCheck size={16} /> Start the review
      </button>
    </div>
  );
}

/* ============================ REVIEW ============================ */
function QtyStepper({ value, onChange, step, min }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden", background: "#FAFBFC" }}>
      <button onClick={() => onChange(Math.max(min, value - step))} style={{ border: "none", background: "transparent", padding: "6px 8px", color: C.sub }} aria-label="decrease"><Minus size={13} /></button>
      <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: C.text, minWidth: 60, textAlign: "center" }}>{fmt(value)}</span>
      <button onClick={() => onChange(value + step)} style={{ border: "none", background: "transparent", padding: "6px 8px", color: C.sub }} aria-label="increase"><Plus size={13} /></button>
    </div>
  );
}
function EvidenceCard({ e }) {
  const Icon = e.icon;
  return (
    <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: 14, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
        <span style={{ display: "grid", placeItems: "center", width: 24, height: 24, borderRadius: 6, background: e.bg, color: e.tone }}><Icon size={13} /></span>
        <span className="lbl" style={{ fontSize: 9.5, fontWeight: 700, color: C.text }}>{e.tag}</span>
        <span className="mono" style={{ fontSize: 9.5, color: C.sub, marginLeft: "auto" }}>{e.via}</span>
      </div>
      <div className="disp" style={{ fontSize: 13, fontWeight: 700, marginBottom: 5 }}>{e.head}</div>
      <div style={{ fontSize: 11.5, color: C.sub, lineHeight: 1.5, marginBottom: 10 }}>{e.body}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: "auto" }}>
        {e.chips.map((c) => <span key={c} className="mono" style={{ fontSize: 9.5, color: e.tone, background: e.bg, padding: "2px 7px", borderRadius: 5 }}>{c}</span>)}
      </div>
    </div>
  );
}
function ActionCard({ a, st, onStatus, onQty }) {
  const approved = st.status === "approved", rejected = st.status === "rejected";
  const edited = a.editable && st.qty !== a.qty;
  const railColor = approved ? C.teal : rejected ? "#C2CAD4" : C.amber;
  return (
    <div style={{ position: "relative", background: C.card, border: `1px solid ${approved ? C.tealLine : rejected ? C.line : C.amberLine}`, borderRadius: 12, padding: "14px 16px 14px 18px", opacity: rejected ? 0.66 : 1 }}>
      <span style={{ position: "absolute", left: 0, top: 12, bottom: 12, width: 4, borderRadius: 4, background: railColor }} />
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
            <span className="lbl" style={{ fontSize: 8.5, fontWeight: 700, color: railColor, background: approved ? C.tealSoft : rejected ? "#EEF1F4" : C.amberSoft, padding: "2px 7px", borderRadius: 5 }}>{a.kind}</span>
            <span className="disp" style={{ fontSize: 14, fontWeight: 700 }}>{a.title}</span>
          </div>
          <div style={{ fontSize: 11.5, color: C.sub, lineHeight: 1.5 }}>{a.detail}</div>
          <div style={{ display: "flex", gap: 18, marginTop: 11, flexWrap: "wrap" }}>
            {a.editable ? (
              <div>
                <div className="lbl" style={{ fontSize: 9, color: C.sub, fontWeight: 700, marginBottom: 5 }}>{a.qtyLabel}</div>
                <QtyStepper value={st.qty} onChange={onQty} step={a.step} min={a.min} />
                {edited && <span className="mono" style={{ fontSize: 9.5, color: C.amber, marginLeft: 8 }}>was {fmt(a.qty)}</span>}
              </div>
            ) : a.facts.map((f) => (
              <div key={f.k}><div className="lbl" style={{ fontSize: 9, color: C.sub, fontWeight: 700, marginBottom: 3 }}>{f.k}</div><div className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: f.tone || C.text }}>{f.v}</div></div>
            ))}
            <div><div className="lbl" style={{ fontSize: 9, color: C.sub, fontWeight: 700, marginBottom: 3 }}>Writes to</div><div className="mono" style={{ fontSize: 12, fontWeight: 600, color: C.blue }}>{a.table}</div></div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button onClick={() => onStatus(approved ? "pending" : "approved")} style={{ display: "flex", alignItems: "center", gap: 6, border: `1px solid ${approved ? C.teal : C.line}`, background: approved ? C.teal : "#fff", color: approved ? "#fff" : C.text, fontSize: 11.5, fontWeight: 600, padding: "7px 12px", borderRadius: 8, justifyContent: "center" }}><Check size={13} /> {approved ? "Approved" : "Approve"}</button>
          <button onClick={() => onStatus(rejected ? "pending" : "rejected")} style={{ display: "flex", alignItems: "center", gap: 6, border: `1px solid ${rejected ? C.red : C.line}`, background: rejected ? C.redSoft : "#fff", color: rejected ? C.red : C.sub, fontSize: 11.5, fontWeight: 600, padding: "7px 12px", borderRadius: 8, justifyContent: "center" }}><X size={13} /> {rejected ? "Held" : "Hold"}</button>
        </div>
      </div>
    </div>
  );
}
function Ledger({ writes, committed, committing }) {
  return (
    <aside className="scrd" style={{ background: C.ink, color: C.inkText, padding: 16, borderRadius: 14, alignSelf: "start" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
        <Database size={15} color={C.teal} /><span className="disp" style={{ fontSize: 14, fontWeight: 700 }}>Lakebase</span>
        <span className="mono" style={{ fontSize: 9.5, color: C.inkSub, marginLeft: "auto" }}>scp_planning</span>
      </div>
      <div style={{ fontSize: 10.5, color: C.inkSub, marginBottom: 14 }}>Write-back ledger</div>
      {writes.length === 0
        ? <div style={{ padding: "18px 12px", textAlign: "center", border: `1px dashed ${C.inkLine}`, borderRadius: 10, color: C.inkSub, fontSize: 11 }}>Approve an action to stage a row.</div>
        : <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {writes.map((w) => (
              <div key={w.key} className="inn" style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 11px", borderRadius: 9, background: C.inkRaise, border: `1px solid ${committed ? "rgba(11,154,130,.4)" : C.inkLine}` }}>
                <span className={committed ? "" : "pls"} style={{ width: 7, height: 7, borderRadius: 9, background: committed ? C.teal : C.amber, flexShrink: 0 }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="mono" style={{ fontSize: 10.5, color: C.inkText, fontWeight: 600 }}>{w.op} {w.table}</div>
                  <div className="mono" style={{ fontSize: 9.5, color: C.inkSub, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{w.summary}</div>
                </div>
                {committed ? <Check size={13} color={C.teal} /> : <span className="lbl" style={{ fontSize: 8, color: C.amber, fontWeight: 700 }}>staged</span>}
              </div>
            ))}
          </div>}
      <div style={{ marginTop: 14, padding: 12, borderRadius: 11, background: committed ? "rgba(11,154,130,.12)" : C.inkRaise, border: `1px solid ${committed ? "rgba(11,154,130,.4)" : C.inkLine}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}><Brain size={13} color={committed ? C.teal : C.inkSub} /><span className="lbl" style={{ fontSize: 9, fontWeight: 700, color: committed ? C.teal : C.inkSub }}>agent_memory · pgvector</span></div>
        <div style={{ fontSize: 10.8, color: committed ? C.inkText : C.inkSub, lineHeight: 1.5 }}>
          {committed ? <>Decision <span className="mono">6843</span> embedded — retrievable next shortage.</> : "On commit, this decision is embedded for future recall."}
        </div>
      </div>
      {committing && <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 8, color: C.amber, fontSize: 11 }}><span className="spn" style={{ width: 13, height: 13, border: `2px solid ${C.amber}`, borderTopColor: "transparent", borderRadius: 9 }} /><span className="mono">BEGIN; flushing…</span></div>}
    </aside>
  );
}
const STEP = [
  { id: "supervisor", label: "Supervisor" }, { id: "gather", label: "Gather" },
  { id: "planner", label: "Planner" }, { id: "hitl", label: "Human review" }, { id: "commit", label: "Commit" },
];
function Stepper({ committed }) {
  return (
    <div className="scr" style={{ display: "flex", alignItems: "center", gap: 0, overflowX: "auto", marginBottom: 16 }}>
      {STEP.map((s, i) => {
        const done = committed ? true : i < 3; const active = committed ? i === 4 : i === 3;
        return (
          <React.Fragment key={s.id}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 11px", borderRadius: 9, background: active ? C.amberSoft : "transparent", flexShrink: 0 }}>
              <span style={{ display: "grid", placeItems: "center", width: 20, height: 20, borderRadius: 6, background: active ? C.amber : done ? C.teal : "#E0E5EC", color: "#fff" }}>{done && !active ? <Check size={12} /> : <span className="mono" style={{ fontSize: 10, fontWeight: 700 }}>{i + 1}</span>}</span>
              <span className="disp" style={{ fontSize: 11.5, fontWeight: 600, color: active ? C.amber : done ? C.text : C.sub }}>{s.label}</span>
            </div>
            {i < STEP.length - 1 && <ArrowRight size={13} color="#C2CAD4" style={{ flexShrink: 0 }} />}
          </React.Fragment>
        );
      })}
    </div>
  );
}
function Review({ state, setPage }) {
  const { actions, setActions, ss, setSs, rationale, setRationale, committed, doCommit, committing, writes, costDelta } = state;
  const DEFS = {
    expedite: { kind: "Expedite", title: "Pull in PO-44817", table: "approved_actions", detail: "Expedite the NovaSemi PO under MSA §7.4 — recover 3 weeks at 12% premium.", editable: true, qty: 6000, qtyLabel: "Units", step: 500, min: 0 },
    buffer: { kind: "New PO", title: "Buffer order · Kyoto", table: "approved_actions", detail: "Split-source with AVL-C alternate. MOQ 2,500 · 4-wk lead · +8%.", editable: true, qty: 2500, qtyLabel: "Units", step: 500, min: 2500 },
    safety: { kind: "Parameter", title: "Raise safety stock · PM-IG1200", table: "planning_parameters", detail: "Temporary buffer through the disruption. Auto-expires in 6 weeks.", editable: false, facts: [{ k: "Current", v: "1,800" }, { k: "Proposed", v: fmt(ss), tone: C.amber }, { k: "Expires", v: "+6 wks" }] },
    alloc: { kind: "Allocation", title: "Prioritize INV-7 over spares", table: "constraints", detail: "Fair-share override on constrained on-hand — protect the $9.4M program.", editable: false, facts: [{ k: "Protect", v: "INV-7" }, { k: "Defer", v: "Spares", tone: C.red }, { k: "Scope", v: "Until PO lands" }] },
  };
  const decided = Object.values(actions).filter((a) => a.status !== "pending").length;
  const canCommit = !committed && !committing && writes.length > 0 && rationale.trim().length >= 12;
  return (
    <div className="inn">
      <PageHead kicker="Human-in-the-loop" title="Review the plan. Your decisions commit to Lakebase."
        right={committed ? <Pill bg={C.tealSoft} fg={C.teal} line={C.tealLine}>Committed</Pill> : <Pill bg={C.amberSoft} fg={C.amber} line={C.amberLine} dot>Awaiting</Pill>} />
      <Stepper committed={committed} />

      <div className="two" style={{ display: "grid", gridTemplateColumns: "1fr 290px", gap: 16, alignItems: "start" }}>
        <div>
          {/* KPIs */}
          <div style={{ display: "flex", gap: 20, padding: 16, background: C.card, border: `1px solid ${C.line}`, borderRadius: 13, marginBottom: 16, flexWrap: "wrap" }}>
            <Stat label="Stockout in" value="18 days" tone={C.red} /><Stat label="Revenue at risk" value="$9.4M" /><Stat label="FG exposed" value="3,100 u" /><Stat label="Mitigation cost" value={costDelta ? usd(costDelta) : "—"} tone={C.amber} />
          </div>
          {/* evidence */}
          <div className="lbl" style={{ fontSize: 9.5, fontWeight: 700, color: C.sub, marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}><Boxes size={13} /> Gather · evidence</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, marginBottom: 20 }}>{EVIDENCE.map((e) => <EvidenceCard key={e.id} e={e} />)}</div>
          {/* actions */}
          <div style={{ display: "flex", alignItems: "center", marginBottom: 11 }}>
            <span className="lbl" style={{ fontSize: 9.5, fontWeight: 700, color: C.sub, display: "flex", alignItems: "center", gap: 7 }}><Cpu size={13} /> Proposed actions</span>
            <span className="mono" style={{ fontSize: 10.5, color: C.sub, marginLeft: "auto" }}>{decided}/4 decided</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            {Object.entries(DEFS).map(([id, a]) => <ActionCard key={id} a={a} st={actions[id]} onStatus={(s) => setActions((p) => ({ ...p, [id]: { ...p[id], status: s } }))} onQty={(q) => setActions((p) => ({ ...p, [id]: { ...p[id], qty: q } }))} />)}
          </div>
          {actions.safety.status === "approved" && (
            <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: 14, marginTop: 11 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}><span className="lbl" style={{ fontSize: 9, fontWeight: 700, color: C.sub }}>Override safety stock · PM-IG1200</span><span className="mono" style={{ fontSize: 13, fontWeight: 600, color: C.amber }}>{fmt(ss)} u</span></div>
              <input type="range" min={1800} max={4000} step={100} value={ss} onChange={(e) => setSs(+e.target.value)} disabled={committed} style={{ width: "100%", accentColor: C.amber }} />
              <div className="mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.sub, marginTop: 4 }}><span>1,800 now</span><span>precedent → 3,000</span><span>4,000</span></div>
            </div>
          )}
          {/* rationale + commit */}
          <div style={{ background: C.card, border: `1px solid ${canCommit ? C.amberLine : C.line}`, borderRadius: 14, padding: 16, marginTop: 18 }}>
            <label className="lbl" htmlFor="rat" style={{ fontSize: 9.5, fontWeight: 700, display: "block", marginBottom: 4 }}>Decision rationale <span style={{ color: C.red }}>· required</span></label>
            <p style={{ fontSize: 11, color: C.sub, margin: "0 0 8px" }}>Embedded into <span className="mono">agent_memory</span> for future recall.</p>
            <textarea id="rat" value={rationale} onChange={(e) => setRationale(e.target.value)} disabled={committed} placeholder="e.g. Mirrored the Oct precedent — split-source 70/30, 6-wk buffer, expedite within 12%. Protected INV-7."
              style={{ width: "100%", minHeight: 60, resize: "vertical", border: `1px solid ${C.line}`, borderRadius: 9, padding: "9px 11px", fontSize: 12.5, fontFamily: "Inter, sans-serif", lineHeight: 1.5, background: committed ? "#F7F8FA" : "#fff" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
              {!committed ? (
                <button onClick={doCommit} disabled={!canCommit} style={{ display: "flex", alignItems: "center", gap: 8, border: "none", borderRadius: 10, padding: "11px 18px", fontSize: 13, fontWeight: 700, fontFamily: "Archivo, sans-serif", background: canCommit ? C.ink : "#D7DCE3", color: canCommit ? "#fff" : "#9AA4B1", cursor: canCommit ? "pointer" : "not-allowed" }}>
                  {committing ? <><span className="spn" style={{ width: 14, height: 14, border: "2px solid #fff", borderTopColor: "transparent", borderRadius: 9 }} /> Committing…</> : <><Database size={15} /> Commit {writes.length} write{writes.length !== 1 ? "s" : ""}</>}
                </button>
              ) : (
                <>
                  <span style={{ display: "flex", alignItems: "center", gap: 7, color: C.teal, fontWeight: 700, fontSize: 13 }}><Sparkles size={15} /> Committed to Lakebase</span>
                  <button onClick={() => setPage("state")} style={{ display: "flex", alignItems: "center", gap: 6, border: `1px solid ${C.line}`, background: "#fff", color: C.blue, fontSize: 11.5, fontWeight: 600, padding: "8px 13px", borderRadius: 9 }}>See it in Lakebase <ArrowRight size={13} /></button>
                </>
              )}
              {!committed && <span style={{ fontSize: 11, color: C.sub, display: "flex", alignItems: "center", gap: 6 }}>{rationale.trim().length >= 12 ? <><Check size={13} color={C.teal} /> Rationale captured</> : <><Clock size={13} /> Add a rationale to commit</>}</span>}
            </div>
          </div>
        </div>
        <Ledger writes={writes} committed={committed} committing={committing} />
      </div>
    </div>
  );
}

/* ============================ STATE (LAKEBASE) ============================ */
function Type({ t }) {
  const map = { uuid: C.blue, text: C.sub, int: C.teal, numeric: C.teal, bool: C.amber, date: C.violet, "vector(1024)": C.violet };
  return <span className="mono" style={{ fontSize: 9, color: map[t] || C.sub }}>{t}</span>;
}
function Bool({ v }) { return <span className="mono" style={{ fontSize: 10.5, fontWeight: 600, color: v ? C.teal : C.sub, background: v ? C.tealSoft : "#EEF1F4", padding: "1px 7px", borderRadius: 5 }}>{String(v)}</span>; }
function LensToggle({ lens, setLens }) {
  const opt = (k, Icon, label) => {
    const on = lens === k;
    return <button onClick={() => setLens(k)} style={{ display: "flex", alignItems: "center", gap: 6, border: "none", borderRadius: 7, padding: "6px 12px", fontSize: 11.5, fontWeight: 600, fontFamily: "Archivo, sans-serif", background: on ? (k === "eng" ? C.inkRaise2 : "#fff") : "transparent", color: on ? (k === "eng" ? C.inkText : C.ink) : C.inkSub, boxShadow: on && k === "plan" ? "0 1px 2px rgba(0,0,0,.25)" : "none" }}><Icon size={13} /> {label}</button>;
  };
  return <div style={{ display: "inline-flex", gap: 3, padding: 3, borderRadius: 9, background: C.inkRaise, border: `1px solid ${C.inkLine}` }}>{opt("eng", Code2, "Engineer")}{opt("plan", User, "Planner")}</div>;
}
function StatePage({ state }) {
  const [lens, setLens] = useState("eng");
  const [tab, setTab] = useState("planning_parameters");
  const { committed, actions, ss } = state;

  const TABLES = {
    planning_parameters: {
      plain: "Active overrides", icon: Boxes,
      cols: [{ k: "param_id", plain: "ID", t: "uuid", hideP: true }, { k: "sku", plain: "Part" }, { k: "parameter", plain: "Setting" }, { k: "prev_value", plain: "Was", t: "numeric" }, { k: "new_value", plain: "Now", t: "numeric", em: true }, { k: "expires_on", plain: "Until", t: "date" }, { k: "approved_by", plain: "By" }],
      rows: [
        ...(committed && actions.safety.status === "approved" ? [{ param_id: "PP-2207", sku: "PM-IG1200", parameter: "safety_stock", prev_value: "1,800", new_value: fmt(ss), expires_on: "2026-07-22", approved_by: "a.miller", _new: true }] : []),
        { param_id: "PP-2118", sku: "PM-IG0800", parameter: "safety_stock", prev_value: "1,500", new_value: "2,400", expires_on: "2025-11-25", approved_by: "a.miller", _expired: true },
      ],
    },
    approved_actions: {
      plain: "Decisions on record", icon: Check,
      cols: [{ k: "action_id", plain: "ID", t: "uuid", hideP: true }, { k: "type", plain: "Action" }, { k: "reference", plain: "Ref" }, { k: "qty", plain: "Qty", t: "int" }, { k: "cost_delta", plain: "Cost Δ", t: "numeric", em: true }, { k: "status", plain: "Status" }, { k: "approved_by", plain: "By" }],
      rows: committed ? [
        ...(actions.expedite.status === "approved" ? [{ action_id: "AA-9931", type: "expedite", reference: "PO-44817", qty: fmt(actions.expedite.qty), cost_delta: `+${usd(actions.expedite.qty * UNIT * 0.12)}`, status: "approved", approved_by: "a.miller", _new: true }] : []),
        ...(actions.buffer.status === "approved" ? [{ action_id: "AA-9932", type: "buffer_po", reference: "KYOTO", qty: fmt(actions.buffer.qty), cost_delta: "+8%", status: "approved", approved_by: "a.miller", _new: true }] : []),
      ] : [],
    },
    constraints: {
      plain: "Rules the agent follows", icon: Lock,
      cols: [{ k: "constraint_id", plain: "ID", t: "uuid", hideP: true }, { k: "rule", plain: "Rule" }, { k: "scope", plain: "Applies to" }, { k: "active", plain: "On", t: "bool" }, { k: "created_by", plain: "By" }],
      rows: [
        ...(committed && actions.alloc.status === "approved" ? [{ constraint_id: "CN-0455", rule: "Allocate INV-7 before spares", scope: "PM-IG1200", active: true, created_by: "a.miller", _new: true }] : []),
        { constraint_id: "CN-0391", rule: "No NovaSemi during quality hold", scope: "PM-IG0800", active: false, created_by: "j.okafor" },
      ],
    },
  };
  const t = TABLES[tab];
  const cols = t.cols.filter((c) => !(lens === "plan" && c.hideP));
  const MEM = [
    ...(committed ? [{ id: "6843", sim: null, summary: "PM-IG1200 shortage: split-source 70/30, SS 1,800→" + fmt(ss) + " 6 wks, expedite ≤12%.", tags: ["shortage", "igbt"], outcome: "pending", new: true, vec: "[ 0.014, -0.221, …]" }] : []),
    { id: "6602", sim: 0.91, summary: "PM-IG0800 shortage: 60/40 split, 6-wk SS bump, expedite ≤15%.", tags: ["shortage", "igbt"], outcome: "held · 98.6%", new: false, vec: "[ 0.019, -0.198, …]" },
    { id: "6210", sim: 0.74, summary: "Casting A-CST44: dual-source qualified, no expedite.", tags: ["shortage", "casting"], outcome: "held · 97.1%", new: false, vec: "[-0.041, -0.150, …]" },
  ];

  return (
    <div className="inn">
      <PageHead kicker="Lakebase state" title="What persists between runs"
        sub="The same rows, two lenses. Toggle to read them as raw Postgres or in plain language."
        right={<LensToggle lens={lens} setLens={setLens} />} />

      {!committed && (
        <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "11px 14px", background: C.amberSoft, border: `1px solid ${C.amberLine}`, borderRadius: 11, marginBottom: 16, fontSize: 12, color: "#7A4A05" }}>
          <Clock size={15} color={C.amber} /> Nothing committed yet — new rows appear here once the planner commits on the <b style={{ margin: "0 3px" }}>Review</b> page.
        </div>
      )}

      {/* short-term */}
      <div className="lbl" style={{ fontSize: 9.5, color: C.blue, fontWeight: 700, marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}><Clock size={13} /> Short-term · this run's checkpoint</div>
      <div className="two" style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 14, marginBottom: 18 }}>
        <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 13, padding: 16 }}>
          {lens === "eng" ? <>
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 12 }}><GitCommitHorizontal size={14} color={C.blue} /><span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>checkpoints</span></div>
            {[["thread_id", "run_8841", "text"], ["checkpoint_id", "ckpt_4f2a…e2", "uuid"], ["step", committed ? "9" : "7", "int"], ["next", committed ? '["END"]' : '["human_review"]', "text"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 8, padding: "5px 0", borderBottom: "1px solid #F1F3F6" }}><span className="mono" style={{ fontSize: 10.5, color: C.sub, width: 96 }}>{k}</span><span className="mono" style={{ fontSize: 11, fontWeight: 500, flex: 1 }}>{v}</span></div>
            ))}
          </> : <>
            <div className="lbl" style={{ fontSize: 9.5, color: C.blue, fontWeight: 700, marginBottom: 10 }}>This run, right now</div>
            {[["Topic", "IGBT shortage"], ["Step", committed ? "Done (9 of 9)" : "Human review (7 of 9)"], ["Planner", "A. Miller"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid #F1F3F6" }}><span style={{ fontSize: 12, color: C.sub }}>{k}</span><span className="disp" style={{ fontSize: 12.5, fontWeight: 600 }}>{v}</span></div>
            ))}
          </>}
        </div>
        <div style={{ background: committed ? C.tealSoft : (lens === "eng" ? C.ink : C.amberSoft), border: `1px solid ${committed ? C.tealLine : (lens === "eng" ? C.inkLine : C.amberLine)}`, borderRadius: 13, padding: 16, color: lens === "eng" && !committed ? C.inkText : C.text }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>{committed ? <Check size={14} color={C.teal} /> : <Lock size={13} color={C.amber} />}<span className="disp" style={{ fontSize: 13, fontWeight: 700 }}>{committed ? "Resumed & committed" : "Paused for your decision"}</span></div>
          <p style={{ fontSize: 11.5, color: committed ? C.teal : (lens === "eng" ? C.inkSub : C.sub), lineHeight: 1.5, margin: 0 }}>
            {committed ? "Command(resume) promoted the checkpoint into durable rows and embedded one memory." : (lens === "eng" ? "__interrupt__ parked · resumable:true · graph.invoke(Command(resume=decisions))" : "Held until you approve. Resumes exactly where it stopped — safe to leave and come back.")}
          </p>
        </div>
      </div>

      {/* promotion */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", marginBottom: 18, borderRadius: 12, background: C.ink, color: C.inkText, flexWrap: "wrap" }}>
        <Zap size={15} color={C.teal} />
        <span className="mono" style={{ fontSize: 11, lineHeight: 1.5 }}>{lens === "eng" ? <><span style={{ color: C.amber }}>interrupt</span> → Command(resume) → <span style={{ color: C.teal }}>BEGIN; promote → durable; embed memory; COMMIT</span></> : <span style={{ fontFamily: "Inter, sans-serif" }}>On commit, working state becomes permanent records the next run can read.</span>}</span>
      </div>

      {/* long-term */}
      <div className="lbl" style={{ fontSize: 9.5, color: C.teal, fontWeight: 700, marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}><Database size={13} /> Long-term · durable + vector</div>
      <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
        {Object.entries(TABLES).map(([k, v]) => { const Icon = v.icon; const on = tab === k; return (
          <button key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 7, border: `1px solid ${on ? C.ink : C.line}`, background: on ? C.ink : "#fff", color: on ? "#fff" : C.sub, borderRadius: 9, padding: "8px 13px", fontSize: 11.5, fontWeight: 600 }}><Icon size={13} /><span className={lens === "eng" ? "mono" : "disp"} style={{ fontSize: lens === "eng" ? 11 : 12 }}>{lens === "eng" ? k : v.plain}</span></button>
        ); })}
      </div>
      <div className="scr" style={{ overflowX: "auto", border: `1px solid ${C.line}`, borderRadius: 11, marginBottom: 16 }}>
        {t.rows.length === 0 ? (
          <div style={{ padding: "26px 16px", textAlign: "center", color: C.sub, fontSize: 12 }}>No rows yet — commit on Review to write decisions here.</div>
        ) : (
          <table>
            <thead><tr style={{ background: "#FAFBFC" }}>{cols.map((c) => <th key={c.k} style={{ padding: "9px 14px", borderBottom: `1px solid ${C.line}` }}><div className="lbl" style={{ fontSize: 9, color: C.sub, fontWeight: 700 }}>{lens === "eng" ? c.k : c.plain}</div>{lens === "eng" && <Type t={c.t || "text"} />}</th>)}</tr></thead>
            <tbody>{t.rows.map((r, i) => (
              <tr key={i} className="inn" style={{ background: r._new ? C.tealSoft : r._expired ? "#FBFBFC" : "#fff", opacity: r._expired ? 0.62 : 1 }}>
                {cols.map((c) => (
                  <td key={c.k} style={{ padding: "10px 14px", borderBottom: i < t.rows.length - 1 ? "1px solid #F1F3F6" : "none" }}>
                    {c.t === "bool" ? <Bool v={r[c.k]} /> : <span className={c.k.endsWith("_id") || ["numeric", "int", "date"].includes(c.t) ? "mono" : ""} style={{ fontSize: c.k.endsWith("_id") ? 10.5 : 11.5, fontWeight: c.em ? 700 : 500, color: c.em ? C.amber : c.k.endsWith("_id") ? C.blue : C.text }}>{String(r[c.k])}</span>}
                    {lens === "plan" && r._new && c === cols[cols.length - 1] && <span className="lbl" style={{ marginLeft: 8, fontSize: 8, color: C.teal, background: "#fff", border: `1px solid ${C.tealLine}`, padding: "1px 6px", borderRadius: 5 }}>just added</span>}
                  </td>
                ))}
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>

      {/* memory */}
      <div style={{ background: lens === "eng" ? C.ink : C.card, border: `1px solid ${lens === "eng" ? C.inkLine : C.line}`, borderRadius: 13, padding: 16, color: lens === "eng" ? C.inkText : C.text }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}><Brain size={15} color={C.violet} /><span className="mono" style={{ fontSize: 12, fontWeight: 600, color: lens === "eng" ? C.inkText : C.text }}>{lens === "eng" ? "agent_memory" : "What the assistant remembers"}</span>{lens === "eng" && <span className="lbl" style={{ fontSize: 8.5, color: C.violet, background: C.violetSoft, padding: "2px 6px", borderRadius: 5 }}>vector(1024) · hnsw</span>}</div>
        {lens === "eng" && <pre className="mono scr" style={{ margin: "0 0 12px", fontSize: 10.5, lineHeight: 1.6, color: C.inkText, overflowX: "auto" }}>{`SELECT decision_id, summary,
       1 - (embedding <=> :q) AS similarity
FROM agent_memory WHERE 'shortage' = ANY(tags)
ORDER BY embedding <=> :q LIMIT 3;`}</pre>}
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {MEM.map((m) => (
            <div key={m.id} className="inn" style={{ border: `1px solid ${lens === "eng" ? C.inkLine : C.line}`, background: m.new ? (lens === "eng" ? "rgba(11,154,130,.1)" : C.tealSoft) : (lens === "eng" ? C.inkRaise : "#FBFCFD"), borderRadius: 10, padding: "11px 13px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5, flexWrap: "wrap" }}>
                {lens === "eng" && <span className="mono" style={{ fontSize: 10, color: C.blue }}>id {m.id}</span>}
                {m.new ? <span className="lbl" style={{ fontSize: 8, fontWeight: 700, color: C.teal, background: lens === "eng" ? "rgba(11,154,130,.16)" : "#fff", border: `1px solid ${C.tealLine}`, padding: "2px 7px", borderRadius: 5 }}>{lens === "eng" ? "written this run" : "saved today"}</span>
                  : <span className="mono" style={{ fontSize: 10.5, fontWeight: 700, color: m.sim >= 0.9 ? C.teal : C.amber }}>{lens === "eng" ? `sim ${m.sim}` : `${Math.round(m.sim * 100)}% match`}</span>}
                <span className="mono" style={{ marginLeft: "auto", fontSize: 9.5, color: lens === "eng" ? C.inkSub : C.sub }}>{m.outcome}</span>
              </div>
              <div style={{ fontSize: 11.5, color: lens === "eng" ? C.inkText : C.text, lineHeight: 1.45 }}>{m.summary}</div>
              {lens === "eng" && <div className="mono" style={{ fontSize: 9.5, color: C.violet, opacity: 0.8, marginTop: 6 }}>{m.vec}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ============================ TRACE ============================ */
function Metric({ icon: Icon, label, value, tone }) {
  return <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 14px", background: C.inkRaise, borderRadius: 10, border: `1px solid ${C.inkLine}`, minWidth: 118 }}><Icon size={15} color={tone || C.inkSub} /><div><div className="lbl" style={{ fontSize: 8.5, color: C.inkSub, fontWeight: 700 }}>{label}</div><div className="disp" style={{ fontSize: 15, fontWeight: 700, color: tone || C.inkText }}>{value}</div></div></div>;
}
function Tracepage({ state }) {
  const { committed } = state;
  const [sel, setSel] = useState("planner");
  const visible = SPANS.filter((s) => s.phase === 1 || committed);
  const span = (visible.find((s) => s.id === sel)) || SPANS.find((s) => s.id === "planner");
  const ticks = [0, 1000, 2000, 3000, 4000];
  const k = KIND[span.kind]; const SIcon = k.icon;
  return (
    <div className="inn">
      <PageHead kicker="MLflow trace · run_8841" title="Prove it ran"
        sub="Span waterfall with the parallel gather and the human interrupt. Click any span for its detail."
        right={<span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: C.teal, fontWeight: 600 }}><Check size={13} /> {committed ? "OK" : "in progress"}</span>} />

      <div className="scr" style={{ display: "flex", gap: 10, marginBottom: 16, overflowX: "auto" }}>
        <Metric icon={Clock} label="Agent latency" value={committed ? "5.90s" : "4.82s"} />
        <Metric icon={Lock} label="Human review" value={committed ? "6m 12s" : "ongoing"} tone={C.amber} />
        <Metric icon={Cpu} label="Tokens" value={committed ? "20.5K" : "17.9K"} tone={C.violet} />
        <Metric icon={Coins} label="Cost" value={committed ? "$0.14" : "$0.12"} />
        <Metric icon={Layers} label="Spans" value={String(visible.length)} />
        <Metric icon={Zap} label="Parallel" value="3" tone={C.teal} />
      </div>

      {/* waterfall */}
      <div style={{ background: C.ink, borderRadius: 14, padding: "16px 16px 18px", border: `1px solid ${C.inkLine}`, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14, flexWrap: "wrap" }}>
          <span className="lbl" style={{ fontSize: 9.5, color: C.inkSub, fontWeight: 700 }}>Waterfall</span>
          <div style={{ display: "flex", gap: 11, flexWrap: "wrap", marginLeft: "auto" }}>{Object.values(KIND).map((kk) => <span key={kk.label} style={{ display: "flex", alignItems: "center", gap: 5 }}><span style={{ width: 9, height: 9, borderRadius: 3, background: kk.c }} /><span className="lbl" style={{ fontSize: 8, color: C.inkSub, fontWeight: 700 }}>{kk.label}</span></span>)}</div>
        </div>
        <div className="scr" style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 640 }}>
            <div style={{ display: "flex", marginBottom: 8 }}>
              <div style={{ width: 188, flexShrink: 0 }} />
              <div style={{ position: "relative", flex: 1, height: 14 }}>{ticks.map((t) => <span key={t} className="mono" style={{ position: "absolute", left: `${pctT(t)}%`, fontSize: 9, color: C.inkSub, transform: "translateX(-50%)" }}>{t / 1000}s</span>)}</div>
            </div>
            <div style={{ position: "relative" }}>
              <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(188px + (100% - 188px) * ${GAP[0] / T_TOTAL})`, width: `calc((100% - 188px) * ${(GAP[1] - GAP[0]) / T_TOTAL})`, background: "repeating-linear-gradient(45deg, rgba(221,130,8,.14), rgba(221,130,8,.14) 6px, transparent 6px, transparent 12px)", borderLeft: `1px dashed ${C.amber}`, borderRight: `1px dashed ${C.amber}` }} />
              {visible.map((sp) => {
                const kk = KIND[sp.kind]; const active = span.id === sp.id;
                return (
                  <button key={sp.id} onClick={() => setSel(sp.id)} className="inn" style={{ position: "relative", zIndex: 1, display: "flex", alignItems: "center", width: "100%", border: "none", background: active ? "rgba(255,255,255,.05)" : "transparent", borderRadius: 7, padding: "3px 0", textAlign: "left" }}>
                    <span style={{ width: 188, flexShrink: 0, paddingLeft: 6 + sp.depth * 13, display: "flex", alignItems: "center", gap: 6, overflow: "hidden" }}>
                      <span style={{ width: 5, height: 5, borderRadius: 6, background: kk.c, flexShrink: 0 }} />
                      <span className="mono" style={{ fontSize: 10, color: active ? "#fff" : C.inkText, fontWeight: active ? 600 : 400, whiteSpace: "nowrap", textOverflow: "ellipsis", overflow: "hidden" }}>{sp.name}</span>
                    </span>
                    <span style={{ position: "relative", flex: 1, height: 22 }}>
                      <span style={{ position: "absolute", top: 4, height: 14, left: `${pctT(sp.start)}%`, width: `${Math.max(pctT(sp.dur), 1.2)}%`, background: kk.c, borderRadius: 4, opacity: active ? 1 : 0.82, boxShadow: active ? "0 0 0 2px rgba(255,255,255,.5)" : "none", display: "flex", alignItems: "center", paddingLeft: 6 }}>
                        <span className="mono" style={{ fontSize: 8.5, color: "rgba(255,255,255,.92)", whiteSpace: "nowrap" }}>{msf(sp.dur)}</span>
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            <div style={{ display: "flex", marginTop: 6 }}>
              <div style={{ width: 188, flexShrink: 0 }} />
              <div style={{ position: "relative", flex: 1, height: 16 }}>
                <span style={{ position: "absolute", left: `${pctT(GAP[0])}%`, display: "flex", alignItems: "center", gap: 5, transform: "translateX(-46%)" }}><Lock size={11} color={C.amber} /><span className="lbl" style={{ fontSize: 8, color: C.amber, fontWeight: 700, whiteSpace: "nowrap" }}>human review {committed ? "· 6m 12s" : "· awaiting"}</span></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* span detail */}
      <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 14, padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14, flexWrap: "wrap" }}>
          <span style={{ display: "grid", placeItems: "center", width: 28, height: 28, borderRadius: 8, background: k.s, color: k.c }}><SIcon size={15} /></span>
          <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{span.name}</span>
          <span className="lbl" style={{ fontSize: 8.5, fontWeight: 700, color: k.c, background: k.s, padding: "2px 7px", borderRadius: 5 }}>{k.label}</span>
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}><span className="mono" style={{ fontSize: 11, color: C.sub }}>{msf(span.dur)}</span><span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: C.teal, fontWeight: 600 }}><Check size={12} /> OK</span></span>
        </div>
        <div className="two" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
          <div><div className="lbl" style={{ fontSize: 8.5, color: C.sub, fontWeight: 700, marginBottom: 5 }}>Input</div><div style={{ background: "#FAFBFC", border: `1px solid ${C.line}`, borderRadius: 8, padding: "9px 11px", fontSize: 11.5, lineHeight: 1.5 }}>{span.input}</div></div>
          <div><div className="lbl" style={{ fontSize: 8.5, color: C.sub, fontWeight: 700, marginBottom: 5 }}>Output</div><div style={{ background: "#FAFBFC", border: `1px solid ${C.line}`, borderRadius: 8, padding: "9px 11px", fontSize: 11.5, lineHeight: 1.5 }}>{span.output}</div></div>
        </div>
        <div className="lbl" style={{ fontSize: 8.5, color: C.sub, fontWeight: 700, marginBottom: 7 }}>Attributes</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>{span.attrs.map(([kk, vv]) => <span key={kk} style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "#F4F6F9", border: `1px solid ${C.line}`, borderRadius: 7, padding: "4px 9px" }}><span className="mono" style={{ fontSize: 10, color: C.sub }}>{kk}</span><span className="mono" style={{ fontSize: 10.5, fontWeight: 600 }}>{vv}</span></span>)}</div>
      </div>

      {/* assessments */}
      <div style={{ background: C.ink, color: C.inkText, borderRadius: 14, padding: 16, border: `1px solid ${C.inkLine}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}><ShieldCheck size={15} color={C.teal} /><span className="disp" style={{ fontSize: 14, fontWeight: 700 }}>Assessments on this trace</span><span className="mono" style={{ fontSize: 9.5, color: C.inkSub, marginLeft: "auto" }}>mlflow.log_feedback</span></div>
        <p style={{ fontSize: 10.5, color: C.inkSub, margin: "0 0 14px" }}>The planner's call becomes labeled data for eval.</p>
        <div style={{ background: C.inkRaise, border: `1px solid ${C.inkLine}`, borderRadius: 10, padding: "11px 13px", marginBottom: 12, opacity: committed ? 1 : 0.5 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}><User size={12} color={C.amber} /><span className="lbl" style={{ fontSize: 8.5, fontWeight: 700, color: C.amber }}>human feedback</span><span className="mono" style={{ fontSize: 9.5, color: C.inkSub, marginLeft: "auto" }}>{committed ? "a.miller" : "pending"}</span></div>
          <div style={{ fontSize: 11.5, color: C.inkText, lineHeight: 1.5 }}>{committed ? "Approved actions · mirrored Oct precedent, split-source 70/30, expedite within 12%." : "Awaiting the planner's decision on Review."}</div>
        </div>
        <div className="lbl" style={{ fontSize: 8.5, color: C.inkSub, fontWeight: 700, marginBottom: 7 }}>automated eval · pre-human gate</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {[["grounded_in_evidence", "all actions trace to a source"], ["cited_prior_memory", "referenced decision 6602"], ["cost_within_ceiling", "+$287K < $350K"]].map(([l, d]) => (
            <div key={l} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 11px", background: "rgba(11,154,130,.1)", border: "1px solid rgba(11,154,130,.3)", borderRadius: 9 }}>
              <span style={{ display: "grid", placeItems: "center", width: 18, height: 18, borderRadius: 6, background: C.teal, flexShrink: 0 }}><Check size={12} color="#06231D" /></span>
              <span className="mono" style={{ fontSize: 11, color: C.inkText, fontWeight: 600 }}>{l}</span>
              <span style={{ fontSize: 10.5, color: C.inkSub, marginLeft: "auto", textAlign: "right" }}>{d}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ============================ APP ============================ */
export default function App() {
  const [page, setPage] = useState("overview");
  const [committed, setCommitted] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [ss, setSs] = useState(3000);
  const [rationale, setRationale] = useState("");
  const [actions, setActions] = useState({
    expedite: { status: "approved", qty: 6000 }, buffer: { status: "approved", qty: 2500 },
    safety: { status: "approved" }, alloc: { status: "pending" },
  });
  const timers = useRef([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const writes = useMemo(() => {
    const o = [];
    if (actions.safety.status === "approved") o.push({ key: "safety", op: "UPDATE", table: "planning_parameters", summary: `PM-IG1200 safety_stock 1800 → ${ss}` });
    if (actions.expedite.status === "approved") o.push({ key: "expedite", op: "INSERT", table: "approved_actions", summary: `expedite PO-44817 · ${fmt(actions.expedite.qty)} u` });
    if (actions.buffer.status === "approved") o.push({ key: "buffer", op: "INSERT", table: "approved_actions", summary: `buffer PO Kyoto · ${fmt(actions.buffer.qty)} u` });
    if (actions.alloc.status === "approved") o.push({ key: "alloc", op: "INSERT", table: "constraints", summary: "allocation: INV-7 > spares" });
    return o;
  }, [actions, ss]);

  const costDelta = (actions.expedite.status === "approved" ? actions.expedite.qty * UNIT * 0.12 : 0) + (actions.buffer.status === "approved" ? actions.buffer.qty * UNIT * 0.08 : 0);

  const doCommit = () => {
    setCommitting(true);
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    timers.current.push(setTimeout(() => { setCommitting(false); setCommitted(true); }, reduce ? 200 : 1500));
  };
  const reset = () => { setCommitted(false); setCommitting(false); setRationale(""); setSs(3000); setActions({ expedite: { status: "approved", qty: 6000 }, buffer: { status: "approved", qty: 2500 }, safety: { status: "approved" }, alloc: { status: "pending" } }); setPage("overview"); };

  const shared = { actions, setActions, ss, setSs, rationale, setRationale, committed, committing, doCommit, writes, costDelta };

  return (
    <div className="ap shell" style={{ display: "grid", gridTemplateColumns: "224px 1fr", background: C.surface, color: C.text, minHeight: "100vh" }}>
      <Style />
      <NavSide page={page} setPage={setPage} committed={committed} reset={reset} />
      <main className="scr" style={{ overflowY: "auto", maxHeight: "100vh" }}>
        <div style={{ maxWidth: 1040, margin: "0 auto", padding: "24px 24px 48px" }} key={page}>
          {page === "overview" && <Overview setPage={setPage} committed={committed} />}
          {page === "review" && <Review state={shared} setPage={setPage} />}
          {page === "state" && <StatePage state={shared} />}
          {page === "trace" && <Tracepage state={shared} />}
        </div>
      </main>
    </div>
  );
}
