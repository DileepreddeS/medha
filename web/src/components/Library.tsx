import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { SkillDetail, SkillSummary } from "../api";
import { api } from "../api";
import { shortId } from "../lib/format";
import { diffLines } from "../lib/diff";

interface Props {
  skills: SkillSummary[];
  detail: SkillDetail | null;
  setDetail: (d: SkillDetail | null) => void;
  setError: (e: string) => void;
  repairingId: string | null;
  repairedId: string | null;
}

const TRUST: Record<string, string> = {
  candidate: "bg-zinc-700 text-zinc-200",
  verified: "bg-cyan-400/15 text-cyan-300 border border-cyan-400/40",
  trusted: "bg-emerald-400/15 text-emerald-300 border border-emerald-400/40",
};

function Card({
  s,
  selected,
  repairing,
  repaired,
  reduce,
  onClick,
}: {
  s: SkillSummary;
  selected: boolean;
  repairing: boolean;
  repaired: boolean;
  reduce: boolean;
  onClick: () => void;
}) {
  const pct = Math.round(s.expected_p * 100);
  return (
    <motion.li
      layout={!reduce}
      initial={reduce ? false : { opacity: 0, y: 14 }}
      animate={
        repairing && !reduce
          ? { opacity: 1, y: 0, x: [0, -7, 7, -5, 5, 0] }
          : { opacity: 1, y: 0, x: 0 }
      }
      transition={repairing && !reduce ? { duration: 0.5, repeat: 2 } : { type: "spring", stiffness: 320, damping: 26 }}
    >
      <button
        onClick={onClick}
        className={`w-full rounded-lg border p-3 text-left transition-colors ${
          repairing
            ? "border-amber-400/70 bg-amber-400/5"
            : repaired
              ? "border-emerald-400/60 bg-emerald-400/5"
              : selected
                ? "border-cyan-400/60 bg-zinc-900"
                : "border-zinc-800 bg-zinc-900/70 hover:border-zinc-600"
        }`}
      >
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm text-zinc-100" title={s.id}>
            {shortId(s.id)}
          </span>
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${TRUST[s.trust] ?? TRUST.candidate}`}>
            {s.trust}
          </span>
          {repairing && <span className="shrink-0 text-[11px] text-amber-300">repairing…</span>}
          {repaired && !repairing && <span className="shrink-0 text-[11px] text-emerald-300">repaired — replay passed</span>}
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
          <motion.div
            initial={false}
            animate={{ width: `${pct}%` }}
            transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 120, damping: 20 }}
            className="h-full rounded-full bg-cyan-400"
          />
        </div>
        <p className="mt-1.5 text-[11px] text-zinc-500">
          {pct}% success · v{s.active_version} · {s.runs} runs · ${s.cost_per_run.toFixed(4)}/run
        </p>
      </button>
    </motion.li>
  );
}

export default function Library(p: Props) {
  const reduce = useReducedMotion() ?? false;
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"contract" | "code" | "diff">("contract");
  const d = p.detail;

  function select(id: string) {
    setSelected(id);
    setTab("contract");
    api
      .skill(id)
      .then(p.setDetail)
      .catch((e) => p.setError(String(e)));
  }

  function rollback(v: number) {
    if (!d) return;
    api
      .rollback(d.id, v)
      .then(() => api.skill(d.id).then(p.setDetail))
      .catch((e) => p.setError(String(e)));
  }

  const diff = d?.prev_skill_py != null ? diffLines(d.prev_skill_py, d.skill_py ?? "") : [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section>
        <h2 className="text-sm font-semibold text-zinc-200">Library ({p.skills.length})</h2>
        <ul className="mt-3 space-y-2">
          <AnimatePresence initial={false}>
            {p.skills.map((s) => (
              <Card
                key={s.id}
                s={s}
                selected={selected === s.id}
                repairing={p.repairingId === s.id}
                repaired={p.repairedId === s.id}
                reduce={reduce}
                onClick={() => select(s.id)}
              />
            ))}
          </AnimatePresence>
          {p.skills.length === 0 && <li className="text-sm text-zinc-500">Empty library — run a task to learn a skill.</li>}
        </ul>
      </section>
      <section>
        {d ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-4">
            <h3 className="break-all font-mono text-sm text-zinc-100">{d.id}</h3>
            <p className="mt-1 text-xs text-zinc-400">{String(d.contract.intent ?? "")}</p>
            {d.lineage.length > 0 && <p className="mt-1 text-xs text-zinc-400">composed of: {d.lineage.join(" → ")}</p>}
            <div className="mt-3 flex gap-2">
              {(["contract", "code", "diff"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  disabled={t === "diff" && d.prev_skill_py == null}
                  className={`rounded-full px-3 py-1 text-xs ${
                    tab === t ? "bg-cyan-400 text-zinc-950" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
                  } disabled:opacity-40`}
                >
                  {t === "diff" ? `diff v${d.prev_version}→v${d.versions.length > 0 ? "active" : "?"}` : t}
                </button>
              ))}
            </div>
            {tab === "contract" && (
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-zinc-950 p-3 text-xs text-zinc-300">
                {JSON.stringify(d.contract, null, 2)}
              </pre>
            )}
            {tab === "code" && (
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-zinc-950 p-3 text-xs text-zinc-300">
                {d.skill_py || "(no code captured)"}
              </pre>
            )}
            {tab === "diff" && d.prev_skill_py != null && (
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-zinc-950 p-3 text-xs leading-relaxed">
                {diff.map((l, i) => (
                  <div key={i} className={l.type === "add" ? "bg-emerald-400/10 text-emerald-200" : l.type === "del" ? "bg-rose-400/10 text-rose-200" : "text-zinc-500"}>
                    {l.type === "add" ? "+" : l.type === "del" ? "−" : " "}
                    {l.text}
                  </div>
                ))}
              </pre>
            )}
            <p className="mt-3 text-xs text-zinc-400">
              versions: {d.versions.map((v) => String(v.version)).join(", ")}
            </p>
            <div className="mt-1 flex flex-wrap gap-2">
              {d.versions.map((v) => Number(v.version)).map((v) => (
                <button key={v} onClick={() => rollback(v)} className="text-xs text-cyan-300 underline underline-offset-2">
                  rollback to v{v}
                </button>
              ))}
            </div>
            <h4 className="mt-4 text-xs font-medium text-zinc-300">Recent runs</h4>
            <ul className="mt-1 space-y-0.5 text-xs text-zinc-500">
              {d.recent_runs.map((r) => (
                <li key={String(r.id)}>
                  #{String(r.id)} {String(r.outcome)} r={String(r.reward)} v{String(r.version)}
                </li>
              ))}
              {d.recent_runs.length === 0 && <li>none yet</li>}
            </ul>
          </div>
        ) : (
          <p className="text-sm text-zinc-500">Select a skill to inspect it.</p>
        )}
      </section>
    </div>
  );
}
