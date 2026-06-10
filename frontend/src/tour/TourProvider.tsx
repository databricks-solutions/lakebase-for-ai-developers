// Tour engine. Loads the vendored Shepherd UMD global (base-path safe via BASE_URL), exposes
// startTour(id) through context, and builds steps from the registry. Single-page — no
// navigation. Ported/simplified from strategic_revenue_demo's TourProvider.

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { TOURS, type Tour, type TourStep } from "./tours";

interface TourContextValue {
  startTour: (id: string) => void;
  ready: boolean;
}

const TourContext = createContext<TourContextValue | null>(null);

export function useTour(): TourContextValue {
  const ctx = useContext(TourContext);
  if (!ctx) throw new Error("useTour must be used within <TourProvider>");
  return ctx;
}

const SELECTOR = (target: string) => `[data-tour="${CSS.escape(target)}"]`;

/** Wait for an element to exist (so a step attaches once its target has mounted). */
function waitForElement(selector: string, timeoutMs = 4000): Promise<void> {
  return new Promise((resolve) => {
    if (document.querySelector(selector)) return resolve();
    const started = Date.now();
    const id = window.setInterval(() => {
      if (document.querySelector(selector) || Date.now() - started > timeoutMs) {
        window.clearInterval(id);
        resolve();
      }
    }, 80);
  });
}

/** Inject the vendored Shepherd CSS + JS once; resolves when window.Shepherd is available. */
function loadShepherd(): Promise<any> {
  const w = window as any;
  if (w.Shepherd) return Promise.resolve(w.Shepherd);
  if (w.__shepherdLoading) return w.__shepherdLoading;
  const base = import.meta.env.BASE_URL || "/";
  if (!document.querySelector('link[data-shepherd]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `${base}vendor/shepherd/shepherd.css`;
    link.setAttribute("data-shepherd", "");
    document.head.appendChild(link);
  }
  w.__shepherdLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = `${base}vendor/shepherd/shepherd.min.js`;
    s.onload = () => resolve(w.Shepherd);
    s.onerror = reject;
    document.body.appendChild(s);
  });
  return w.__shepherdLoading;
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const activeRef = useRef<any>(null);

  useEffect(() => {
    loadShepherd().then(() => setReady(true)).catch(() => setReady(false));
  }, []);

  const buildAndRun = useCallback((tour: Tour, Shepherd: any) => {
    if (activeRef.current) {
      try { activeRef.current.cancel(); } catch { /* gone */ }
    }
    document.body.classList.add("sr-tour-active");
    const t = new Shepherd.Tour({
      useModalOverlay: true,
      defaultStepOptions: {
        scrollTo: { behavior: "smooth", block: "center" },
        cancelIcon: { enabled: true },
        classes: "sr-tour",
        modalOverlayOpeningPadding: 4,
        modalOverlayOpeningRadius: 6,
      },
    });
    activeRef.current = t;
    const cleanup = () => document.body.classList.remove("sr-tour-active");
    t.on("complete", cleanup);
    t.on("cancel", cleanup);

    tour.steps.forEach((step: TourStep, i: number) => {
      const isLast = i === tour.steps.length - 1;
      const buttons: Array<Record<string, unknown>> = [
        { text: "Exit", action: () => t.cancel(), classes: "sr-tour-btn sr-tour-btn--exit" },
      ];
      if (i > 0) buttons.push({ text: "Back", action: () => t.back(), classes: "sr-tour-btn sr-tour-btn--ghost" });
      buttons.push({ text: isLast ? "Done" : "Next", action: () => (isLast ? t.complete() : t.next()), classes: "sr-tour-btn sr-tour-btn--primary" });

      t.addStep({
        id: `${tour.id}-${i}`,
        title: step.title,
        text: step.body,
        buttons,
        attachTo: step.target ? { element: SELECTOR(step.target), on: step.side ?? "bottom" } : undefined,
        beforeShowPromise: () =>
          new Promise<void>((resolve) => {
            if (!step.target) { window.setTimeout(resolve, 100); return; }
            waitForElement(SELECTOR(step.target)).finally(() =>
              window.requestAnimationFrame(() => resolve())
            );
          }),
      });
    });
    t.start();
  }, []);

  const startTour = useCallback((id: string) => {
    const tour = TOURS[id];
    if (!tour) { console.warn("[tour] unknown id", id); return; }
    loadShepherd().then((Shepherd) => {
      if (!Shepherd) { console.warn("[tour] Shepherd failed to load"); return; }
      window.requestAnimationFrame(() => buildAndRun(tour, Shepherd));
    });
  }, [buildAndRun]);

  return <TourContext.Provider value={{ startTour, ready }}>{children}</TourContext.Provider>;
}
