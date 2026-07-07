import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

interface Props {
  allow: boolean;
  children: ReactNode;
}

/** Redirects to "/" when the current user doesn't satisfy the role check for
 * this route. Load-bearing (not just UI hiding) now that URLs are guessable/
 * bookmarkable — it's the only thing standing between a non-admin and typing
 * /users into the address bar. */
export default function RequireRole({ allow, children }: Props) {
  if (!allow) return <Navigate to="/" replace />;
  return <>{children}</>;
}
