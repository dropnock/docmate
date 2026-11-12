import api from "@shared/api/client";
import type {
  AuditEvent, AvailableStaff, FieldResult, Lot, LotDetail, LotInspectionDetail, LotInspectionList,
  LotInspectionReport, QcFieldResultsOut,
} from "@shared/types";

/** Shared across CustomerLotManager, LotSettingsDrawer, and LotAssignQcDrawer
 * — all three need the same lot-detail query, so the key must be the same
 * everywhere for cache/invalidation to actually line up. */
export const lotDetailKey = (lotId?: number) => ["lot-detail", lotId] as const;

export function getLot(lotId: number) {
  return api.get<LotDetail>(`/lots/${lotId}`).then((r) => r.data);
}

export function getQcAgents(projectId: number) {
  return api.get<AvailableStaff[]>(`/projects/${projectId}/qc-agents`).then((r) => r.data);
}

/** sampleRate is only meaningful when the project's AQLConfig.sampling_mode
 * is "manual" — omit it entirely in "iso" mode, the backend computes the
 * size itself and 422s if a rate is supplied. */
export function applySample(lotId: number, sampleRate?: number) {
  return api.post<Lot>(`/lots/${lotId}/sample`, { sample_rate: sampleRate ?? null });
}

export function createQcBatches(
  lotId: number,
  body: { project_id: number; document_type_id: number; assignments: { agent_id: number; record_ids: number[] }[] }
) {
  return api.post(`/lots/${lotId}/qc-batches`, body);
}

export function sendForRemediation(lotId: number) {
  return api.post<Lot>(`/lots/${lotId}/send-for-remediation`);
}

export function getQcFieldResults(lotId: number) {
  return api.get<QcFieldResultsOut>(`/lots/${lotId}/qc-field-results`).then((r) => r.data);
}

// ── Outgoing inspection (digitizing supervisor) ─────────────────────────────

export const lotInspectionsKey = (lotId?: number) => ["lot-inspections", lotId] as const;
export const lotInspectionKey = (lotId?: number, inspectionId?: number) =>
  ["lot-inspection", lotId, inspectionId] as const;
export const lotInspectionReportKey = (lotId?: number) => ["lot-inspection-report", lotId] as const;

export function listInspections(lotId: number) {
  return api.get<LotInspectionList>(`/lots/${lotId}/inspections`).then((r) => r.data);
}

export function startInspection(lotId: number, aqlLevel: number) {
  return api
    .post<LotInspectionDetail>(`/lots/${lotId}/inspections`, { aql_level: aqlLevel })
    .then((r) => r.data);
}

export function getInspection(lotId: number, inspectionId: number) {
  return api.get<LotInspectionDetail>(`/lots/${lotId}/inspections/${inspectionId}`).then((r) => r.data);
}

export function reviewInspectionItem(
  lotId: number, inspectionId: number, recordId: number, fieldResults: FieldResult[],
) {
  return api.put(`/lots/${lotId}/inspections/${inspectionId}/items/${recordId}`, {
    field_results: fieldResults,
  });
}

export function completeInspection(lotId: number, inspectionId: number, summaryNote?: string) {
  return api
    .post<LotInspectionDetail>(`/lots/${lotId}/inspections/${inspectionId}/complete`, {
      summary_note: summaryNote || null,
    })
    .then((r) => r.data);
}

export function cancelInspection(lotId: number, inspectionId: number) {
  return api.post(`/lots/${lotId}/inspections/${inspectionId}/cancel`);
}

export function getInspectionReport(lotId: number) {
  return api.get<LotInspectionReport>(`/lots/${lotId}/inspection-report`).then((r) => r.data);
}

export const lotHistoryKey = (lotId?: number) => ["lot-history", lotId] as const;

/** The lot's own audit trail. The customer portal only gets events from the
 * lot's first release onward (and 404s for a draft). */
export function getLotHistory(lotId: number) {
  return api.get<AuditEvent[]>(`/lots/${lotId}/history`).then((r) => r.data);
}
