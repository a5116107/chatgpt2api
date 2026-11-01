# chatgpt2api 共享代理平台接入

完整设计与 P0–P9 定义已合并至共享平台仓库 `docs/project/PROXY_PLATFORM_PLAN.md`。

- 共享平台工作区：`H:/Root/jiaoben/zhongzhuang/prox/ops/proxy-stack`。
- 唯一执行清单：该仓库 `TODO.md` 的“共享代理平台与项目接入（2026-11-01）”。
- 应用状态与真实业务阻塞继续在本仓库 `STATE.md` 维护，不复制第二份清单。
- 接入只使用既有项目凭据；平台连接数、本地 profile 绑定数、账号额度与注册任务目标分列。
- 共享状态与反馈经明确配置的可信 HTTPS 平台接口；不共享全局 runtime/admin token，不新增账号，不轮换出口规避上游限制。
