import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { AvailableStaff } from "@shared/types";

export function useAvailableStaff(projectId: number, shiftId: number | null | undefined) {
  return useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", projectId, shiftId],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/available-staff`, { params: { shift_id: shiftId } })
        .then((r) => r.data),
    enabled: !!shiftId,
  });
}
