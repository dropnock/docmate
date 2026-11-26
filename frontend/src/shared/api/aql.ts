import api from "@shared/api/client";
import type { AQLConfig, AuditEvent, ClassResults, GeneralLevel, InspectionLevel, Severity } from "@shared/types";

export const aqlConfigKey = (projectId?: number) => ["aql-config", projectId] as const;

export function getAqlConfig(projectId: number) {
  return api.get<AQLConfig>(`/projects/${projectId}/aql`).then((r) => r.data);
}

export function updateAqlConfig(projectId: number, body: Partial<AQLConfig>) {
  return api.patch<AQLConfig>(`/projects/${projectId}/aql`, body).then((r) => r.data);
}

/** The single sample and per-class Ac/Re a lot of lotSize gets with these
 * AQLs (critical null = zero tolerance). */
export function getClassPlanPreview(
  lotSize: number,
  aqls: Record<Severity, number | null>,
  inspectionLevel: InspectionLevel,
  generalLevel: GeneralLevel,
) {
  return api
    .get<{ code_letter: string; sample_size: number; classes: ClassResults }>("/aql/class-plan", {
      params: {
        lot_size: lotSize,
        major_aql: aqls.major,
        minor_aql: aqls.minor,
        ...(aqls.critical === null ? {} : { critical_aql: aqls.critical }),
        inspection_level: inspectionLevel,
        general_level: generalLevel,
      },
    })
    .then((r) => r.data);
}

export function getSampleSizePreview(
  batchSize: number,
  aqlLevel: number,
  inspectionLevel: InspectionLevel,
  generalLevel: GeneralLevel,
) {
  return api
    .get<{ sample_size: number; acceptance_number: number; aql_level: number }>("/aql/sample-size", {
      params: { batch_size: batchSize, aql_level: aqlLevel, inspection_level: inspectionLevel, general_level: generalLevel },
    })
    .then((r) => r.data);
}

export const projectHistoryKey = (projectId?: number) => ["project-history", projectId] as const;

/** The project's audit trail, newest first (digitizing admins and supervisors). */
export function getProjectHistory(projectId: number) {
  return api.get<AuditEvent[]>(`/projects/${projectId}/history`).then((r) => r.data);
}
