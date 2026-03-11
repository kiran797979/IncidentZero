import { useInView, useMotionValue, useSpring } from "motion/react";
import { useCallback, useEffect, useRef } from "react";

interface CountUpProps {
  to: number;
  from?: number;
  direction?: "up" | "down";
  delay?: number;
  duration?: number;
  className?: string;
  startWhen?: boolean;
  separator?: string;
  decimals?: number;
  suffix?: string;
  onStart?: () => void;
  onEnd?: () => void;
}

export default function CountUp({
  to,
  from = 0,
  direction = "up",
  delay = 0,
  duration = 2,
  className = "",
  startWhen = true,
  separator = "",
  decimals,
  suffix = "",
  onStart,
  onEnd,
}: CountUpProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const motionValue = useMotionValue(direction === "down" ? to : from);
  const springValue = useSpring(motionValue, {
    damping: 20 + 40 * (1 / duration),
    stiffness: 100 * (1 / duration),
  });
  const isInView = useInView(ref, { once: true, margin: "0px" });

  const maxDecimals =
    decimals !== undefined
      ? decimals
      : Math.max(
          ...[from, to].map((n) => {
            const s = n.toString();
            return s.includes(".") ? s.split(".")[1].length : 0;
          })
        );

  const format = useCallback(
    (v: number) => {
      const opts: Intl.NumberFormatOptions = {
        useGrouping: !!separator,
        minimumFractionDigits: maxDecimals,
        maximumFractionDigits: maxDecimals,
      };
      let s = Intl.NumberFormat("en-US", opts).format(v);
      if (separator) s = s.replace(/,/g, separator);
      return s + suffix;
    },
    [maxDecimals, separator, suffix]
  );

  useEffect(() => {
    if (ref.current) {
      ref.current.textContent = format(direction === "down" ? to : from);
    }
  }, [from, to, direction, format]);

  useEffect(() => {
    if (isInView && startWhen) {
      if (onStart) onStart();
      const timeoutId = setTimeout(() => {
        motionValue.set(direction === "down" ? from : to);
      }, delay * 1000);
      const endTimeoutId = setTimeout(() => {
        if (onEnd) onEnd();
      }, delay * 1000 + duration * 1000);
      return () => {
        clearTimeout(timeoutId);
        clearTimeout(endTimeoutId);
      };
    }
  }, [isInView, startWhen, motionValue, direction, from, to, delay, onStart, onEnd, duration]);

  useEffect(() => {
    const unsub = springValue.on("change", (latest: number) => {
      if (ref.current) ref.current.textContent = format(latest);
    });
    return () => unsub();
  }, [springValue, format]);

  return <span className={className} ref={ref} />;
}