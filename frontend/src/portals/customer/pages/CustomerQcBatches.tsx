import PageHeader from "@shared/components/PageHeader";
import QcBatchesTable from "../components/QcBatchesTable";

/** Every QC batch handed out on the project, across all lots: who holds it,
 * how far it has got, and Reassign. Batches are created from a lot's
 * "Assign to QC" drawer on the Lots page. */
export default function CustomerQcBatches({ projectId }: { projectId: number }) {
  return (
    <div>
      <PageHeader title="QC Batches" />
      <QcBatchesTable projectId={projectId} />
    </div>
  );
}
