import type { Metadata } from "next";

import { Evals } from "@/components/evals/evals";

export const metadata: Metadata = { title: "Evals" };

export default function Page() {
  return <Evals />;
}
