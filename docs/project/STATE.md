# ChatGPT2API 交付状态

更新：2026-11-01（Asia/Shanghai）。首页凭据隔离与死锁修复已发布；真实对话/生图仍受上游浏览器验证 403 阻塞，业务恢复验收未通过。

## 目标与唯一任务清单

- 当前目标：账号登录/线路状态可见；只读检测不刷新令牌、不切换代理、不删除账号、不发起生成；保持真实模型验证缺口可见。
- 唯一清单：`H:/Root/jiaoben/zhongzhuang/prox/ops/proxy-stack/TODO.md`。P0–P6、P9–P14、P16/P17 完成；P7 清理审核阻断、P8/P15 真实业务仍受上游验证阻塞。
- 原地修改；用户已明确授权将本轮已发布改动提交并推送到当前任务分支；不重置现有改动；另一工作树 `api-acica-top-otp-primary` 不动。不注册新账号，不通过账号/出口轮换规避权限或配额。

## 当前生产与回滚

- 主机 38.76.168.140，容器 `api-acica-top-app`，映射 `127.0.0.1:23020 -> 80/tcp`；SSH 使用既有信任指纹和本地系统代理。
- 应用 `runtimefix-20261101T084031Z`，镜像 `sha256:5ba280b77064a64c18d933137cae49a32186e6ae37e0a9a7a59251d41f3fa588`；直接回滚 `runtimefix-20261101T054825Z` 保留。
- 平台 `proxy-project-20261031T232551Z`，直接回滚 `proxy-project-20261031T231741Z` 保留；本次不修改平台服务。
- 新发布只含白名单源码和静态资源；账号、授权、邮箱源、注册和代理库存不打包覆盖。切换前检查在途媒体任务，保留服务器端 0600 账号快照。
- 激活、独立 verify、检测后复核通过：本次发布前实际 2 个账号，发布与检测后仍为 2 个；登录凭据、绑定、授权、配置与注册设置保持。此前 7 个是历史观测，不回填旧文件；本次差异原因未推定。服务器端账号快照 0600。

## 已发布功能

- 项目隔离代理状态/反馈，三协议同出口、时效/地区与容量分维度，原固定节点、连接 40 和准入速率 1200/分钟保持。
- 21 条自有来源通过分层探测并启用；共享五组加自有来源共 26 个绑定。地区 US 9、DE 4、FR 3、BR 3、CA 2。接口 200 不等于模型业务可用。
- 导入/启用每批最多 2000；分页 50、测试分段 30、后端 10 路探测，每段合并保存。后台按库存自适应复查，保留并发库存修改。
- 每账号可关闭连接恢复；仅传输/代理认证故障触发，候选同地区、新鲜合格、启用且空闲，300 秒冷却、绑定 CAS 与保存失败回滚。
- GET/HEAD 非流式有界恢复；POST/流式不重复执行，目标 401/403/429 不换线路。删除 bootstrap 任意失败直连/关闭证书验证路径。
- 全站中性工作区、固定顶栏、导航和工具栏；对话/图像独立历史栏，管理、登录、设置、视频和资料库布局一致。
- 现网 13 页 × 桌面/手机/手机深色共 39 个页面验收，页面异常、业务写入和异常响应均为 0；键盘搜索与导航通过。

## 本次缺陷及验收

- 生产日志证明旧图单张重试已创建新 ID；同一阶段新建、编辑和重试均出现 bootstrap 403，不能将其解释为旧任务 ID 被复用。
- 当前账号曾观测 7 个 Free（4 正常、3 异常），注册停止；与早前 0 个账号属于不同时点，不恢复旧账号文件或重置统计。
- 使用现有绑定单次 GET 诊断：`chatgpt.com /` HTTP 403，页面正文 `Enable JavaScript and cookies to continue`。保持访问拒绝，不实现换出口绕过。
- 修复：单张提交失败不再让已受理兄弟任务失败；每轮只处理自己提交的任务 ID，晚到响应不能污染新的重试；参考文件和原参数保留。
- 修复：Chat Completions / Responses / Messages / Aurora 在选账号前传递请求模型；对话排除认证冷却，健康优先且同级按最近分配公平；图片在途槽和同级公平分配。
- 修复：轻量探测不会清除有效生图冷却、重新启用禁用/耗尽账号。失败任务与日志保留任务 ID 和脱敏账号哈希，界面区分可分配与待验证。
- 新增浏览器验证错误标识与说明，HTML 不再挤入错误卡；明确账号数和额度不是实测业务成功。
- 本地和隔离镜像后端各 `254 tests / OK`；静态生产浏览器 `96 passed (52.3s)`，含生成/编辑单张重试、部分提交失败与刷新恢复；Linux 构建 179 个静态文件通过，typecheck/lint/format、Ruff 和 diff 检查通过。
- 新版线上账号/对话/图像三页 × 桌面/手机/手机深色共 9 个页面通过，异常响应、页面错误、业务写入均 0，键盘搜索/导航通过；账号表截图遮蔽敏感信息。
- 原失败图按保留日志找回原提示词/模型/尺寸/质量，单次新任务重试：原任务未改、新任务 ID 不同、选择账号哈希可追踪，结果 `upstream_browser_verification_required`，未生成图片；单次 auto 对话 502，根因为 403。
- 前一发布本地/隔离镜像各 220 项、浏览器 92 项通过。后续严格整账号文件哈希校验曾因健康更新失败；独立业务配置/源码/运行参数校验通过，未将该失败写成通过。

