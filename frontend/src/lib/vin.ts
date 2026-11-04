/** A VIN's anatomy, for display: what each position means and the check digit worked out. */

const VALUES: Record<string, number> = {
  A: 1, B: 2, C: 3, D: 4, E: 5, F: 6, G: 7, H: 8, J: 1, K: 2, L: 3, M: 4, N: 5, P: 7, R: 9,
  S: 2, T: 3, U: 4, V: 5, W: 6, X: 7, Y: 8, Z: 9,
}; // prettier-ignore
const WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2];
const YEARS = "ABCDEFGHJKLMNPRSTVWXY123456789"; // 1980 (A) ... 2009 (9), then again from 2010

export type Segment = { label: string; from: number; to: number; text: string };

export const SEGMENTS: [string, number, number][] = [
  ["Maker", 1, 3],
  ["Descriptor", 4, 8],
  ["Check digit", 9, 9],
  ["Model year", 10, 10],
  ["Plant", 11, 11],
  ["Serial", 12, 17],
];

export function segments(vin: string): Segment[] {
  return SEGMENTS.map(([label, from, to]) => ({ label, from, to, text: vin.slice(from - 1, to) }));
}

/** The check digit a 17-character VIN should carry ('0'-'9' or 'X'), or null. */
export function checkDigit(vin: string): string | null {
  if (vin.length !== 17) return null;
  let sum = 0;
  for (let i = 0; i < 17; i++) {
    const c = vin[i].toUpperCase();
    const v = /\d/.test(c) ? Number(c) : VALUES[c];
    if (v === undefined) return null;
    sum += v * WEIGHTS[i];
  }
  const r = sum % 11;
  return r === 10 ? "X" : String(r);
}

/** The model years a year code can mean (it repeats every 30 years). */
export function modelYears(code: string): number[] {
  const i = YEARS.indexOf(code.toUpperCase());
  return i < 0 ? [] : [1980 + i, 2010 + i];
}
