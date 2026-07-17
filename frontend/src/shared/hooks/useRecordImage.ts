import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";

interface RecordImage {
  objectUrl: string;
  contentType: string;
  pageCount: number;
}

/** Fetches a record's image/PDF bytes through the backend (same origin as
 * the rest of the API) instead of a presigned S3 URL on a separate origin —
 * see backend/app/services/s3_service.py's stream_object for why: a
 * background image/tile fetch to an untrusted second TLS origin fails with
 * no user-actionable "proceed anyway" prompt, unlike top-level navigation.
 *
 * staleTime: Infinity + refetchOnWindowFocus/refetchOnReconnect: false
 * mirror the same reasoning the old presigned-URL query needed — revoking
 * the object URL out from under an in-progress OpenSeadragon/iframe load
 * would break it the same way a refreshed presigned URL did.
 *
 * The returned object URL is revoked automatically once superseded by a
 * newer one — callers must not revoke it themselves. It is deliberately
 * NOT revoked on unmount: with staleTime: Infinity, React Query keeps
 * this query's cached data (and its objectUrl) alive for gcTime after
 * unmount, and a remount within that window (e.g. navigating back to the
 * same task, or WorkspaceErrorBoundary's Retry) would otherwise be handed
 * back an already-revoked URL, making the image silently fail to load.
 *
 * `page` tracks which frame of a multi-page TIFF is requested — see
 * batches.py's get_record_image, which reports the total via X-Page-Count.
 * Switching records resets back to the first page. */
export function useRecordImage(recordId: number | undefined, enabled: boolean) {
  const [page, setPage] = useState(0);
  useEffect(() => {
    setPage(0);
  }, [recordId]);

  const query = useQuery({
    queryKey: ["record-image", recordId, page],
    queryFn: async (): Promise<RecordImage> => {
      const res = await api.get(`/records/${recordId}/image`, {
        responseType: "blob",
        params: { page },
      });
      return {
        objectUrl: URL.createObjectURL(res.data as Blob),
        contentType: (res.headers["content-type"] as string | undefined) ?? "application/octet-stream",
        pageCount: Number(res.headers["x-page-count"] ?? 1),
      };
    },
    enabled: enabled && recordId != null,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const objectUrl = query.data?.objectUrl;
  const prevObjectUrlRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevObjectUrlRef.current;
    if (prev && prev !== objectUrl) {
      URL.revokeObjectURL(prev);
    }
    prevObjectUrlRef.current = objectUrl;
  }, [objectUrl]);

  return { ...query, page, setPage, pageCount: query.data?.pageCount ?? 1 };
}
