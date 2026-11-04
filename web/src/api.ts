export interface TaskResponse {
  task_id: string;
  status: string;
  skill_id: string | null;
  version: number | null;
  result: Record<string, unknown>;
  file_names: string[];
  detail: string;
  approval_id: number | null;
}

export interface MedhaEvent {
  id: number;
  ts: string;
  type: string;
  actor: string;
  skill_id: string | null;
  version: number | null;
  payload: Record<string, unknown>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
}

export interface SkillSummary {
  id: string;
  active_version: number;
  trust: string;
  expected_p: number;
  runs: number;
  cost_per_run: number;
  versions: number[];
}

export interface SkillDetail {
  id: string;
  contract: Record<string, unknown>;
  skill_md: string;
  skill_py: string;
  prev_skill_py: string | null;
  prev_version: number | null;
  versions: Record<string, unknown>[];
  lineage: string[];
  recent_runs: Record<string, unknown>[];
}

export interface BenchRow {
  id: string;
  family: string;
  passed: boolean;
  tokens: number;
  cost_usd: number;
  latency_ms: number;
  reused: boolean;
}

export interface BenchRun {
  ts: string;
  suite: string;
  author?: string;
  configs: Record<string, { summary: Record<string, number>; rows: BenchRow[] }>;
}

export interface ActivityPoint {
  ts: string;
  skills: number;
  runs: number;
}

export interface DemoPreset {
  id: string;
  label: string;
  text: string;
  files: string[];
}

export function token(): string {
  return localStorage.getItem("medha_token") ?? "";
}

export function setToken(t: string): void {
  localStorage.setItem("medha_token", t);
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return { Authorization: `Bearer ${token()}`, ...(extra ?? {}) };
}

async function req(path: string, init: RequestInit = {}): Promise<unknown> {
  const r = await fetch(path, { ...init, headers: authHeaders(init.headers as Record<string, string> | undefined) });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export const api = {
  createTask: (text: string, files: File[]): Promise<TaskResponse> => {
    const fd = new FormData();
    fd.append("text", text);
    files.forEach((f) => fd.append("files", f));
    return fetch("/api/tasks", { method: "POST", headers: authHeaders(), body: fd }).then(
      async (r) => {
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
        return r.json() as Promise<TaskResponse>;
      }
    );
  },
  decide: (id: number, decision: string): Promise<TaskResponse> =>
    req(`/api/approvals/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }) as Promise<TaskResponse>,
  feedback: (taskId: string, kind: string, text = ""): Promise<Record<string, unknown>> =>
    req(`/api/tasks/${taskId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, text }),
    }) as Promise<Record<string, unknown>>,
  skills: (): Promise<SkillSummary[]> => req("/api/skills") as Promise<SkillSummary[]>,
  skill: (id: string): Promise<SkillDetail> =>
    req(`/api/skills/${id}`) as Promise<SkillDetail>,
  rollback: (id: string, to_version: number) =>
    req(`/api/skills/${id}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_version }),
    }),
  lessons: (): Promise<Record<string, unknown>[]> =>
    req("/api/lessons") as Promise<Record<string, unknown>[]>,
  deleteLesson: (id: number) => req(`/api/lessons/${id}`, { method: "DELETE" }),
  stats: (): Promise<Record<string, unknown>> =>
    req("/api/stats") as Promise<Record<string, unknown>>,
  bench: (): Promise<{ runs: BenchRun[] }> =>
    req("/api/bench") as Promise<{ runs: BenchRun[] }>,
  activity: (): Promise<{ points: ActivityPoint[] }> =>
    req("/api/activity") as Promise<{ points: ActivityPoint[] }>,
  demoPresets: (): Promise<DemoPreset[]> =>
    req("/api/demo/presets") as Promise<DemoPreset[]>,
  demoFile: async (name: string): Promise<Blob> => {
    const r = await fetch(`/api/demo/files/${name}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.blob();
  },
  outputs: (taskId: string): Promise<string[]> =>
    req(`/api/tasks/${taskId}/outputs`) as Promise<string[]>,
  download: async (taskId: string, name: string): Promise<void> => {
    const r = await fetch(`/api/tasks/${taskId}/outputs/${name}`, {
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  },
};

/** Manual SSE reader (EventSource cannot send Authorization headers). */
export async function streamEvents(
  taskId: string,
  signal: AbortSignal,
  onEvent: (e: MedhaEvent) => void
): Promise<void> {
  const r = await fetch(`/api/tasks/${taskId}/events`, {
    headers: authHeaders(),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const p of parts) {
      const line = p.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as MedhaEvent);
    }
  }
}
