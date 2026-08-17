import { describe, expect, it } from "vitest";

import { bandDisplay } from "./reviewMeta";

describe("bandDisplay", () => {
  it("projects every backend band, including LOWEST as INFERRED", () => {
    expect(bandDisplay("HIGHEST")).toEqual({ label: "VERIFIED 96", tone: "verified", percent: 96 });
    expect(bandDisplay("HIGH")).toEqual({ label: "VERIFIED 92", tone: "verified", percent: 92 });
    expect(bandDisplay("MEDIUM")).toEqual({ label: "PROBABLE 78", tone: "probable", percent: 78 });
    expect(bandDisplay("SINGLE")).toEqual({ label: "PROBABLE 70", tone: "probable", percent: 70 });
    expect(bandDisplay("LOWEST")).toEqual({ label: "INFERRED 55", tone: "inferred", percent: 55 });
  });

  it("projects unknown bands to the weakest tier, same as LOWEST", () => {
    expect(bandDisplay("bogus")).toEqual({ label: "INFERRED 55", tone: "inferred", percent: 55 });
  });
});
