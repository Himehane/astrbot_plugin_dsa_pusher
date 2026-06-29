# DSA Pusher (astrbot_plugin_dsa_pusher)

[中文](README.md)

An AstrBot plugin that receives stock analysis reports pushed by DSA (DailyStockAnalysis) via Webhook and forwards them to chat platforms like WeChat/QQ.

Originally forked from `astrbot_plugin_daily_stock_analysis_adapter`, now independently evolved into a more feature-complete DSA pusher.

## Workflow

```
DSA generates report
     │
     ├── Custom Webhook → Markdown
     │                       │
     │                       ├── Text mode ── Strip markdown syntax → push as whole
     │                       ├── Markdown mode ── Keep MD syntax → push as whole
     │                       └── Image mode ── Single long image / Multi-image by section
     │
     └── AstrBot Sender  → HTML (with styles)
                            │
                            ├── Text mode ── html2text → MD → strip syntax → push as whole
                            ├── Markdown mode ── html2text → MD → push as whole
                            └── Image mode ── Extract body + plugin CSS → render image
```

## Features

- ✅ HTTP Webhook receiver (supports both Markdown & HTML sources)
- ✅ **Text mode** — Auto-strip markdown syntax, easy to copy and forward
- ✅ **Markdown mode** — Keep full MD syntax, ideal for platforms with markdown rendering
- ✅ **Image mode** — Render as images for better mobile reading experience
- ✅ **Table card display** — All tables converted to card format for mobile readability
- ✅ **Optional multi-image splitting** — Off=one long image, On=split by section
- ✅ **Multi-target push** — Send to multiple users/groups simultaneously, auto-detect platform
- ✅ **Source-adaptive** — HTML auto-converts to Markdown, no manual config needed
- ✅ **Full panel configuration** — All settings via Web UI, no restart required
- ✅ **Chat commands** — Query quotes, reports, market reviews, manage watchlist, toggle push notification

## Chat Commands

Send these commands directly in chat (prefixed with `/DSA` to avoid conflicts with other plugins):

| Command | Description | Example |
|---------|-------------|---------|
| **/DSA help** | Show all available commands | `/DSA help` or `/DSA h` |
| **/DSA tasks [n]** | List recent analysis tasks | `/DSA tasks 10` (show 10) |
| **/DSA report [ID]** | Pull full report for a task | `/DSA report` (latest) / `/DSA report abc123` |
| **/DSA review** | Push latest market review report | `/DSA review` |
| **/DSA quotes** | View real-time watchlist quotes | `/DSA quotes` |
| **/DSA history \<code\>** | Query individual stock history report | `/DSA history 000001` |
| **/DSA my reports** | Batch push all watchlist reports | `/DSA my reports` |
| **/DSA my watchlist** | View current watchlist | `/DSA my watchlist` |
| **/DSA add \<code\>** | Add stock to watchlist | `/DSA add 600519` |
| **/DSA remove \<code\>** | Remove stock from watchlist | `/DSA remove 600519` |
| **/DSA enable push** | Enable auto push notification | `/DSA enable push` |
| **/DSA disable push** | Disable auto push notification | `/DSA disable push` |
| **/DSA push status** | Check push notification status | `/DSA push status` |

## Installation

1. Copy the plugin folder to AstrBot's `data/plugins/` directory
2. Restart AstrBot
3. Configure in Web UI → Plugin Settings

## Configuration

Configure via AstrBot Web UI → Plugin Settings:

| Key | Description | Default |
|-----|-------------|---------|
| `debug` | Debug mode: `true`=verbose logs with target IDs, `false`=warnings/errors only | `false` |
| `target_user_ids` | Push target list (raw IDs, platform prefix auto-appended) | `[]` |
| `output_mode` | Output mode: `text` / `markdown` / `image` | `text` |
| `split_image` | Split into multiple images by section (image mode only) | `false` |
| `webhook_path` | Webhook path | `/stock-analysis` |
| `webhook_port` | Listen port | `8080` |
| `image_quality` | Image quality 0-100 (image mode) | `85` |
| `device_scale_factor_level` | Pixel scale: `low` / `normal` / `high` | `normal` |
| `viewport_width` | Viewport width in px (image mode) | `800` |

### Mode Comparison

| Mode | Pros | Best for |
|------|------|----------|
| **Text mode** | Clean syntax, copyable, small size | Mobile reading, tech users, secondary processing |
| **Markdown mode** | Preserves MD syntax, supports rendering | Telegram, markdown-capable platforms |
| **Image mode** | Beautiful layout, great reading experience | Desktop viewing, non-tech users |

### Split Image Details

In image mode:
- **Split off** (`split_image=false`) — Render entire report as one long image
- **Split on** (`split_image=true`) — Split by `##`/`###` sections into multiple vertical images for easy scrolling

## Usage

1. Configure Webhook URL in DSA admin panel: `http://your-astrbot-address:8080/stock-analysis`
2. DSA will push reports to this plugin automatically
3. Plugin processes content based on config and sends to target users/groups

## Notes

1. Make sure the configured port is not occupied by another service
2. Some platforms require sending a message first to establish conversation context
3. Lower `image_quality` if images exceed platform file size limits
4. All settings can be changed via Web UI — no code editing needed

## Version History

- **v1.3.0** — Added `/DSA help` command (aliases: `h`/`help`), lists all available commands with descriptions by category; added missing watchlist commands (my watchlist/add/remove) and push control commands (enable/disable/push status)
- **v1.2.2** — Unified command prefix to `/DSA`, fixed market review history query bug, examples use SSE Composite Index
- **v1.2.1** — Added Markdown output mode, auto-strip syntax in text mode, table card display
- **v1.2.0** — Added chat commands (tasks/report/review/quotes/history/my reports), bilingual docstrings and code comments
- **v1.1.0** — Added debug config toggle; quiet logs by default, verbose logs with target IDs in debug mode
- **v1.0.0** — Initial release. Complete rewrite with split_image config, source-adaptive processing, multi-target push, full panel configuration

## Acknowledgements

Most of the code in this plugin is AI-assisted. The author provides requirements and optimization suggestions.

## Support

Submit an Issue if you have any questions.
