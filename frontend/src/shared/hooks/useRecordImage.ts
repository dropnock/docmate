import { useEffect, useState } from "react";
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
 * gcTime: 0 is paired deliberately with the unmount-revoke below: it drops
 * this query's cache entry the instant it has no observers, so a later
 * remount (navigating back to the same task, WorkspaceErrorBoundary's
 * Retry, revisiting a TIFF page) always refetches fresh bytes into a new
 * blob instead of being handed back a cached objectUrl that was already
 * revoked. Without gcTime: 0, staleTime: Infinity would keep the stale,
 * revoked URL servable for the default 5-minute gcTime window, which is
 * what caused images to silently fail to load after a successful fetch.
 *
 * The returned object URL is revoked automatically once superseded by a
 * newer one, and on unmount — callers must not revoke it themselves.
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
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const objectUrl = query.data?.objectUrl;
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  return { ...query, page, setPage, pageCount: query.data?.pageCount ?? 1 };
}
