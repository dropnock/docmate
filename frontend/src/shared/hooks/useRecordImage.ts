import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";

interface RecordImage {
  objectUrl: string;
  contentType: string;
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
 * The returned object URL is revoked automatically once superseded or on
 * unmount — callers must not revoke it themselves. */
export function useRecordImage(recordId: number | undefined, enabled: boolean) {
  const query = useQuery({
    queryKey: ["record-image", recordId],
    queryFn: async (): Promise<RecordImage> => {
      const res = await api.get(`/records/${recordId}/image`, { responseType: "blob" });
      return {
        objectUrl: URL.createObjectURL(res.data as Blob),
        contentType: (res.headers["content-type"] as string | undefined) ?? "application/octet-stream",
      };
    },
    enabled: enabled && recordId != null,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const objectUrl = query.data?.objectUrl;
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  return query;
}
