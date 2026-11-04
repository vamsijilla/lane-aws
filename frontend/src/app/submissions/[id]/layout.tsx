import { Suspense } from "react";

import { SubmissionShell } from "@/components/submission/shell";
import { Skeleton } from "@/components/ui/primitives";

export default function SubmissionLayout({ children, params }: LayoutProps<"/submissions/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="px-8 py-6">
          <Skeleton lines={8} />
        </div>
      }
    >
      <Resolved params={params}>{children}</Resolved>
    </Suspense>
  );
}

async function Resolved({
  params,
  children,
}: {
  params: LayoutProps<"/submissions/[id]">["params"];
  children: React.ReactNode;
}) {
  const { id } = await params;
  return <SubmissionShell id={id}>{children}</SubmissionShell>;
}
