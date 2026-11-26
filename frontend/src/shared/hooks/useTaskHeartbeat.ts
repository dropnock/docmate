import { useEffect } from "react";
import api from "@shared/api/client";

/** Matches backend services/task_timing.py: a beat every 30s while the agent
 * is working; a gap over 90s counts as time away. */
const BEAT_MS = 30_000;
/** No mouse, key or wheel input for this long and the clock stops. */
const IDLE_MS = 5 * 60_000;

/** Tells the backend the agent is still working on a task, so the time on
 * the record adds up across sessions and leaves out time away (tab hidden,
 * idle, logged off). */
export function useTaskHeartbeat(taskId: number | undefined, active: boolean) {
  useEffect(() => {
    if (!taskId || !active) return;
    let lastInput = Date.now();
    const onInput = () => {
      lastInput = Date.now();
    };
    const working = () => document.visibilityState === "visible" && Date.now() - lastInput < IDLE_MS;
    const beat = () => {
      api.post(`/tasks/${taskId}/heartbeat`).catch(() => {
        /* finished or moved on: nothing to count */
      });
    };
    const onVisibility = () => {
      // Count up to the moment the tab is hidden, and restart on return.
      if (document.visibilityState === "hidden" && Date.now() - lastInput < IDLE_MS) beat();
      if (document.visibilityState === "visible") lastInput = Date.now();
    };
    const events = ["pointermove", "pointerdown", "keydown", "wheel"] as const;
    events.forEach((e) => window.addEventListener(e, onInput, { passive: true }));
    document.addEventListener("visibilitychange", onVisibility);
    const timer = window.setInterval(() => {
      if (working()) beat();
    }, BEAT_MS);
    return () => {
      window.clearInterval(timer);
      events.forEach((e) => window.removeEventListener(e, onInput));
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [taskId, active]);
}
