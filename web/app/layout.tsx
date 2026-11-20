import type { Metadata } from "next";
import "@adem.rejeb/ui/styles.css";
import "./globals.css";

import { ThemeProvider, ThemeScript } from "@adem.rejeb/ui";

export const metadata: Metadata = {
  title: { default: "Basacore", template: "%s · Basacore" },
  description: "Manage applications, organizations, workspaces and access in one place.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <ThemeProvider defaultTheme="dark">{children}</ThemeProvider>
      </body>
    </html>
  );
}
