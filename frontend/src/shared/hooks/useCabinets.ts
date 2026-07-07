import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { Cabinet } from "@shared/types";

export function useCabinets(projectId: number) {
  return useQuery<Cabinet[]>({
    queryKey: ["cabinets", projectId],
    queryFn: () => api.get(`/cabinets/project/${projectId}`).then((r) => r.data),
  });
}
