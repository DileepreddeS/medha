import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { ActivityPoint, BenchRun, DemoPreset, MedhaEvent, SkillDetail, SkillSummary, TaskResponse } from "./api";
import { api, setToken, streamEvents, token } from "./api";
import Chat from "./components/Chat";
import Pipeline from "./components/Pipeline";
import Library from "./components/Library";
import Evidence from "./components/Evidence";
import ApprovalModal from "./components/ApprovalModal";

type Tab = "demo" | "library" | "evidence";

const TABS: { id: Tab; label: string }[] = [
  { id: "demo", label: "Demo" },
  { id: "library", label: "Library" },
  { id: "evidence", label: "Evidence" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("demo");
  const [tok, setTok] = useState(token());
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [events, setEvents] = useState<MedhaEvent[]>([]);
  const [result, setResult] = useState<TaskResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [presetBusy, setPresetBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [presets, setPresets] = useState<DemoPreset[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [runs, setRuns] = useState<BenchRun[]>([]);
  const [activity, setActivity] = useState<ActivityPoint[]>([]);
  const [lessons, setLessons] = useState<Record<string, unknown>[]>([]);
  const [repairingId, setRepairingId] = useState<string | null>(null);
  const [repairedId, setRepairedId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const t0Ref = useRef(0);

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    api.demoPresets().then(setPresets).catch(() => {});
  }, []);

  // Track repair state from the live event stream for the library cards.
  useEffect(() => {
    const reps = events.filter((e) => e.type === "llm.repairer" || e.type === "repair.attempt");
    if (reps.length === 0) {
      setRepairingId(null);
      return;
    }
    const regs = events.filter((e) => e.type === "skill.registered");
    const lastRep = reps[reps.length - 1];
    const lastReg = regs[regs.length - 1];
    if (lastReg && lastReg.id > lastRep.id && Number(lastReg.version) > 1) {
      setRepairingId(null);
      setRepairedId(lastReg.skill_id);
    } else {
      setRepairingId(lastRep.skill_id);
      setRepairedId(null);
    }
  }, [events]);

  async function refreshSkills() {
    try {
      setSkills(await api.skills());
    } catch (e) {
      setError(String(e));
    }
  }

  async function refreshEvidence() {
    try {
      const [s, b, a, l] = await Promise.all([api.stats(), api.bench(), api.activity(), api.lessons()]);
      setStats(s);
      setRuns(b.runs ?? []);
      setActivity(a.points ?? []);
      setLessons(l);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    if (tab === "library") void refreshSkills();
    if (tab === "evidence") void refreshEvidence();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function runPreset(p: DemoPreset) {
    setPresetBusy(p.id);
    setError("");
    try {
      const blobs = await Promise.all(p.files.map((n) => api.demoFile(n)));
      setFiles(blobs.map((b, i) => new File([b], p.files[i], { type: "text/csv" })));
      setText(p.text);
    } catch (e) {
      setError(String(e));
    } finally {
      setPresetBusy(null);
    }
  }

  /** Collect a task's events, then release them on a beat so the
   *  pipeline visibly steps through stages instead of flashing done. */
  async function playEvents(taskId: string, seen: Set<number>, ac: AbortController) {
    const queue: MedhaEvent[] = [];
    await streamEvents(taskId, ac.signal, (e) => {
      if (seen.has(e.id)) return;
      seen.add(e.id);
      queue.push(e);
    }).catch(() => {});
    for (const e of queue) {
      if (ac.signal.aborted) break;
      setEvents((prev) => [...prev, e]);
      await new Promise((r) => setTimeout(r, 140));
    }
  }

  async function submit() {
    setError("");
    setEvents([]);
    setResult(null);
    setElapsedMs(null);
    setRepairedId(null);
    setBusy(true);
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    t0Ref.current = performance.now();
    try {
      const res = await api.createTask(text, files);
      await playEvents(res.task_id, new Set<number>(), ac);
      setElapsedMs(performance.now() - t0Ref.current);
      setResult(res);
      if (res.status === "done") void refreshSkills();
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function decide(approvalId: number, decision: "approve" | "deny") {
    setBusy(true);
    try {
      const res = await api.decide(approvalId, decision);
      if (res.task_id && abortRef.current) {
        await playEvents(res.task_id, new Set(events.map((e) => e.id)), abortRef.current);
      }
      setElapsedMs(performance.now() - t0Ref.current);
      setResult(res);
      void refreshSkills();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-4 pb-2 sm:px-6">
      <header className="flex flex-wrap items-center gap-3 border-b border-zinc-800 py-4">
        <h1 className="text-lg font-bold tracking-tight text-zinc-100">
          Medha <span className="font-normal text-zinc-500">· skills that stick</span>
        </h1>
        <nav className="flex gap-1.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-full px-4 py-1.5 text-sm ${
                tab === t.id ? "bg-cyan-400 font-medium text-zinc-950" : "bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <input
          type="password"
          placeholder="MEDHA_TOKEN"
          value={tok}
          onChange={(e) => {
            setTok(e.target.value);
            setToken(e.target.value);
          }}
          className="ml-auto w-40 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 placeholder-zinc-600 focus:border-cyan-400 focus:outline-none"
        />
      </header>

      <AnimatePresence>
        {error && (
          <motion.p
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-3 rounded-lg border border-rose-400/40 bg-rose-400/10 p-3 text-sm text-rose-200"
          >
            {error}
            <button onClick={() => setError("")} className="ml-3 underline underline-offset-2">
              dismiss
            </button>
          </motion.p>
        )}
      </AnimatePresence>

      <main className="flex-1 py-5">
        {tab === "demo" && (
          <div className="grid gap-6 lg:grid-cols-2">
            <Chat
              text={text}
              setText={setText}
              files={files}
              setFiles={setFiles}
              presets={presets}
              onPreset={runPreset}
              busy={busy}
              presetBusy={presetBusy}
              result={result}
              onRun={submit}
            />
            <Pipeline events={events} elapsedMs={elapsedMs} />
          </div>
        )}
        {tab === "library" && (
          <Library
            skills={skills}
            detail={detail}
            setDetail={setDetail}
            setError={setError}
            repairingId={repairingId}
            repairedId={repairedId}
          />
        )}
        {tab === "evidence" && (
          <Evidence
            stats={stats}
            runs={runs}
            activity={activity}
            lessons={lessons}
            onDeleteLesson={(id) =>
              api
                .deleteLesson(id)
                .then(() => refreshEvidence())
                .catch((e) => setError(String(e)))
            }
          />
        )}
      </main>

      <ApprovalModal task={result} busy={busy} onDecide={decide} />

      <footer className="mt-auto border-t border-zinc-800 py-4 text-center text-xs text-zinc-500">
        Built with Meta Muse Glimmer
      </footer>
    </div>
  );
}
