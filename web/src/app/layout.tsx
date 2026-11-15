import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { Suspense } from "react";

import "./globals.css";
import { ApiStatus, ApiStatusFallback } from "@/components/api-status";
import { AppSidebar } from "@/components/app-sidebar";
import { Providers } from "@/components/providers";
import { cn } from "@/lib/utils";

// IBM Plex: designed for an engineering company, free; next/font downloads it at BUILD time
// and serves it from our own origin (no request to Google from the user's browser).
const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex-sans",
});
const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: "FabPilot",
  description: "AI maintenance copilot for factories",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={cn("font-sans", sans.variable, mono.variable)}>
      <body>
        <Providers>
          <div className="flex min-h-screen flex-col lg:flex-row">
            <AppSidebar
              status={
                // The API status is uncached runtime data: its own Suspense boundary, so the
                // shell (sidebar, page) never waits for /health.
                <Suspense fallback={<ApiStatusFallback />}>
                  <ApiStatus />
                </Suspense>
              }
            />
            <div className="min-w-0 flex-1">{children}</div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
