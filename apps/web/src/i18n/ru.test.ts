import { describe, expect, it } from "vitest";

import { plural } from "./ru";

describe("plural", () => {
  it("picks the Russian form by the count", () => {
    expect(plural(1, "shows.models")).toBe("1 модель");
    expect(plural(3, "shows.models")).toBe("3 модели");
    expect(plural(5, "shows.models")).toBe("5 моделей");
    expect(plural(12, "shows.scenes")).toBe("12 сцен");
    expect(plural(21, "shows.drones")).toBe("21 дрон");
    expect(plural(150, "shows.drones")).toBe("150 дронов");
    expect(plural(0, "stage.violations")).toBe("0 нарушений");
  });
});