## 账号状态检测（P16）

- 显式检测全部/所选、实时进度、刷新恢复进行中任务、逐账号展开原因/时间/阶段/HTTP 状态；最多 3 路，每请求最多 2000 个，活动批次复用、繁忙账号跳过。
- 独立只读会话，最多首页和 /backend-api/me 两次 GET；禁止重放/跳转，不刷新凭据、不写画像、不换线路、不改变状态/额度、不调用对话/生成。只合并检测元数据，保存失败回滚，删除/令牌/线路变化后不写回陈旧结果。
- 区分浏览器验证、登录失效、上游拒绝、地区限制、限流、代理认证/隧道拒绝、TLS/网络故障及无效响应；只保存稳定标识，不保存原始 HTML/错误或认证代理 URL。登录有效不等于模型可用，模型面板改为“已配置模型”。
- 结果 15 分钟有效，1 分钟内重复请求复用，令牌/有效代理/画像变化立即标旧；页面每 30 秒更新过期显示，不另建自动探测轮询线程。
- 本地和隔离镜像各 269 项后端、100 项静态生产浏览器通过；Linux 构建 179 文件，typecheck/lint/format、Ruff 通过。
- 线上只读检测 2/2 完成，均 browser_verification_required，connection 阶段 HTTP 403；凭据/绑定前后相同，注册保持停止，未发起生成；实际业务仍未恢复。新版账号/对话/图像三页九种视图通过，页面错误、异常响应及业务写入均 0，键盘导航/搜索通过。

## 阻塞与保留

- 真实业务尚无本轮成功模型结果：对话和图片均观测上游 403；视频 `video_provider_not_configured`。正常登录环境的上游验证和真实视频服务是业务验收缺口。
- 后续真实验证仅使用现有账号、有界单任务；不新注册、不对明确拒绝反复轮账号/出口。
- P7：7 个旧解包目录约 473 MiB 递归删除遭自动审批两次拒绝，原因 blocked by policy，保留且不绕过。
- 已执行远端限定清理实测释放 5,598,797,824 字节；216,371 条过期会话删除但未 VACUUM，不把删行当作整机释放。当前/回滚、备份、Redis 租约和配置保留。
- 原工作树及既有改动保留；本轮按用户最新授权提交和推送应用改动，无新增工作树，不修改另一工作树；生成证据均在 `artifacts/`。

## 证据

- 状态检测：`artifacts/frontend/health-backend-all.txt`、`health-browser-tests.txt`、`health-build.txt`、`health-prepare.txt`、`health-activate.txt`、`health-verify.txt`、`health-online-acceptance.json`、`health-post-business-verified.txt`、`health-online-ui.txt`。
- 上轮修复：`artifacts/frontend/retry-backend-all.txt`、`retry-browser-tests.txt`、`retry-build.txt`、`retry-prepare.txt`、`retry-activate.txt`、`retry-verify.txt`、`retry-online-acceptance.json`、`retry-online-ui.txt`、`test-results.json`。
- 复现：`retry-scheduler-before.txt` 六项失败、`image-retry-before.txt` 部分提交失败、`probe-cooldown-before.txt` 冷却/禁用状态被探测清除；对应测试已修复通过。
- 上一发布：`current-activate.txt`、`current-post-business-verified.json`、`current-business-acceptance.json`、`workbench-live-verification.json`；平台历史证据位于 `artifacts/proxy-platform/`。

## 首页请求边界与服务死锁修复（P17）

- py-spy 证实风控同步持 risk 锁等待 account 锁，账号选择持 account 锁等待 risk 锁；注册 SSE 又在事件循环同步等锁，导致登录页超时。修正 sync/update 的锁顺序；注册普通路由走线程池，SSE 通过 asyncio.to_thread 取快照。
- 首页公共请求从 Session 默认头和请求头移除 Authorization，finally 恢复认证头；生成 bootstrap 与只读检测复用同一实现，禁止自动跳转。明确 403/429 不再经旧空正文/requirements/TLS 分支换账号或重复提交，保留真实状态。
- 现场备份后受控重启恢复响应，4/4 凭据和绑定即时一致，注册标记 interrupted_by_restart、不续跑；之后旧后台 image_quota_probe 观察到 refresh_token_invalidated 并改变库存，未回写旧凭据。最终重新 prepare 时 2 个账号，activate/verify/检测/业务验收前后保持 2 个。
- 本地和隔离镜像各 273 tests / OK；100 项静态产物浏览器回归、typecheck/lint、Ruff 通过；复用校验哈希一致的 Linux 构建 179 文件。发布及 post-business verify 通过，监控/故障切换错误计数均 0。线上 13 页桌面/手机/深色共 39 视图通过，页面错误、异常响应、业务写入均 0，导航与键盘搜索通过。
- 线上只读检测 2/2 均 connection 阶段 browser_verification_required / 403；单次旧图原参数新任务失败同标识，历史任务不变；单次 auto 对话 502、根因为 403。无新注册；真实业务仍未恢复，P8/P15 保持未完成。
- 证据：artifacts/frontend/final403-prepare.txt、final403-activate.txt、final403-verify.txt、final403-health-acceptance.txt、final403-business-acceptance.txt、final403-post-business-verify.txt、final403-live-ui.txt；现场私密快照在远端 releases/diagnostics/incident-20261101T082946Z。
