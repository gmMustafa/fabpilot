import { Plus } from "lucide-react";
import Link from "next/link";

import { Page, PageHeader } from "@/components/page-header";
import { PlantOverview } from "@/components/plant-overview";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// The static shell (header) is prerendered; the overview is a Client Component that loads and
// refreshes itself in the browser.
export default function Home() {
  return (
    <Page>
      <PageHeader
        eyebrow="Live operations"
        title="Production lines"
        description="Every machine on the three lines with its active alarms. Refreshes every 10 seconds; open a machine for its sensor history."
        actions={
          <Link href="/incidents/new" className={cn(buttonVariants())}>
            <Plus aria-hidden /> New investigation
          </Link>
        }
      />
      <PlantOverview />
    </Page>
  );
}
