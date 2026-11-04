export interface DiffLine {
  type: "same" | "add" | "del";
  text: string;
}

/** Small LCS line diff for skill v1 → v2. Capped for display. */
export function diffLines(a: string, b: string, cap = 400): DiffLine[] {
  const A = a.split("\n");
  const B = b.split("\n");
  const n = A.length;
  const m = B.length;
  // Cap quadratic work: fall back to block replace on big files.
  if (n * m > 120000) {
    return [
      ...A.slice(0, cap).map((text) => ({ type: "del" as const, text })),
      ...B.slice(0, cap).map((text) => ({ type: "add" as const, text })),
    ];
  }
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m && out.length < cap) {
    if (A[i] === B[j]) {
      out.push({ type: "same", text: A[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "del", text: A[i] });
      i++;
    } else {
      out.push({ type: "add", text: B[j] });
      j++;
    }
  }
  while (i < n && out.length < cap) out.push({ type: "del", text: A[i++] });
  while (j < m && out.length < cap) out.push({ type: "add", text: B[j++] });
  return out;
}
