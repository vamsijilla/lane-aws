import type { Metadata } from "next";

import { HowLaneWorks } from "@/components/how/how-lane-works";

export const metadata: Metadata = { title: "How Lane works" };

export default function Page() {
  return <HowLaneWorks />;
}
