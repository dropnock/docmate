import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { message } from "antd";
import { flagKey, getFlag, type RecordFlagListItem } from "@shared/api/flags";

/** The flag open in a Flagged Records drawer. Opens from a row click, or
 * from `?flag=<id>` in the URL (where a notification links to); the flag is
 * fetched on its own so the drawer stays current after share/resolve. */
export function useFlagReview(rows: RecordFlagListItem[]) {
  const [params, setParams] = useSearchParams();
  const linkedId = Number(params.get("flag")) || null;
  const [flagId, setFlagId] = useState<number | null>(null);

  useEffect(() => {
    if (linkedId) setFlagId(linkedId);
  }, [linkedId]);

  const query = useQuery({
    queryKey: flagKey(flagId ?? 0),
    queryFn: () => getFlag(flagId!),
    enabled: flagId != null,
    initialData: () => rows.find((r) => r.id === flagId),
    retry: false,
  });

  const close = () => {
    setFlagId(null);
    if (params.has("flag")) {
      const next = new URLSearchParams(params);
      next.delete("flag");
      setParams(next, { replace: true });
    }
  };

  useEffect(() => {
    if (query.isError && flagId != null) {
      message.warning("That flag is no longer available");
      close();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.isError, flagId]);

  return {
    reviewing: flagId != null ? query.data ?? null : null,
    open: (flag: RecordFlagListItem) => setFlagId(flag.id),
    close,
  };
}
