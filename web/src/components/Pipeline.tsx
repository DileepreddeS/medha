import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { MedhaEvent } from "../api";
import { fmtMs, fmtTok, fmtUsd } from "../lib/format";

type Status = "idle" | "active" | "done" | "fail" | "skip" | "warn";

interface Stage {
  key: string;
  label: string;
  status: Status;
  detail: string;
  tok: number;
  usd: number;
  ms: number;
}

const byType = (events: MedhaEvent[], t: string) => events.filter((e) => e.type === t);
const sumTok = (es: MedhaEvent[]) => es.reduce((a, e) => a + e.tokens_in + e.tokens_out, 0);
const sumUsd = (es: MedhaEvent[]) => es.reduce((a, e) => a + e.cost_usd, 0);
const sumMs = (es: MedhaEvent[]) => es.reduce((a, e) => a + e.latency_ms, 0);

function num(v: unknown, d = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : d;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function deriveStages(events: MedhaEvent[]): { stages: Stage[]; reused: boolean; totals: { tok: number; usd: number } } {
  const planned = byType(events, "task.planned");
  const routed = byType(events, "task.routed");
  const lastRoute = routed[routed.length - 1];
  const outcome = lastRoute ? str(lastRoute.payload["outcome"]) : "";
  const reused = outcome === "hit";
  const composed = outcome === "compose";

  const authors = events.filter((e) => ["llm.author", "llm.composer", "llm.repairer"].includes(e.type));
  const registered = byType(events, "skill.registered");
  const repaired = events.some((e) => e.type === "llm.repairer" || e.type === "repair.attempt");
  const vis = byType(events, "gate.visible");
  const hold = byType(events, "gate.holdout");
  const judge = byType(events, "gate.judge");
  const done = byType(events, "task.done");
  const failed = byType(events, "task.failed");
  const apprReq = byType(events, "approval.requested");
  const apprDec = byType(events, "approval.decided");

  const totals = { tok: sumTok(events), usd: sumUsd(events) };
  const stages: Stage[] = [];

  // Route
  {
    const es = [...planned, ...byType(events, "route.candidates"), ...routed];
    let status: Status = "idle";
    let detail = "waiting for task";
    if (events.length > 0) {
      status = "active";
      detail = "routing…";
    }
    if (lastRoute) {
      status = "done";
      const rel = num(lastRoute.payload["relevance"]);
      detail = `${outcome}${rel > 0 ? ` · rel ${rel.toFixed(2)}` : ""}`;
      const reason = str(lastRoute.payload["reason"]);
      if (reason) detail += ` — ${reason.slice(0, 90)}`;
    }
    stages.push({ key: "route", label: "Route", status, detail, tok: sumTok(es), usd: sumUsd(es), ms: sumMs(es) });
  }

  // Learn / Repair / Compose
  {
    let label = "Learn";
    if (repaired) label = "Repair";
    else if (composed || authors.some((e) => e.type === "llm.composer")) label = "Compose";
    let status: Status = reused ? "skip" : outcome && outcome !== "hit" && authors.length === 0 ? "active" : "idle";
    let detail = reused ? "skill reused — no authoring" : "authoring skill…";
    if (registered.length > 0) {
      status = "done";
      const v = registered[registered.length - 1];
      detail = `v${String(v.version ?? "?")} registered`;
      if (repaired) detail += " · drift fixed";
    } else if (failed.length > 0) {
      status = "fail";
      detail = "no verified skill — see result";
    }
    const es = [...authors, ...byType(events, "learn.attempt"), ...byType(events, "repair.attempt"), ...registered];
    stages.push({ key: "learn", label, status, detail, tok: sumTok(es), usd: sumUsd(es), ms: sumMs(es) });
  }

  // Test
  {
    let status: Status = reused ? "skip" : vis.length > 0 || hold.length > 0 ? "active" : "idle";
    let detail = reused ? "verified at learn time" : "awaiting candidate";
    const parts: string[] = [];
    const lastVis = vis[vis.length - 1];
    const lastHold = hold[hold.length - 1];
    if (lastVis) {
      const p = lastVis.payload;
      const np = Array.isArray(p["passed"]) ? p["passed"].length : 0;
      const nf = Array.isArray(p["failed"]) ? p["failed"].length : 0;
      parts.push(`visible ${np}/${np + nf}`);
      if (nf > 0) status = "fail";
    }
    if (lastHold) {
      const p = lastHold.payload;
      const rate = num(p["rate"]);
      const overfit = p["overfit"] === true;
      parts.push(`holdout ${(rate * 100).toFixed(0)}%`);
      if (rate > 0 && rate < 0.8) status = "fail";
      if (overfit) status = "fail";
    }
    if (parts.length > 0) {
      detail = parts.join(" · ");
      if (status === "active") status = "done";
    }
    const es = [...byType(events, "gate.static"), ...byType(events, "llm.testwriter"), ...vis, ...hold];
    stages.push({ key: "test", label: "Test", status, detail, tok: sumTok(es), usd: sumUsd(es), ms: sumMs(es) });
  }

  // Judge
  {
    let status: Status = reused ? "skip" : judge.length > 0 ? "done" : vis.length > 0 ? "active" : "idle";
    let detail = reused ? "judged at learn time" : status === "active" ? "evaluating…" : "awaiting tests";
    const last = judge[judge.length - 1];
    if (last) {
      const pass = last.payload["overall_pass"] === true;
      status = pass ? "done" : "fail";
      const verdicts = Array.isArray(last.payload["verdicts"]) ? last.payload["verdicts"] : [];
      const np = verdicts.filter((v) => (v as Record<string, unknown>)["passed"] === true).length;
      detail = `${np}/${verdicts.length} semantic checks`;
    }
    const es = [...judge];
    stages.push({ key: "judge", label: "Judge", status, detail, tok: sumTok(es), usd: sumUsd(es), ms: sumMs(es) });
  }

  // Execute
  {
    let status: Status = "idle";
    let detail = "awaiting verdict";
    if (apprReq.length > apprDec.length) {
      status = "warn";
      detail = "waiting for approval";
    }
    if (done.length > 0) {
      status = "done";
      const n = num(done[done.length - 1].payload["checks"]);
      detail = n > 0 ? `${n} checks green` : "done";
    } else if (failed.length > 0) {
      status = "fail";
      detail = str(failed[failed.length - 1].payload["detail"]).slice(0, 90) || "failed";
    } else if (status === "idle" && (registered.length > 0 || reused)) {
      status = "active";
      detail = "executing…";
    }
    stages.push({ key: "exec", label: "Execute", status, detail, tok: 0, usd: 0, ms: 0 });
  }

  return { stages, reused, totals };
}

const DOT: Record<Status, string> = {
  idle: "bg-zinc-700",
  active: "bg-cyan-400",
  done: "bg-emerald-400",
  fail: "bg-rose-400",
  skip: "bg-zinc-800",
  warn: "bg-amber-400",
};

function Mark({ status, reduce }: { status: Status; reduce: boolean }) {
  if (status === "done")
    return (
      <motion.span
        initial={reduce ? false : { scale: 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 22 }}
        className="text-emerald-400 text-sm leading-none"
      >
        ✓
      </motion.span>
    );
  if (status === "fail")
    return (
      <motion.span
        initial={reduce ? false : { scale: 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 22 }}
        className="text-rose-400 text-sm leading-none"
      >
        ✗
      </motion.span>
    );
  return null;
}

export default function Pipeline({ events, elapsedMs }: { events: MedhaEvent[]; elapsedMs: number | null }) {
  const reduce = useReducedMotion() ?? false;
  const { stages, reused, totals } = deriveStages(events);
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">Pipeline</h2>
        <span className="text-xs text-zinc-500">
          {fmtTok(totals.tok)} tok · {fmtUsd(totals.usd)}
          {elapsedMs != null && ` · ${fmtMs(elapsedMs)}`}
        </span>
      </div>
      <AnimatePresence>
        {reused && (
          <motion.div
            initial={reduce ? false : { scale: 0.85, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ type: "spring", stiffness: 380, damping: 20 }}
            className="mt-3 rounded-lg border border-cyan-400/40 bg-cyan-400/10 px-4 py-3 text-center"
          >
            <span className="text-base font-semibold text-cyan-300">
              ⚡ Reused skill — {fmtUsd(totals.usd)}
              {elapsedMs != null && `, ${fmtMs(elapsedMs)}`}
            </span>
          </motion.div>
        )}
      </AnimatePresence>
      <ol className="mt-3 space-y-2">
        {stages.map((s, i) => (
          <motion.li
            key={s.key}
            layout={!reduce}
            initial={false}
            animate={{ opacity: s.status === "idle" ? 0.45 : 1 }}
            className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 ${
              s.status === "active"
                ? "border-cyan-400/50 bg-zinc-900"
                : s.status === "fail"
                  ? "border-rose-400/40 bg-zinc-900"
                  : "border-zinc-800 bg-zinc-900/60"
            }`}
          >
            <span className="flex flex-col items-center pt-0.5">
              <span className={`h-2.5 w-2.5 rounded-full ${DOT[s.status]} ${s.status === "active" && !reduce ? "animate-pulse" : ""}`} />
              {i < stages.length - 1 && <span className="mt-1 w-px flex-1 bg-zinc-800" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-100">{s.label}</span>
                <Mark status={s.status} reduce={reduce} />
                {s.status === "skip" && <span className="text-[11px] text-zinc-500">skipped</span>}
                {s.status === "active" && <span className="text-[11px] text-cyan-300">running</span>}
              </div>
              <p className="mt-0.5 truncate text-xs text-zinc-400" title={s.detail}>
                {s.detail}
              </p>
            </div>
            {(s.tok > 0 || s.usd > 0 || s.ms > 0) && (
              <span className="shrink-0 text-[11px] text-zinc-500">
                {s.tok > 0 && `${fmtTok(s.tok)}t `}
                {s.usd > 0 && `${fmtUsd(s.usd)} `}
                {s.ms > 0 && fmtMs(s.ms)}
              </span>
            )}
          </motion.li>
        ))}
      </ol>
    </div>
  );
}
