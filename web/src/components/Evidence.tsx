import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ActivityPoint, BenchRow, BenchRun } from "../api";
import { useCountUp } from "../lib/useCountUp";

interface Props {
  stats: Record<string, unknown> | null;
  runs: BenchRun[];
  activity: ActivityPoint[];
  lessons: Record<string, unknown>[];
  onDeleteLesson: (id: number) => void;
}

const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);

function latestWith(runs: BenchRun[], suiteSuffix: string, config: string, author?: string): BenchRow[] | null {
  const sorted = [...runs].sort((a, b) => (a.ts < b.ts ? 1 : -1));
  for (const r of sorted) {
    if (author && r.author !== author) continue;
    if (r.suite.endsWith(suiteSuffix) && r.configs[config]) return r.configs[config].rows;
  }
  return null;
}

function firstRepeat(rows: BenchRow[]): { first: BenchRow[]; repeat: BenchRow[] } {
  const seen = new Set<string>();
  const first: BenchRow[] = [];
  const repeat: BenchRow[] = [];
  for (const r of rows) {
    if (seen.has(r.family)) repeat.push(r);
    else {
      seen.add(r.family);
      first.push(r);
    }
  }
  return { first, repeat };
}

const mean = (rs: BenchRow[], k: "tokens" | "latency_ms"): number =>
  rs.length > 0 ? rs.reduce((a, r) => a + r[k], 0) / rs.length : 0;

const AXIS = { fill: "#a1a1aa", fontSize: 11 };
const TIP = {
  backgroundColor: "#18181b",
  border: "1px solid #3f3f46",
  borderRadius: 8,
  fontSize: 12,
  color: "#f4f4f5",
};

function Headline({ label, value, format }: { label: string; value: number; format: (n: number) => string }) {
  const v = useCountUp(value);
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/70 px-4 py-3">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-zinc-100">{format(v)}</p>
    </div>
  );
}

function Panel({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-4">
      <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
      {hint && <p className="mt-0.5 text-xs text-zinc-500">{hint}</p>}
      <div className="mt-3 h-56">{children}</div>
    </section>
  );
}

const Empty = ({ what }: { what: string }) => (
  <div className="flex h-56 items-center justify-center text-sm text-zinc-500">No {what} yet — run the bench to populate.</div>
);

