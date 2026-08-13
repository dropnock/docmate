import api from "@shared/api/client";
import type { AvailableStaff, Lot, LotDetail, QcFieldResultsOut } from "@shared/types";

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
