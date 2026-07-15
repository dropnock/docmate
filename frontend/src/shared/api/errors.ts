/** FastAPI's `detail` is usually a string, but lock_service.acquire_lock()
 * raises 409 with a structured detail `{message, locked_by, locked_at}` (see
 * CLAUDE.md's record-locking section) — rendering that object directly as a
 * JSX child or passing it straight to antd's message.error() throws "Objects
 * are not valid as a React child". Callers on the record-locking path
 * (task start/complete) must go through this instead of reading
 * `.response.data.detail` directly. */
export function formatApiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return (detail as { message: string }).message;
  }
  return fallback;
}
