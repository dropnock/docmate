import type { ReactNode } from "react";
import { Select, Space } from "antd";
import { useProjectId } from "@shared/hooks/useProjectId";
import { useProjects } from "@shared/hooks/useProjects";

function ProjectSelector({
  projectId,
  onChange,
}: {
  projectId: number | null;
  onChange: (id: number) => void;
}) {
  const { data: projects = [] } = useProjects();
  return (
    <Select
      placeholder="Select project"
      style={{ width: "100%", maxWidth: 220 }}
      value={projectId}
      onChange={onChange}
      options={projects.map((p) => ({ value: p.id, label: p.name }))}
    />
  );
}

interface Props {
  children: (projectId: number) => ReactNode;
}

/** Wraps a project-scoped page: shows the project picker above the content,
 * and a placeholder in place of the page until one is selected. The chosen
 * project is carried in the URL (see useProjectId) so it survives navigation
 * and refresh. */
export default function ProjectScopedRoute({ children }: Props) {
  const [projectId, setProjectId] = useProjectId();

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <Space>
          <span style={{ color: "#595959" }}>Project:</span>
          <ProjectSelector projectId={projectId} onChange={setProjectId} />
        </Space>
      </div>
      {projectId ? (
        children(projectId)
      ) : (
        <div style={{ color: "#8c8c8c", marginTop: 40, textAlign: "center" }}>
          Select a project above to continue.
        </div>
      )}
    </>
  );
}
