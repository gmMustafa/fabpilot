import { GatewayStats } from "@/components/gateway-stats";
import { Page, PageHeader } from "@/components/page-header";

export default function GatewayPage() {
  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="LLM usage"
        description="Free models only: requests are the scarce resource. Counts and model names, never prompts."
      />
      <GatewayStats />
    </Page>
  );
}
