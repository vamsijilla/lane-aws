/** What each status word means for the submission, for hints beside the word. */
export const STATUS_HINT: Record<string, string> = {
  "potential decline":
    "A decline if a disputed fact turns out one way. A person settles the fact before it stands.",
  decline: "The guideline does not allow this account.",
  refer: "An underwriter must review this before a quote.",
  missing: "A required document or answer is not in the submission. It is in the agent request.",
  conflict: "Documents disagree on a fact that can change an outcome. A person picks the value.",
  unknown: "The documents cannot answer this yet. It refers until they do.",
  "cannot check": "The documents cannot answer this yet. It refers until they do.",
  "can’t check yet": "The documents cannot answer this yet. It refers until they do.",
  flag: "Worth a look, but it does not by itself change the route.",
  note: "Worth a look, but it does not by itself change the route.",
  pass: "The documents meet this part of the guideline.",
  "n/a": "Does not apply to this account.",
  "at quote": "Needs outside data (SAFER, premium). The underwriter checks it at quote.",
};

export function statusHint(word: string): string | undefined {
  return STATUS_HINT[word.toLowerCase()];
}
