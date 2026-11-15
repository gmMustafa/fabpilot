import { describe, expect, it } from "vitest";

import { buttonVariants } from "@/components/ui/button";

import { cn } from "./utils";

describe("cn", () => {
  it("keeps a text colour next to a theme font size", () => {
    expect(cn("text-primary-foreground", "text-button")).toBe(
      "text-primary-foreground text-button",
    );
  });

  it("still lets a theme font size replace a standard one", () => {
    expect(cn("text-sm", "text-button")).toBe("text-button");
  });

  // what <Button> renders: cva only JOINS the classes, cn resolves the conflicts
  it.each(["default", "lg"] as const)("gives the primary button (%s) white text", (size) => {
    const classes = cn(buttonVariants({ size })).split(" ");
    expect(classes).toContain("text-primary-foreground");
    expect(classes).toContain("text-button");
    expect(classes).not.toContain("text-sm"); // the size's font size replaced the base one
  });
});
