import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { TaskResponse } from "../api";

interface Props {
  task: TaskResponse | null;
  busy: boolean;
  onDecide: (approvalId: number, decision: "approve" | "deny") => void;
}

export default function ApprovalModal({ task, busy, onDecide }: Props) {
  const reduce = useReducedMotion() ?? false;
  const open = task != null && task.status === "needs_approval" && task.approval_id != null;
  return (
    <AnimatePresence>
      {open && task && (
        <motion.div
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
        >
          <motion.div
            initial={reduce ? false : { scale: 0.94, y: 10 }}
            animate={{ scale: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { scale: 0.96, opacity: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 28 }}
            className="w-full max-w-md rounded-xl border border-zinc-700 bg-zinc-900 p-6"
          >
            <h3 className="text-base font-semibold text-zinc-100">Approve this new skill?</h3>
            <p className="mt-2 font-mono text-sm text-cyan-300">
              {task.skill_id} <span className="text-zinc-400">v{task.version}</span>
            </p>
            {task.detail && <p className="mt-2 text-sm text-zinc-400">{task.detail}</p>}
            <p className="mt-3 text-xs leading-relaxed text-zinc-500">
              Medha learned and verified this skill on your data. Approving runs it to
              produce your result; the skill stays in your library for reuse.
            </p>
            <div className="mt-5 flex gap-3">
              <button
                onClick={() => onDecide(task.approval_id as number, "approve")}
                disabled={busy}
                className="flex-1 rounded-lg bg-emerald-400 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-emerald-300 disabled:opacity-40"
              >
                {busy ? "Working…" : "Approve & run"}
              </button>
              <button
                onClick={() => onDecide(task.approval_id as number, "deny")}
                disabled={busy}
                className="flex-1 rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
              >
                Deny
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
