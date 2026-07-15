import { QueryClient } from "@tanstack/react-query";

/** Shared QueryClient defaults for both portals. Baseline staleTime stops
 * the app's many actively-polling queries from *also* refetching on every
 * component mount and window focus in between their own refetchInterval
 * ticks — the intervals themselves are untouched by this.
 *
 * Per-query options always override these defaults, so a query with its own
 * staleTime (e.g. useRecordImage's record-image query, deliberately
 * staleTime: Infinity to avoid revoking its object URL out from under an
 * in-progress viewer mid-load) is unaffected — don't "simplify" that
 * override away, it needs to survive this default. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}
