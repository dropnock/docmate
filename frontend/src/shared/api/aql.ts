import api from "@shared/api/client";
import type { AQLConfig } from "@shared/types";

export const aqlConfigKey = (projectId?: number) => ["aql-config", projectId] as const;

export function getAqlConfig(projectId: number) {
  return api.get<AQLConfig>(`/projects/${projectId}/aql`).then((r) => r.data);
}

export function updateAqlConfig(projectId: number, body: Partial<AQLConfig>) {
  return api.patch<AQLConfig>(`/projects/${projectId}/aql`, body).then((r) => r.data);
}

export function getSampleSizePreview(batchSize: number, aqlLevel: number) {
  return api
    .get<{ sample_size: number; acceptance_number: number; aql_level: number }>("/aql/sample-size", {
      params: { batch_size: batchSize, aql_level: aqlLevel },
    })
    .then((r) => r.data);
}
