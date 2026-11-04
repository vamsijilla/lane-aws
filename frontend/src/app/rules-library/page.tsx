import type { Metadata } from "next";

import { RulesLibrary } from "@/components/rules-library/rules-library";

export const metadata: Metadata = { title: "Rules library" };

export default function Page() {
  return <RulesLibrary />;
}
