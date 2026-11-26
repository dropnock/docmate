import { useEffect } from "react";
import api from "@shared/api/client";

/** Matches backend services/task_timing.py: a beat every 30s while the agent
 * is working; a gap over 90s counts as time away. */
const BEAT_MS = 30_000;
/** No mouse, key or wheel input for this long and the clock stops. */
const IDLE_MS = 5 * 60_000;

/** Tells the backend the agent is working on a task, so the time on the
 * record adds up across sessions and leaves out time away (tab hidden, idle,
 * workspace closed). Beats on opening (which counts a session), every 30s
 * while working, on coming back from idle or a hidden tab, and on leaving. */
export function useTaskHeartbeat(taskId: number | undefined, active: boolean) {
  useEffect(() => {
    if (!taskId || !active) return;
    let lastInput = Date.now();
    const recent = () => Date.now() - lastInput < IDLE_MS;
    const working = () => document.visibilityState === "visible" && recent();
    const beat = (opened = false) => {
      api.post(`/tasks/${taskId}/heartbeat`, null, { params: opened ? { opened: true } : undefined }).catch(() => {
        /* finished or moved on: nothing to count */
      });
    };
    const onInput = () => {
      const wasIdle = !recent();
      lastInput = Date.now();
      if (wasIdle && document.visibilityState === "visible") beat();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (recent()) beat(); // count up to the moment the tab is hidden
      } else {
        lastInput = Date.now();
        beat();
      }
    };
    const events = ["pointermove", "pointerdown", "keydown", "wheel"] as const;
    events.forEach((e) => window.addEventListener(e, onInput, { passive: true }));
    document.addEventListener("visibilitychange", onVisibility);
    beat(true);
    const timer = window.setInterval(() => {
      if (working()) beat();
    }, BEAT_MS);
    return () => {
      window.clearInterval(timer);
      events.forEach((e) => window.removeEventListener(e, onInput));
      document.removeEventListener("visibilitychange", onVisibility);
      if (working()) beat();
    };
  }, [taskId, active]);
}
