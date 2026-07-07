import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { DocumentType } from "@shared/types";

/** Canonical key is "document-types" — two call sites used to key this
 * query as "doc-types" instead, which meant they never shared React Query's
 * cache with the third (identical) call site and silently double-fetched. */
export function useDocumentTypes(projectId: number) {
  return useQuery<DocumentType[]>({
    queryKey: ["document-types", projectId],
    queryFn: () => api.get(`/projects/${projectId}/document-types`).then((r) => r.data),
  });
}
