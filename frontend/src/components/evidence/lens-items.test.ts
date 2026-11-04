import { describe, expect, it } from "vitest";

import type { Schemas } from "@/lib/api";

import { fieldItems, matchesAttribute, shortTitle } from "./lens-items";

const fact = (id: string, path: string): Schemas["EvidenceFact"] =>
  ({ id, path }) as Schemas["EvidenceFact"];

describe("lens items", () => {
  it("matches a field to its own fact, not a neighbour in the same row", () => {
    // The bug this guards: a driver's date of birth opened the license number cell.
    expect(matchesAttribute("drivers_table[2].date_of_birth", "date_of_birth")).toBe(true);
    expect(matchesAttribute("drivers_table[2].dob", "date_of_birth")).toBe(true);
    expect(matchesAttribute("drivers_table[2].license_number", "date_of_birth")).toBe(false);
    expect(matchesAttribute("drivers[0].cdl_number", "license_number")).toBe(true);
  });

  it("shows only the facts about the field when a source has several", () => {
    const field = {
      attribute: "date_of_birth",
      candidates: [
        {
          display: "age 47",
          superseded: null,
          sources: [
            {
              title: "Driver list",
              facts: [
                fact("lic", "drivers_table[0].license_number"),
                fact("dob", "drivers_table[0].dob"),
              ],
            },
          ],
        },
      ],
    } as unknown as Schemas["FieldDetail"];
    expect(fieldItems(field).map((i) => i.fact?.id)).toEqual(["dob"]);
  });

  it("keeps a derived value with no facts as its own item", () => {
    const field = {
      attribute: "power_units",
      candidates: [
        {
          display: "5",
          superseded: null,
          sources: [{ title: "IFTA", facts: [], note: "Derived" }],
        },
      ],
    } as unknown as Schemas["FieldDetail"];
    const [item] = fieldItems(field);
    expect(item.fact).toBeNull();
    expect(item.source.note).toBe("Derived");
  });

  it("shortens long titles at the first comma", () => {
    expect(shortTitle("MVR, State driver record, Doe")).toBe("MVR");
    expect(shortTitle("A".repeat(40))).toHaveLength(33);
  });

  it("opens every fact a worked-out value rests on, marked as worked out by code", () => {
    const field = {
      attribute: "owner_drives",
      candidates: [
        {
          display: "Yes",
          superseded: null,
          sources: [
            {
              title: "Driver schedule",
              doc_type: "derived",
              note: "The owner is listed as a driver",
              facts: [fact("name", "rows[0].last_name"), fact("mark", "rows[0].hire_date")],
            },
          ],
        },
        {
          display: "No",
          superseded: null,
          sources: [{ title: "Application", facts: [fact("answer", "owner_is_driver")] }],
        },
      ],
    } as unknown as Schemas["FieldDetail"];
    const items = fieldItems(field);
    expect(items.map((i) => [i.fact?.id, i.derived])).toEqual([
      ["name", true],
      ["mark", true],
      ["answer", false],
    ]);
  });
});
