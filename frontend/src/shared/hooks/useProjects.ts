import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { Project } from "@shared/types";

export function useProjects() {
  return useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });
}
