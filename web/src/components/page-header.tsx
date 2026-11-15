/**
 * One page title for every page (UI pass): the 32 px heading, a description in the body size,
 * optional actions on the right. Consistent headers are most of what makes pages feel like ONE
 * product instead of a set of screens.
 */
import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div className="space-y-1">
        {eyebrow && (
          <div className="text-xs font-semibold tracking-wider text-primary uppercase">
            {eyebrow}
          </div>
        )}
        <h1 className="text-heading font-semibold tracking-tight">{title}</h1>
        {description && <p className="max-w-3xl text-body text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

/** The content frame of every page: the same width and spacing everywhere. */
export function Page({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
      {children}
    </main>
  );
}
