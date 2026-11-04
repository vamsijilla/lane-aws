import { describe, expect, it } from "vitest";

import { locationText, sourceName } from "@/lib/source-names";

describe("conflict sources", () => {
  it("says where a value was read in a few words", () => {
    expect(locationText(["cell 'Application Info'!L58"])).toBe("cell L58");
    expect(locationText(["cell 'Drivers'!B4", "cell 'Drivers'!D4", "cell 'Drivers'!J4"])).toBe(
      "row 4",
    );
    expect(locationText(["cell 'Units'!E2", "cell 'Units'!E3"])).toBe("column E");
    expect(locationText(["page 1"])).toBe("page 1");
    // A derived value citing one cell on another sheet still reads as its row
    expect(locationText(["cell 'Drivers'!B4", "cell 'Drivers'!D4", "cell 'Application'!E24"])).toBe(
      "row 4",
    );
  });

  it("names a source by where it came from, without the applicant or a split number", () => {
    const app = {
      id: "a",
      doc_type: "application",
      filename: "ACME HAULING - APP.xlsx",
      attachment_id: "f1",
      title: "Application",
    };
    const tab = {
      id: "b",
      doc_type: "driver_schedule",
      filename: "ACME HAULING - APP.xlsx",
      attachment_id: "f1",
      title: "Driver schedule (1 of 2)",
    };
    const list = {
      id: "c",
      doc_type: "driver_schedule",
      filename: "DRIVER LIST - ACME HAULING.xlsx",
      attachment_id: "f2",
      title: "Driver schedule (2 of 2)",
    };
    const docs = [app, tab, list];
    const at = (sheet: string) => [`cell '${sheet}'!B4`];
    expect(sourceName(tab.title, tab, docs, "Acme Hauling", at("Drivers"))).toBe(
      "Application, drivers tab",
    );
    expect(sourceName(list.title, list, docs, "Acme Hauling", at("export"))).toBe("Driver list");
    expect(sourceName("Quick quote form, Acme Hauling", undefined, docs, "Acme Hauling", [])).toBe(
      "Quick quote form",
    );
    expect(locationText(["page 1", "cell 'Application'!E24"])).toBe("page 1");
  });
});
