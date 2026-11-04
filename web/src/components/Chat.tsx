import { useRef, useState } from "react";
import type { DemoPreset, TaskResponse } from "../api";
import { api } from "../api";

interface Props {
  text: string;
  setText: (t: string) => void;
  files: File[];
  setFiles: (f: File[]) => void;
  presets: DemoPreset[];
  onPreset: (p: DemoPreset) => void;
  busy: boolean;
  presetBusy: string | null;
  result: TaskResponse | null;
  onRun: () => void;
}

export default function Chat(p: Props) {
  const [drag, setDrag] = useState(false);
  const [correction, setCorrection] = useState("");
  const [note, setNote] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const r = p.result;

  async function sendFeedback(kind: string) {
    if (!r) return;
    try {
      const out = await api.feedback(r.task_id, kind, correction);
      setNote(kind === "correct" ? `correction recorded: ${JSON.stringify(out)}` : "");
      if (kind !== "correct") setCorrection("");
    } catch (e) {
      setNote(String(e));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-2 flex flex-wrap gap-2">
          {p.presets.map((pr) => (
            <button
              key={pr.id}
              onClick={() => p.onPreset(pr)}
              disabled={p.busy || p.presetBusy != null}
              className="rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300 hover:border-cyan-400/60 hover:text-cyan-300 disabled:opacity-40"
            >
              {p.presetBusy === pr.id ? "loading…" : pr.label}
            </button>
          ))}
        </div>
        <textarea
          value={p.text}
          onChange={(e) => p.setText(e.target.value)}
          placeholder="Describe the chore, e.g. weekly sales report from this CSV…"
          rows={3}
          className="w-full rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-sm text-zinc-100 placeholder-zinc-500 focus:border-cyan-400 focus:outline-none"
        />
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const dropped = [...e.dataTransfer.files];
            if (dropped.length > 0) p.setFiles(dropped);
          }}
          onClick={() => fileRef.current?.click()}
          className={`mt-2 cursor-pointer rounded-lg border border-dashed px-3 py-4 text-center text-sm transition-colors ${
            drag ? "border-cyan-400 bg-cyan-400/10 text-cyan-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"
          }`}
        >
          {p.files.length > 0 ? p.files.map((f) => f.name).join(", ") : "Drop files here or click to attach"}
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => p.setFiles([...(e.target.files ?? [])])}
          />
        </div>
        <button
          onClick={p.onRun}
          disabled={p.busy || (!p.text.trim() && p.files.length === 0)}
          className="mt-3 rounded-lg bg-cyan-400 px-5 py-2 text-sm font-semibold text-zinc-950 hover:bg-cyan-300 disabled:opacity-40"
        >
          {p.busy ? "Working…" : "Run task"}
        </button>
      </div>

      {r && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-4 text-sm">
          <p className="text-zinc-100">
            <b>{r.status}</b>
            {r.skill_id && <span className="text-zinc-400"> · {r.skill_id} v{r.version}</span>}
          </p>
          {r.detail && <p className="mt-1 text-zinc-400">{r.detail}</p>}
          {r.status === "done" && (
            <>
              <pre className="mt-3 max-h-48 overflow-auto rounded-md bg-zinc-950 p-3 text-xs text-zinc-300">
                {JSON.stringify(r.result, null, 2)}
              </pre>
              {r.file_names.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {r.file_names.map((n) => (
                    <button
                      key={n}
                      onClick={() => api.download(r.task_id, n).catch(() => {})}
                      className="text-xs text-cyan-300 underline underline-offset-2"
                    >
                      {n}
                    </button>
                  ))}
                </div>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button onClick={() => sendFeedback("accept")} className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700">
                  Accept
                </button>
                <button onClick={() => sendFeedback("reject")} className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700">
                  Reject
                </button>
                <input
                  value={correction}
                  onChange={(e) => setCorrection(e.target.value)}
                  placeholder="correction…"
                  className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 placeholder-zinc-500"
                />
                <button onClick={() => sendFeedback("correct")} className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700">
                  Correct
                </button>
              </div>
              {note && <p className="mt-2 text-xs text-zinc-400">{note}</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
