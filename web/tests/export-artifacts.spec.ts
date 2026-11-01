import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

for (const broken of [false, true])
  test("static export rejects nested segments: " + broken, async ({}, testInfo) => {
    const root = testInfo.outputPath("export");
    await mkdir(resolve(root, "login"), { recursive: true });
    await writeFile(resolve(root, "login/index.html"), "<!DOCTYPE html>");
    await mkdir(resolve(root, "tasks"), { recursive: true });
    if (broken) {
      await mkdir(resolve(root, "tasks/__next.tasks"));
      await writeFile(resolve(root, "tasks/__next.tasks/__PAGE__.txt"), "segment");
    } else await writeFile(resolve(root, "tasks/__next.tasks.__PAGE__.txt"), "segment");
    const result = spawnSync(process.execPath, ["scripts/verify-export.mjs", root], {
      encoding: "utf8",
    });
    expect(result.status, result.stderr).toBe(broken ? 1 : 0);
    expect(result.stdout + result.stderr).toContain(
      broken ? "Invalid segmented static export" : "Static export verified",
    );
  });
