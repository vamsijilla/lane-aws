import type { Metadata } from "next";
import { Suspense } from "react";

import { SignInReturn } from "@/components/shell/sign-in-return";

export const metadata: Metadata = { title: "Signing in" };

export default function SignInCallbackPage() {
  return (
    <div className="px-8 py-16">
      <Suspense fallback={<p className="text-graphite">Signing in</p>}>
        <SignInReturn />
      </Suspense>
    </div>
  );
}
