import { SectionHeader } from '../components/ui.js';
import { StudioV2 } from './StudioV2.js';

// The Studio authors executable-v2, the only format the engine runs. Workflow-package v1
// authoring and publishing were retired (ADR-0013 step 3); stored v1 versions stay readable
// through the API.
export function StudioHome({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  return (
    <div className="studio-home">
      <SectionHeader
        eyebrow="Build"
        title="Studio"
        description="Author a workflow: the steps it runs, the systems it reads and writes, and its limits. The server checks every change; you sign it with your publisher key before it is published."
      />
      <StudioV2 workspace={workspace} projectId={projectId} role={role} />
    </div>
  );
}
