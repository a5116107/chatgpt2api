import { test, expect } from "@playwright/test";
import { mockApi, signIn } from "./fixtures";

for (const width of [1440, 390]) {
  test(`bulk proxy import, screening and explicit use at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockApi(page);
    let items: Array<Record<string, unknown>> = [];
    let activated = false;
    let tested = false;
    await page.route("**/api/proxies", (route) =>
      route.fulfill({
        json: {
          items: [
            ...items,
            {
              id: "px-shared-entry",
              proxy: "http://***:***@shared.example.test:8080",
              protected: true,
            },
          ],
        },
      }),
    );
    await page.route("**/api/proxies/import", async (route) => {
      expect(route.request().postDataJSON().text).toContain("fixture-secret");
      items = [
        {
          id: "px-imported",
          proxy: "http://***:***@proxy.example.test:3129",
          provider: "自有线路",
          assignment_enabled: false,
        },
      ];
      await route.fulfill({
        json: {
          imported: 1,
          duplicates: 1,
          invalid: [{ line: 3, reason: "地址格式错误" }],
          ids: ["px-imported"],
        },
      });
    });
    await page.route("**/api/proxies/test-pool", async (route) => {
      expect(route.request().postDataJSON().ids).toEqual(["px-imported"]);
      tested = true;
      items[0] = {
        ...items[0],
        assignment_ready: true,
        probe: {
          network_ok: true,
          target_ok: true,
          target_status: 200,
          exit_ip: "192.0.2.5",
          country: "US",
          latency_ms: 100,
          observed_at: new Date().toISOString(),
        },
      };
      await route.fulfill({ json: { tested: 1, ok: 1, failed: 0, skipped: 0, items: [] } });
    });
    await page.route("**/api/proxies/activate", async (route) => {
      expect(tested).toBe(true);
      expect(route.request().postDataJSON()).toEqual({ ids: ["px-imported"], enabled: true });
      activated = true;
      items[0] = { ...items[0], assignment_enabled: true };
      await route.fulfill({ json: { updated: 1, enabled: true } });
    });
    await signIn(page);
    await page.goto("/proxies/");
    await expect(
      page.getByRole("heading", { name: "已添加的代理 · 0 条", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("还没有代理，在上方粘贴地址即可批量添加。", { exact: true }),
    ).toBeVisible();
    await expect(page.locator("tbody")).not.toContainText("shared.example.test");
    await page
      .getByLabel("批量代理地址")
      .fill(
        "http://fixture:fixture-secret@proxy.example.test:3129\nhttp://fixture:fixture-secret@proxy.example.test:3129\ninvalid",
      );
    await page.getByLabel("来源名称").fill("自有线路");
    await page.getByRole("button", { name: "导入并测试", exact: true }).click();
    await expect(page.getByLabel("批量代理地址")).toHaveValue("");
    await expect(
      page.locator("main [role='status']").filter({ hasText: "测试完成：1 条可用" }),
    ).toBeVisible();
    await expect(page.locator("body")).not.toContainText("fixture-secret");
    expect(activated).toBe(false);
    await page.getByRole("button", { name: "使用已通过代理", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("不会自动启动注册");
    await dialog.getByRole("button", { name: "确认启用", exact: true }).click();
    await expect.poll(() => activated).toBe(true);
    await expect(page.getByText("可分配 / 故障备用", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({
      path: `../artifacts/frontend/proxy-pool-${width}.png`,
      fullPage: true,
    });
  });
}
