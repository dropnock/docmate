import { useSearchParams } from "react-router-dom";

/** The selected project is a cross-cutting filter shared across many pages,
 * modeled as a query param rather than a path segment so switching pages
 * preserves it. `replace: true` keeps project switches out of browser history. */
export function useProjectId(): [number | null, (id: number) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("project");
  const projectId = raw ? Number(raw) : null;

  const setProjectId = (id: number) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("project", String(id));
        return next;
      },
      { replace: true }
    );
  };

  return [projectId, setProjectId];
}