export default function Evidence(p: Props) {
  const mini = useMemo(() => {
    const out: { cfg: string; rate: number }[] = [];
    const rate = (rows: BenchRow[]) => rows.filter((r) => r.passed).length / rows.length;
    const a = latestWith(p.runs, "mini.yaml", "a");
    const b = latestWith(p.runs, "mini.yaml", "b");
    const cs = latestWith(p.runs, "mini.yaml", "c", "meta");
    const cg = latestWith(p.runs, "mini.yaml", "c", "glimmer");
    if (a) out.push({ cfg: "A", rate: rate(a) });
    if (b) out.push({ cfg: "B", rate: rate(b) });
    if (cs) out.push({ cfg: "C·Spark", rate: rate(cs) });
    if (cg) out.push({ cfg: "C·Glimmer", rate: rate(cg) });
    return out;
  }, [p.runs]);

  const econ = useMemo(() => {
    const a = latestWith(p.runs, "econ.yaml", "a");
    const c = latestWith(p.runs, "econ.yaml", "c");
    if (!a || !c) return null;
    const n = Math.min(a.length, c.length);
    const perTask = Array.from({ length: n }, (_, i) => ({
      i: i + 1,
      A: a[i].tokens,
      C: c[i].tokens,
      Alat: Math.round(a[i].latency_ms / 1000),
      Clat: Math.round(c[i].latency_ms / 1000),
    }));
    const ar = firstRepeat(a).repeat;
    const hits = firstRepeat(c).repeat.filter((r) => r.reused);
    const base = hits.length > 0 ? hits : firstRepeat(c).repeat;
    return { perTask, speedup: mean(ar, "latency_ms") / Math.max(1, mean(base, "latency_ms")) };
  }, [p.runs]);

  const repair = useMemo(() => {
    const sorted = [...p.runs].sort((a, b) => (a.ts < b.ts ? 1 : -1));
    const run =
      sorted.find((r) => r.suite.endsWith("repair.yaml") && r.configs["repair"]) ??
      sorted.find((r) => r.configs["repair"]);
    if (!run) return null;
    return run.configs["repair"].rows.map((r) => ({ id: r.id.replace("drift_", "").replace(/_/g, " "), pass: r.passed ? 1 : 0 }));
  }, [p.runs]);

  const growth = useMemo(
    () =>
      p.activity.map((pt, i) => ({
        i,
        skills: pt.skills,
        runs: pt.runs,
        ts: pt.ts.slice(11, 16),
      })),
    [p.activity]
  );

  const reuseRate = num(p.stats?.["reuse_rate"]);
  const nSkills = num(p.stats?.["skills"]);
  const miniCRate = mini.find((m) => m.cfg === "C·Spark")?.rate ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Headline label="Reuse rate (live)" value={reuseRate} format={(n) => `${Math.round(n * 100)}%`} />
        <Headline label="Skills in library" value={nSkills} format={(n) => String(Math.round(n))} />
        <Headline label="Mini pass, C·Spark" value={miniCRate} format={(n) => `${Math.round(n * 100)}%`} />
        <Headline label="HIT repeat speedup" value={econ?.speedup ?? 0} format={(n) => `${n.toFixed(1)}×`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Pass rate by config" hint="mini MedhaBench, 12 tasks">
          {mini.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={mini}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="cfg" tick={AXIS} axisLine={{ stroke: "#3f3f46" }} tickLine={false} />
                <YAxis domain={[0, 1]} tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
                <Tooltip contentStyle={TIP} formatter={(v) => [`${Math.round(Number(v) * 100)}%`, "pass"]} />
                <Bar dataKey="rate" fill="#22d3ee" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty what="mini runs" />
          )}
        </Panel>

        <Panel title="Repair suite" hint="one bar per drift case">
          {repair ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={repair}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="id" tick={AXIS} axisLine={{ stroke: "#3f3f46" }} tickLine={false} interval={0} fontSize={10} />
                <YAxis domain={[0, 1]} hide />
                <Tooltip contentStyle={TIP} formatter={(v) => [Number(v) === 1 ? "pass" : "fail", "outcome"]} />
                <Bar dataKey="pass" fill="#34d399" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty what="repair runs" />
          )}
        </Panel>

        <Panel title="Tokens per task over the sequence" hint="econ suite: A pays full price every task, C drops on reuse">
          {econ ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={econ.perTask}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="i" tick={AXIS} axisLine={{ stroke: "#3f3f46" }} tickLine={false} label={{ value: "task", fill: "#71717a", fontSize: 11 }} />
                <YAxis tick={AXIS} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TIP} />
                <Line type="monotone" dataKey="A" stroke="#71717a" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="C" stroke="#22d3ee" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Empty what="econ runs" />
          )}
        </Panel>

        <Panel title="Latency per task (s)" hint="same sequence, wall clock">
          {econ ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={econ.perTask}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="i" tick={AXIS} axisLine={{ stroke: "#3f3f46" }} tickLine={false} />
                <YAxis tick={AXIS} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TIP} />
                <Line type="monotone" dataKey="Alat" name="A" stroke="#71717a" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="Clat" name="C" stroke="#22d3ee" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Empty what="econ runs" />
          )}
        </Panel>
      </div>

      <Panel title="Library growth" hint="cumulative skills and runs from the live event log">
        {growth.length > 1 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={growth}>
              <CartesianGrid stroke="#27272a" vertical={false} />
              <XAxis dataKey="ts" tick={AXIS} axisLine={{ stroke: "#3f3f46" }} tickLine={false} minTickGap={40} />
              <YAxis tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip contentStyle={TIP} />
              <Area type="stepAfter" dataKey="skills" name="skills" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.15} />
              <Area type="stepAfter" dataKey="runs" name="runs" stroke="#34d399" fill="#34d399" fillOpacity={0.1} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <Empty what="activity" />
        )}
      </Panel>

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-4">
        <h3 className="text-sm font-semibold text-zinc-200">Lessons ({p.lessons.length})</h3>
        <ul className="mt-2 space-y-1 text-xs text-zinc-400">
          {p.lessons.map((l) => (
            <li key={String(l.id)} className="flex items-start gap-2">
              <span className="flex-1">
                [{String(l.scope)}] {String(l.trigger_text)} → {String(l.guidance)}
              </span>
              <button onClick={() => p.onDeleteLesson(l.id as number)} className="text-zinc-500 underline underline-offset-2 hover:text-zinc-300">
                delete
              </button>
            </li>
          ))}
          {p.lessons.length === 0 && <li className="text-zinc-500">No lessons recorded.</li>}
        </ul>
      </section>
    </div>
  );
}
