import { test, expect } from "@playwright/test";
import { mockApi, signIn } from "./fixtures";

test("proxy batches accept 2000, block 2001, and paginate inventory", async ({ page }) => {
  await mockApi(page);
  const items = Array.from({ length: 2000 }, (_, index) => ({
    id: `px-${index}`,
    proxy: `http://proxy-${index}.example.test:3129`,
    assignment_ready: false,
  }));
  await page.route("**/api/proxies", (route) => route.fulfill({ json: { items } }));
  let imported: unknown;
  await page.route("**/api/proxies/import", async (route) => {
    imported = route.request().postDataJSON();
    await route.fulfill({
      json: { imported: 2000, duplicates: 0, invalid: [], ids: items.map((item) => item.id) },
    });
  });
  await signIn(page);
  await page.goto("/proxies/");
  await expect(page.locator("tbody tr")).toHaveCount(50);
  await page.getByRole("checkbox", { name: "选择代理 px-0", exact: true }).check();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "选择代理 px-50", exact: true })).toBeVisible();
  await expect(page.getByText("已选 1 条", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "选择代理 px-0", exact: true })).toBeChecked();
  const lines = Array.from(
    { length: 2001 },
    (_, index) => `http://proxy-${index}.example.test:3129`,
  );
  await page.getByLabel("批量代理地址").fill(lines.join("\n"));
  await expect(page.getByRole("button", { name: "仅导入", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "导入并测试", exact: true })).toBeDisabled();
  await page.getByLabel("批量代理地址").fill(lines.slice(0, 2000).join("\n"));
  await page.getByRole("button", { name: "仅导入", exact: true }).click();
  await expect.poll(() => imported).toMatchObject({ text: lines.slice(0, 2000).join("\n") });
});

test("account connection exposes persisted automatic recovery control", async ({ page }) => {
  await mockApi(page);
  let enabled = true;
  let saved: unknown;
  await page.route("**/api/runtime-profiles", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "fixture-profile",
            proxy_policy: {
              failover_enabled: enabled,
              country: "US",
              failover_count: 2,
              last_failover_at: "2026-11-01T03:00:00Z",
              runtime_proxy: "http://***:***@proxy.example.test:3129",
            },
          },
        ],
        audit: { total: 1, ok: 1, failed: 0, items: [] },
      },
    }),
  );
  await page.route("**/api/runtime-profiles/proxy-policy", async (route) => {
    saved = route.request().postDataJSON();
    enabled = false;
    await route.fulfill({ json: { profile: { id: "fixture-profile" } } });
  });
  await signIn(page);
  await page.goto("/runtime-profiles/");
  const toggle = page.getByRole("checkbox", { name: "自动换线 fixture-profile", exact: true });
  await expect(toggle).toBeChecked();
  await expect(page.getByText("已切换 2 次", { exact: true })).toBeVisible();
  await toggle.click();
  await expect.poll(() => saved).toEqual({ profile_id: "fixture-profile", enabled: false });
  await expect(toggle).not.toBeChecked();
});
