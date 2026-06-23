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
     │                       ├── Text mode ── Send as whole (easy to copy/share)
     │                       └── Image mode ── Single long image / Multi-image by section
     │
     └── AstrBot Sender  → HTML (with styles)
                            │
                            ├── Text mode ── html2text → MD → push as whole
                            └── Image mode ── Extract body + plugin CSS → render image
```

## Features

- ✅ HTTP Webhook receiver (supports both Markdown & HTML sources)
- ✅ **Text mode** — Push full content as text, easy to copy and forward
- ✅ **Image mode** — Render as images for better mobile reading experience
- ✅ **Optional multi-image splitting** — Off=one long image, On=split by section
- ✅ **Multi-target push** — Send to multiple users/groups simultaneously, auto-detect platform
- ✅ **Source-adaptive** — HTML auto-converts to Markdown, no manual config needed
- ✅ **Full panel configuration** — All settings via Web UI, no restart required

## Installation

1. Copy the plugin folder to AstrBot's `data/plugins/` directory
2. Restart AstrBot
3. Configure in Web UI → Plugin Settings

## Configuration

Configure via AstrBot Web UI → Plugin Settings:

| Key | Description | Default |
|-----|-------------|---------|
| `target_user_ids` | Push target list (raw IDs, platform prefix auto-appended) | `[]` |
| `output_mode` | Output mode: `text` or `image` | `text` |
| `split_image` | Split into multiple images by section (image mode only) | `false` |
| `webhook_path` | Webhook path | `/stock-analysis` |
| `webhook_port` | Listen port | `8080` |
| `image_quality` | Image quality 0-100 (image mode) | `85` |
| `device_scale_factor_level` | Pixel scale: `low` / `normal` / `high` | `normal` |
| `viewport_width` | Viewport width in px (image mode) | `800` |

### Mode Comparison

| Mode | Pros | Best for |
|------|------|----------|
| **Text mode** | Copyable, small size, fast | Mobile reading, tech users, secondary processing |
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

- **v1.0.0** — Initial release. Complete rewrite with split_image config, source-adaptive processing, multi-target push, full panel configuration

## Acknowledgements

Most of the code in this plugin is AI-assisted. The author provides requirements and optimization suggestions.

## Support

Submit an Issue if you have any questions.
