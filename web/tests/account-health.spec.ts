import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { accounts, mockApi, signIn } from "./fixtures";

const check = {
  state: "browser_verification_required",
  phase: "connection",
  checked_at: Date.now() / 1000,
  http_status: 403,
  duration_ms: 180,
  stale: false,
};
const job = {
  progress_id: "health-fixture",
  started_at: Date.now() / 1000,
  total: 3,
  processed: 0,
  done: false,
  error: null,
  counts: {},
};

test("check all shows progress, distinguishes browser verification and preserves base status", async ({
  page,
}) => {
  await mockApi(page);
  await page.route("**/api/accounts/health-check/current", (route) =>
    route.fulfill({ json: { job: null } }),
  );
  let completed = false;
  const requests: unknown[] = [];
  await page.route("**/api/accounts/health-check", (route) => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ json: job });
  });
  let polls = 0;
  await page.route("**/api/accounts/health-check/health-fixture", (route) => {
    completed = ++polls > 1;
    return route.fulfill({
      json: {
        ...job,
        processed: completed ? 3 : 1,
        done: completed,
        counts: { browser_verification_required: completed ? 3 : 1 },
      },
    });
  });
  await page.route("**/api/accounts", (route) =>
    route.fulfill({
      json: {
        items: accounts.map((item) => ({ ...item, health_check: completed ? check : null })),
      },
    }),
  );
  await signIn(page);
  await page.goto("/accounts/");
  await page.getByRole("button", { name: "检测账号状态", exact: true }).click();
  await expect(page.getByTestId("account-health-summary")).toContainText("正在检测账号");
  await expect(page.getByTestId("account-health-summary")).toContainText("检测完成 · 3 / 3");
  expect(requests).toEqual([{ access_tokens: [] }]);
  const status = page.getByTestId("account-health-status").first();
  await status.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(status).toContainText("这不代表账号被封或额度耗尽");
  await expect(status).toContainText("HTTP 403");
  await expect(status).toContainText("检测时间");
  await expect(page.getByText("正常", { exact: true }).first()).toBeVisible();
});

test("selection sends only selected tokens and a failed start stays retryable", async ({
  page,
}) => {
  await mockApi(page);
  await page.route("**/api/accounts/health-check/current", (route) =>
    route.fulfill({ json: { job: null } }),
  );
  let payload: unknown;
  await page.route("**/api/accounts/health-check", (route) => {
    payload = route.request().postDataJSON();
    return route.fulfill({ status: 503, json: { detail: { error: "检测服务暂时繁忙" } } });
  });
  await signIn(page);
  await page.goto("/accounts/");
  await page
    .getByRole("row")
    .filter({ hasText: "creator@example.test" })
    .getByRole("checkbox")
    .click();
  await page.getByRole("button", { name: "检测所选（1）" }).click();
  await expect(page.getByText("检测服务暂时繁忙", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "检测所选（1）" })).toBeEnabled();
  expect(payload).toEqual({ access_tokens: [accounts[0].access_token] });
});

test("reload resumes a running check without submitting another batch", async ({ page }) => {
  const api = await mockApi(page);
  await page.route("**/api/accounts/health-check/current", (route) =>
    route.fulfill({ json: { job } }),
  );
  await page.route("**/api/accounts/health-check/health-fixture", (route) =>
    route.fulfill({ json: { ...job, processed: 3, done: true, counts: { login_verified: 3 } } }),
  );
  await signIn(page);
  await page.goto("/accounts/");
  await expect(page.getByTestId("account-health-summary")).toContainText("检测完成 · 3 / 3");
  await expect(page.getByTestId("account-health-summary")).toContainText("登录有效 3");
  expect(api.writes.filter((path) => path.includes("health-check"))).toEqual([]);
});

test("stale results and successful login have clear capability limits on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.route("**/api/accounts/health-check/current", (route) =>
    route.fulfill({ json: { job: null } }),
  );
  await page.route("**/api/accounts", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            ...accounts[0],
            health_check: { ...check, state: "login_verified", phase: "login", http_status: 200 },
          },
          { ...accounts[1], health_check: { ...check, stale: true } },
        ],
      },
    }),
  );
  await signIn(page);
  await page.goto("/accounts/");
  const status = page.getByTestId("account-health-status").first();
  await status.locator("summary").click();
  await expect(status).toContainText("各模型的权限与额度仍以实际请求为准");
  const stale = page.getByTestId("account-health-status").nth(1);
  await stale.locator("summary").click();
  await expect(stale).toContainText("待重新检测");
  await expect(stale).toContainText("上次结果：需要浏览器验证");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const accessibility = await new AxeBuilder({ page })
    .include('[data-testid="account-health-summary"]')
    .include('[data-testid="account-health-status"]')
    .analyze();
  expect(accessibility.violations).toEqual([]);
});
