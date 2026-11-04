import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/** Animated count-up; jumps to target under reduced motion. */
export function useCountUp(target: number, duration = 900): number {
  const reduce = useReducedMotion() ?? false;
  const [val, setVal] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (reduce) {
      setVal(target);
      return;
    }
    const start = from.current;
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / duration);
      const eased = 1 - Math.pow(1 - k, 3);
      setVal(start + (target - start) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, reduce]);
  return val;
}
