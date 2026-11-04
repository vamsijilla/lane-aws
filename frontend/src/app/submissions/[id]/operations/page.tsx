import type { Metadata } from "next";

import { Operations } from "@/components/submission/operations";

export const metadata: Metadata = { title: "Applicant and operations" };

export default function Page() {
  return <Operations />;
}
