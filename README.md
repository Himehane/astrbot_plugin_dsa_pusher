# DSA推送器 (astrbot_plugin_dsa_pusher)

[English](README_EN.md)

AstrBot 插件，接收 DSA (DailyStockAnalysis) 通过 Webhook 推送的股票分析报告，自动转发到微信/QQ 等聊天平台。

最初基于 astrbot_plugin_daily_stock_analysis_adapter 重构，现已独立演化为功能更完善的 DSA 推送器。

## 工作流程

```
DSA 生成报告
     │
     ├── 自定义 Webhook → Markdown
     │                       │
     │                       ├── 文字模式 ── 整篇推送（方便转发复制）
     │                       └── 图片模式 ── 整张长图 / 按章节拆多张竖版小图
     │
     └── AstrBot Sender  → HTML（含样式）
                            │
                            ├── 文字模式 ── html2text 转 MD → 整篇推送
                            └── 图片模式 ── 注入插件 CSS → 渲染图片
```

## 功能特性

- ✅ 接收 HTTP Webhook（兼容 Markdown / HTML 两种来源）
- ✅ **文字模式** — 正文文本整篇推送，方便复制转发
- ✅ **图片模式** — 渲染为图片推送，阅读体验更好
- ✅ **可选多图拆分** — 关闭=整张长图，开启=按章节拆多张竖版小图
- ✅ **多目标推送** — 同时推送到多个用户/群聊，自动检测平台
- ✅ **来源自适应** — HTML 自动转 Markdown，无需手动配置
- ✅ **全配置面板化** — Web UI 直接改配置，无需重启

## 安装方式

1. 将插件文件夹复制到 AstrBot 的 `data/plugins/` 目录下
2. 重启 AstrBot
3. 在 WebUI 中配置参数

## 配置说明

在 AstrBot 的 WebUI → 插件配置中进行设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `target_user_ids` | 推送目标列表（填裸 ID，代码自动补平台前缀） | `[]` |
| `output_mode` | 推送模式：`text` 文字 / `image` 图片 | `text` |
| `split_image` | 图片模式下是否按章节拆分多图（false=整张长图） | `false` |
| `webhook_path` | Webhook 路径 | `/stock-analysis` |
| `webhook_port` | 监听端口 | `8080` |
| `image_quality` | 图片质量 0-100（图片模式有效） | `85` |
| `device_scale_factor_level` | 像素缩放：`low` / `normal` / `high` | `normal` |
| `viewport_width` | 渲染视口宽度 px（图片模式有效） | `800` |

### 推送模式对比

| 模式 | 优点 | 适合场景 |
|------|------|---------|
| **文字模式 (text)** | 可复制转发、体积小、速度快 | 手机端阅读、技术用户、需二次处理 |
| **图片模式 (image)** | 排版精美、阅读体验好 | 电脑端方便查看、非技术用户 |

### 多图拆分说明

图片模式下：
- **拆分关闭 (split_image=false)** — 整篇报告渲染为一张长图，保持完整
- **拆分开启 (split_image=true)** — 按 `##`/`###` 章节拆成多张竖版小图，手机滑动阅读更流畅

## 使用方式

1. 在 DSA 管理后台配置 Webhook URL：`http://你的AstrBot地址:8080/stock-analysis`
2. DSA 生成报告后自动推送到本插件
3. 插件根据配置处理内容并发送到目标用户/群聊

## 注意事项

1. 确保配置端口未被其他服务占用
2. 推送前需先向机器人发送任意消息建立对话上下文（部分平台需要）
3. 图片模式下若文件过大可调低 `image_quality`
4. 所有配置通过 WebUI 修改即可，无需编辑代码文件

## 版本历史

- **v1.2.0** — 重构为独立版本。新增 split_image 配置、来源自适应、多目标推送、全配置面板化
- **v1.1.1** — 优化文字/图片模式分流逻辑
- **v1.1.0** — 新增图片模式，支持按章节拆分多图
- **v1.0.0** — 初始版本，基础 Webhook 接收与推送

## 致谢

本插件大部分代码由 AI 辅助生成，作者仅提供需求与优化建议。

## 支持

如有问题，请提交 Issue。
