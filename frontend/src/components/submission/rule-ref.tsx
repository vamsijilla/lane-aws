"use client";

import clsx from "clsx";
import Link from "next/link";

import { useSubmissionId } from "@/components/submission/shell";
import { OUTCOMES } from "@/components/ui/chips";
import { Hint } from "@/components/ui/hint";
import type { Schemas } from "@/lib/api";
import { useRules } from "@/lib/queries";

/** What a rule checks and where it stands, for a hint wherever the rule is named. */
export function RuleHint({ rule }: { rule: Schemas["RuleOut"] }) {
  const word = rule.potential_decline
    ? "Potential decline"
    : (OUTCOMES[rule.outcome]?.label ?? rule.outcome);
  return (
    <span className="block space-y-1">
      <span className="block font-medium">
        {rule.rule_id} {rule.name}
      </span>
      {rule.explain ? <span className="block">{rule.explain}</span> : null}
      <span className="block text-graphite italic">&ldquo;{rule.quote}&rdquo;</span>
      <span className="block text-graphite">Now: {word.toLowerCase()}</span>
    </span>
  );
}

/** A rule id that opens the rule on the Rules tab and explains it on hover or focus. */
export function RuleRef({ ruleId, className }: { ruleId: string; className?: string }) {
  const id = useSubmissionId();
  const rules = useRules(id);
  const rule = rules.data?.rules.find((r) => r.rule_id === ruleId);
  const link = (
    <Link
      href={`/submissions/${id}/rules#${ruleId}`}
      className={clsx("font-mono text-[0.92em] hover:text-interstate hover:underline", className)}
    >
      {ruleId}
    </Link>
  );
  return rule ? (
    <Hint asChild content={<RuleHint rule={rule} />}>
      {link}
    </Hint>
  ) : (
    link
  );
}
