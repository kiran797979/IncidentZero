import { useEffect, useState, useRef } from "react";
import { motion } from "motion/react";
import type { HTMLMotionProps } from "motion/react";

interface DecryptedTextProps extends HTMLMotionProps<"span"> {
  text: string;
  speed?: number;
  maxIterations?: number;
  sequential?: boolean;
  revealDirection?: "start" | "end" | "center";
  useOriginalCharsOnly?: boolean;
  characters?: string;
  className?: string;
  parentClassName?: string;
  encryptedClassName?: string;
  animateOn?: "view" | "hover" | "both";
}

export default function DecryptedText({
  text,
  speed = 50,
  maxIterations = 10,
  sequential = false,
  revealDirection = "start",
  useOriginalCharsOnly = false,
  characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!@#$%^&*()_+",
  className = "",
  parentClassName = "",
  encryptedClassName = "",
  animateOn = "hover",
  ...props
}: DecryptedTextProps) {
  const [displayText, setDisplayText] = useState(text);
  const [isHovering, setIsHovering] = useState(false);
  const [isScrambling, setIsScrambling] = useState(false);
  const [revealedIndices, setRevealedIndices] = useState<Set<number>>(new Set());
  const [hasAnimated, setHasAnimated] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    let currentIteration = 0;

    const getNextIndex = (revealed: Set<number>): number => {
      const len = text.length;
      if (revealDirection === "start") return revealed.size;
      if (revealDirection === "end") return len - 1 - revealed.size;
      const mid = Math.floor(len / 2);
      const off = Math.floor(revealed.size / 2);
      const next = revealed.size % 2 === 0 ? mid + off : mid - off - 1;
      if (next >= 0 && next < len && !revealed.has(next)) return next;
      for (let i = 0; i < len; i++) if (!revealed.has(i)) return i;
      return 0;
    };

    const chars = useOriginalCharsOnly
      ? Array.from(new Set(text.split(""))).filter((c) => c !== " ")
      : characters.split("");

    const shuffle = (orig: string, revealed: Set<number>): string => {
      if (useOriginalCharsOnly) {
        const pos = orig.split("").map((ch, i) => ({ ch, isSpace: ch === " ", i, done: revealed.has(i) }));
        const pool = pos.filter((p) => !p.isSpace && !p.done).map((p) => p.ch);
        for (let i = pool.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [pool[i], pool[j]] = [pool[j], pool[i]];
        }
        let ci = 0;
        return pos.map((p) => (p.isSpace ? " " : p.done ? orig[p.i] : pool[ci++])).join("");
      }
      return orig.split("").map((ch, i) => (ch === " " ? " " : revealed.has(i) ? orig[i] : chars[Math.floor(Math.random() * chars.length)])).join("");
    };

    if (isHovering) {
      setIsScrambling(true);
      interval = setInterval(() => {
        setRevealedIndices((prev) => {
          if (sequential) {
            if (prev.size < text.length) {
              const next = new Set(prev);
              next.add(getNextIndex(prev));
              setDisplayText(shuffle(text, next));
              return next;
            }
            clearInterval(interval);
            setIsScrambling(false);
            return prev;
          }
          setDisplayText(shuffle(text, prev));
          currentIteration++;
          if (currentIteration >= maxIterations) {
            clearInterval(interval);
            setIsScrambling(false);
            setDisplayText(text);
          }
          return prev;
        });
      }, speed);
    } else {
      setDisplayText(text);
      setRevealedIndices(new Set());
      setIsScrambling(false);
    }
    return () => { if (interval) clearInterval(interval); };
  }, [isHovering, text, speed, maxIterations, sequential, revealDirection, characters, useOriginalCharsOnly]);

  useEffect(() => {
    if (animateOn !== "view" && animateOn !== "both") return;
    const observer = new IntersectionObserver(
      (entries) => { entries.forEach((e) => { if (e.isIntersecting && !hasAnimated) { setIsHovering(true); setHasAnimated(true); } }); },
      { threshold: 0.1 }
    );
    const el = containerRef.current;
    if (el) observer.observe(el);
    return () => { if (el) observer.unobserve(el); };
  }, [animateOn, hasAnimated]);

  const hoverProps = animateOn === "hover" || animateOn === "both"
    ? { onMouseEnter: () => setIsHovering(true), onMouseLeave: () => setIsHovering(false) }
    : {};

  return (
    <motion.span ref={containerRef} className={parentClassName} style={{ display: "inline-block", whiteSpace: "pre-wrap" }} {...hoverProps} {...props}>
      <span style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}>{displayText}</span>
      <span aria-hidden="true">
        {displayText.split("").map((ch, i) => (
          <span key={i} className={revealedIndices.has(i) || !isScrambling || !isHovering ? className : encryptedClassName}>{ch}</span>
        ))}
      </span>
    </motion.span>
  );
}