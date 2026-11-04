import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Newsreader, Overpass } from "next/font/google";

import { Providers } from "@/components/providers";
import { AppShell } from "@/components/shell/app-shell";
import { DENSITY_BOOTSTRAP } from "@/lib/density";
import { THEME_BOOTSTRAP } from "@/lib/theme";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });
// Highway-sign numerals: only the mile markers and the large counts on How Lane works
// Editorial serif: page titles, the verdict sentence and the summary prose
const newsreader = Newsreader({ subsets: ["latin"], variable: "--font-newsreader" });
const overpass = Overpass({ subsets: ["latin"], weight: ["700"], variable: "--font-overpass" });

export const metadata: Metadata = {
  title: { default: "Lane", template: "%s | Lane" },
  description: "Submission intake and review for commercial auto underwriting",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${mono.variable} ${overpass.variable} ${newsreader.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
        <script dangerouslySetInnerHTML={{ __html: DENSITY_BOOTSTRAP }} />
      </head>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
