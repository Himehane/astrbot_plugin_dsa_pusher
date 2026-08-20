# Changelog / 更新日志

## [v1.4.4] - 2026-08-20

- **新增 DSA astrbot 渠道签名兼容**：支持 `X-Signature` + `X-Timestamp`（HMAC-SHA256，key 优先取 `secret_key`，未配置时回退 `webhook_token`）；Bearer 鉴权与签名验签双轨并行，DSA 推送无需额外配置即可通过；签名无效或缺失时返回 401  
  **Added DSA astrbot channel signature compatibility**: accepts `X-Signature` + `X-Timestamp` (HMAC-SHA256, key = `secret_key`, falls back to `webhook_token`); Bearer auth and signature verification run in parallel, DSA push works without extra config; invalid or missing signature returns 401

## [v1.4.3]

- 新增 webhook_token 鉴权配置（Bearer / X-Auth-Token / ?token=），公网/云服务器部署时未携带有效 token 的请求返回 401  
  Added `webhook_token` config for Webhook authentication (Bearer / X-Auth-Token / ?token=), required for public/cloud deployments; unauthorized requests return 401

## [v1.4.1]

- 声明最低 AstrBot 版本要求（>= 4.2.5），适配 AstrBot v4.2.5 渲染接口和 MessageChain API  
  Declared minimum AstrBot version requirement (>= 4.2.5), adapted to AstrBot v4.2.5 rendering interface and MessageChain API

## [v1.4.0]

- 适配 AstrBot v4.2.5 渲染接口（`self.context.html_render()` → `self.html_render()`）；修复 Image 导入（`Image.fromFileSystem()` → `MessageChain.file_image()`）；推送开关改回纯本地控制，不再调用 DSA API，减少与 DSA 的耦合；移除 `_api_put_config` 和 `_dsa_get_notification_channels` 方法；清理 `_save_config` 无效分支；移除 `_render_to_image` 的 `mobile_viewport` 死参数；配置面板已支持推送开关  
  Adapted to AstrBot v4.2.5 rendering interface (`self.context.html_render()` → `self.html_render()`); fixed Image import (`Image.fromFileSystem()` → `MessageChain.file_image()`); reverted push toggle to local-only control, no longer calls DSA API to reduce coupling; removed `_api_put_config` and `_dsa_get_notification_channels` methods; cleaned up `_save_config` invalid branch; removed `_render_to_image` `mobile_viewport` dead parameter; push toggle now available in config panel

## [v1.3.1]

- 修复推送开关命令（开启推送/关闭推送）通过 DSA API 修改配置后版本冲突的问题；新增乐观锁机制确保 API 写入可靠；提示用户修改配置后刷新 DSA WebUI 页面  
  Fixed push toggle commands (enable/disable push) version conflict after DSA API config update; added optimistic locking for reliable API writes; prompt users to refresh DSA WebUI page after config changes

## [v1.3.0]

- 新增 `/DSA 帮助` 命令（别名 `h`/`help`），按类别列出所有可用命令及说明；补全文档中遗漏的自选股管理（我的自选/增加自选/删除自选）和推送控制（开启推送/关闭推送/推送状态）指令  
  Added `/DSA help` command (aliases: `h`/`help`), lists all available commands with descriptions by category; added missing watchlist commands (my watchlist/add/remove) and push control commands (enable/disable/push status)

## [v1.2.2]

- 指令组统一为 `/DSA` 前缀，修复复盘历史记录查询 bug，示例统一为上证指数  
  Unified command prefix to `/DSA`, fixed market review history query bug, examples use SSE Composite Index

## [v1.2.1]

- 新增 Markdown 输出模式，纯文本模式自动清理语法符号，表格统一转卡片式展示  
  Added Markdown output mode, auto-strip syntax in text mode, table card display

## [v1.2.0]

- 新增聊天指令系统（大盘任务/大盘报告/大盘复盘/自选行情/历史分析/我的自选报告），完整中英双语文档注释  
  Added chat commands (tasks/report/review/quotes/history/my reports), bilingual docstrings and code comments

## [v1.1.0]

- 重构为独立版本。新增 `split_image` 配置、来源自适应、多目标推送、全配置面板化；新增 debug 开关，默认静默日志  
  Rewritten as standalone version. Added `split_image` config, source-adaptive processing, multi-target push, full panel configuration; added debug config toggle with quiet logs by default

## [v1.0.0]

- 初始版本，基础 Webhook 接收与推送  
  Initial release, basic Webhook receiving and push
