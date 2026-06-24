import asyncio
import hashlib
import hmac
import json
import re
import time

from aiohttp import web

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain


@register(
    "astrbot_plugin_dsa_pusher",
    "Himehane",
    "DSA推送器 - 接收股票分析报告并推送到聊天平台",
    "v1.1.0",
)
class DSAPusher(Star):
    """
    接收 DSA (Daily Stock Analysis) Webhook 推送，
    支持文字/图片双模式，按需拆分多图，自动推送多目标。
    """

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.webhook_port = int(config.get("webhook_port", 8080))
        self.webhook_path = config.get("webhook_path", "/stock-analysis")
        self.secret_key = config.get("secret_key")
        self.enable_signature_verification = False  # 强制关闭签名验证
        self.image_quality = int(config.get("image_quality", 85))
        self.device_scale_factor_level = config.get(
            "device_scale_factor_level", "normal"
        )
        self.viewport_width = int(config.get("viewport_width", 800))
        self.web_app = None
        self.today_stock_report = None
        self.runner = None
        self.site = None

        # ---- 新配置项 ----
        # 输出模式: "text" 或 "image"
        self.output_mode = config.get("output_mode", "text")
        # 多目标：使用者只填裸用户/群 ID，代码自动补全平台前缀
        self.target_user_ids: list[str] = config.get("target_user_ids", [])
        # 多图拆分开关：默认为 false，整张长图保底
        self.split_image: bool = config.get("split_image", False)
        # 调试日志开关：默认为 false，只显示警告/错误
        _debug_val = config.get("debug", False)
        if isinstance(_debug_val, str):
            self.debug = _debug_val.lower() in ("true", "1", "yes")
        else:
            self.debug = bool(_debug_val)

        # 记录已缓存的平台标识和上下文
        self._cached_platform = None
        self._context_token_fallback = None

        if not self.enable_signature_verification:
            logger.warning(
                "每日股票分析适配器: 警告! 当前未启用签名验证，"
                "请自行确保服务仅可内部网络访问"
            )
        if self.enable_signature_verification and self.secret_key is None:
            raise ValueError("每日股票分析适配器: 密钥未配置!")

    # ================================================================
    #  生命周期
    # ================================================================

    async def initialize(self):
        """初始化插件，启动 HTTP 服务"""
        try:
            await self.start_http_server()
            if self.debug:
                logger.info(
                    f"每日股票分析适配器: 插件已启动, 监听端口 {self.webhook_port}, "
                    f"路径 {self.webhook_path}, 模式={self.output_mode}, "
                    f"拆分={self.split_image}"
                )
        except Exception as e:
            logger.error(f"每日股票分析适配器: 初始化失败: {e}")
            raise

    async def terminate(self):
        """插件销毁时清理资源"""
        try:
            if self.runner:
                await self.runner.cleanup()
                self.runner = None
                self.site = None
                self.web_app = None
            if self.debug:
                logger.info("每日股票分析适配器: 插件已停止")
        except Exception as e:
            logger.error(f"每日股票分析适配器: 插件终止时出错: {e}")

    # ================================================================
    #  HTTP 服务
    # ================================================================

    async def start_http_server(self):
        """启动 HTTP 服务"""
        self.web_app = web.Application()
        self.web_app.router.add_post(self.webhook_path, self.handle_webhook)
        self.web_app.router.add_get(self.webhook_path, self.health_check)

        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, "0.0.0.0", self.webhook_port)
        await self.site.start()
        if self.debug:
            logger.info(
                f"每日股票分析适配器: HTTP 服务已启动在端口 {self.webhook_port}"
            )

    async def health_check(self, request):
        """健康检查接口"""
        return web.json_response(
            {
                "status": "ok",
                "plugin": "daily_stock_analysis_adapter",
                "version": "v1.0.0",
                "timestamp": time.time(),
            }
        )

    async def handle_webhook(self, request):
        """处理 Webhook 请求"""
        try:
            data = await request.json()
            headers = dict(request.headers)

            content_len = len(data.get("content", ""))
            if self.debug:
                logger.info(
                    f"每日股票分析适配器: 收到 Webhook, content_len={content_len}"
                )

            if not data.get("content"):
                logger.warning("每日股票分析适配器: 请求缺少 content 字段")
                return web.json_response(
                    {"error": "Missing required field: content"}, status=400
                )

            if self.enable_signature_verification:
                if not await self.verify_signature(data, headers):
                    logger.warning("每日股票分析适配器: 签名验证失败")
                    return web.json_response(
                        {"error": "Signature verification failed"}, status=401
                    )

            await self.process_stock_analysis(data)
            return web.json_response({"status": "ok", "timestamp": time.time()})

        except json.JSONDecodeError:
            logger.error("每日股票分析适配器: 请求体不是有效 JSON")
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"每日股票分析适配器: 处理 Webhook 出错: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def verify_signature(self, data: dict, headers: dict) -> bool:
        """验证 HMAC 签名"""
        if not self.secret_key:
            return False
        signature = headers.get("X-Signature", "")
        if not signature:
            return False
        payload = json.dumps(data, separators=(",", ":"))
        expected = hmac.new(
            self.secret_key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    # ================================================================
    #  平台检测与目标构造
    # ================================================================

    def _detect_platform(self) -> str | None:
        """
        自动检测第一个支持主动推送的已连接平台。
        返回平台 ID (如 'wechatcom_official')，或 None。
        """
        if self._cached_platform:
            return self._cached_platform
        try:
            for inst in self.context.get_registered_instances():
                pid = getattr(inst, "platform_meta", {}).get("platform", "")
                if pid and pid not in ("web", "cli"):
                    self._cached_platform = pid
                    if self.debug:
                        logger.info(f"每日股票分析适配器: 自动检测到平台 {pid}")
                    return pid
        except Exception as e:
            logger.warning(f"每日股票分析适配器: 检测平台失败: {e}")
        return None

    def _construct_target_ids(self) -> list[str]:
        """
        根据 target_user_ids 和自动检测的平台构造完整 target_id。
        如果用户已经填了完整标识符则直接使用。
        """
        platform = self._detect_platform()
        if not platform:
            logger.warning("每日股票分析适配器: 未检测到可用平台")
            return [uid for uid in self.target_user_ids if uid]

        result = []
        for uid in self.target_user_ids:
            uid = uid.strip()
            if not uid:
                continue
            # 如果已包含平台前缀则不重复拼接
            if ":" in uid:
                result.append(uid)
            else:
                # 默认使用 "FriendMessage" 类型
                result.append(f"{platform}:FriendMessage:{uid}")
        return result

    # ================================================================
    #  入口：处理股票分析数据
    # ================================================================

    async def process_stock_analysis(self, data: dict):
        """
        处理股票分析数据(入口方法)。

        兼容两种来源链路:
          - DSA AstrBot Sender -> HTML 内容(含样式)
          - 其他来源 -> Markdown 内容

        按 output_mode 分流:
          - text 模式 -> 整篇推送，不分片(方便转发复制)
          - image 模式 -> 渲染为图片推送

        图片模式下根据 split_image 决定：
          - False(默认) -> 整张长图
          - True -> 按章节拆分成多张竖版小图
        """
        try:
            content = data.get("content", "")
            if not content:
                return

            # 来源格式自适应: 归一化为 Markdown
            content = self._normalize_content(content)

            if self.output_mode == "text":
                await self._process_text_mode(content)
            else:
                await self._process_image_mode(content)

        except Exception as e:
            logger.error(f"每日股票分析适配器: 处理股票分析数据时出错: {e}")
            raise

    # ================================================================
    #  文字模式
    # ================================================================

    async def _process_text_mode(self, content: str):
        """文字模式：整篇推送 MD，不分片(方便转发复制)"""
        chunks = [content]
        await self._send_to_targets(chunks, mode="text")

    # ================================================================
    #  图片模式
    # ================================================================

    async def _process_image_mode(self, md_content: str):
        """
        图片模式处理链路:
          MD -> (可选拆分) -> 渲染图片 -> 下载 -> 发送

        根据 split_image 决定是否拆分:
          - False: 整篇渲染为一张长图
          - True: 按 ## 或 ### 拆分，每段渲染一张竖版小图
        """
        if self.split_image:
            sections = self._split_md_for_mobile(md_content)
            if self.debug:
                logger.info(f"每日股票分析适配器: MD 已拆分为 {len(sections)} 段")
        else:
            sections = [md_content]
            if self.debug:
                logger.info("每日股票分析适配器: 整篇渲染为一张长图")

        local_paths = []
        for i, chunk_md in enumerate(sections, 1):
            if self.debug:
                logger.info(f"每日股票分析适配器: 渲染第 {i}/{len(sections)} 段...")

            body_html = self._md_to_html(chunk_md)
            html_doc = self._wrap_html(body_html)

            url = await self._render_to_image(html_doc, mobile_viewport=True)

            # 下载图片到本地，避免路径问题
            local_path = await self._download_image(url, i)
            if local_path:
                local_paths.append(local_path)
            else:
                local_paths.append(url)

            if i < len(sections):
                await asyncio.sleep(1.5)

        await self._send_to_targets(local_paths, mode="image")

    async def _download_image(self, url: str, seq: int) -> str | None:
        """下载图片到本地临时路径"""
        try:
            from astrbot.core.utils.io import download_image_by_url

            local_path = await download_image_by_url(url)
            if self.debug:
                logger.info(f"每日股票分析适配器: 第{seq}段图片已下载: {local_path}")
            return local_path
        except Exception as e:
            logger.warning(f"每日股票分析适配器: 第{seq}段图片下载失败: {e}, 改用 URL")
            return None

    # ================================================================
    #  MD 拆分 (仅 split_image=True 时使用)
    # ================================================================

    @staticmethod
    def _split_md_for_mobile(md_content: str) -> list[str]:
        """
        将 Markdown 按标题拆分成多个段落 (仅图片模式 + 拆分启用时使用)

        拆分规则:
          1. 先按 ## (h2) 拆分
          2. 如果拆分后只有 1 段，回退到按 ### (h3) 拆分
          3. 每段保留标题，并配上统一的头部(日期+引言)
        """
        # 提取 H1 作为头部
        lines = md_content.split("\n")
        head_lines = []
        body_start = 0
        for i, line in enumerate(lines):
            head_lines.append(line)
            if line.startswith("## "):
                body_start = i
                break

        head = "\n".join(head_lines).strip()
        body = "\n".join(lines[body_start:])

        # 按 ## 拆分
        sections = _split_by_heading(body, r"^## ")
        if len(sections) <= 1:
            # 回退到按 ### 拆分
            sections = _split_by_heading(body, r"^### ")

        if not sections:
            return [md_content]

        result = []
        for sec in sections:
            combined = f"{head}\n\n{sec.strip()}"
            result.append(combined)
        return result

    # ================================================================
    #  MD -> HTML 转换
    # ================================================================

    @staticmethod
    def _md_to_html(md_text: str) -> str:
        """将 Markdown 片段转换为简易 HTML"""
        html = md_text

        # 标题
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

        # 加粗
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)

        # 管道符表格 -> HTML table
        html = _convert_pipe_tables(html)

        # 换行 -> <br>
        html = html.replace("\n", "<br>")

        # 多个 <br> 压缩
        html = re.sub(r"(<br>){3,}", "<br><br>", html)

        return html

    # ================================================================
    #  HTML 包装与渲染
    # ================================================================

    @staticmethod
    def _wrap_html(body_html: str) -> str:
        """将 body HTML 包装为完整 HTML 文档(插件 CSS)"""
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 20px;
    line-height: 1.7;
    padding: 24px 20px;
    color: #333;
    background: #fff;
  }}
  h1 {{ font-size: 24px; margin: 16px 0 12px; color: #1a1a1a; }}
  h2 {{ font-size: 22px; margin: 14px 0 10px; color: #2c3e50; }}
  h3 {{ font-size: 20px; margin: 12px 0 8px; color: #34495e; }}
  p {{ margin: 6px 0; }}
  strong {{ color: #e74c3c; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
    font-size: 17px;
  }}
  table td, table th {{
    border: 1px solid #ddd;
    padding: 5px 8px;
    text-align: left;
  }}
  table th {{ background: #f5f5f5; font-weight: 600; }}
  br {{ display: block; margin: 4px 0; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    async def _render_to_image(
        self, html_doc: str, mobile_viewport: bool = False
    ) -> str:
        """将 HTML 渲染为图片，返回图片 URL"""
        try:
            viewport = "375x812" if mobile_viewport else "800x600"
            payload = {
                "html": html_doc,
                "viewport": viewport,
                "quality": self.image_quality,
                "device_scale_factor_level": self.device_scale_factor_level,
            }
            url = await self.context.html_render(payload)
            if self.debug:
                logger.info(f"每日股票分析适配器: 渲染完成, url_len={len(url)}")
            return url
        except Exception as e:
            logger.error(f"每日股票分析适配器: 渲染图片失败: {e}")
            raise

    # ================================================================
    #  发送到目标
    # ================================================================

    async def _send_to_targets(self, chunks: list, mode: str = "text"):
        """发送内容到目标群组和用户(文字/图片通用)"""
        target_ids = self._construct_target_ids()
        if not target_ids:
            logger.warning("每日股票分析适配器: 没有配置推送目标")
            return

        failed_targets = []
        for target_id in target_ids:
            try:
                if self.debug:
                    logger.info(
                        f"每日股票分析适配器: 向 {target_id} 发送 "
                        f"{len(chunks)} 段 ({mode}模式)..."
                    )

                if mode == "text":
                    # 文字模式：整篇推送
                    text = chunks[0] if chunks else ""
                    if text:
                        await self.context.send_message(
                            target_id,
                            MessageChain().message([Plain(text)]),
                        )
                else:
                    # 图片模式：逐张发送
                    for path in chunks:
                        msg = MessageChain().message([Image.fromFileSystem(path)])
                        await self.context.send_message(target_id, msg)
                        await asyncio.sleep(0.5)

                if self.debug:
                    logger.info(f"每日股票分析适配器: 向 {target_id} 推送成功")
            except Exception as e:
                error_msg = str(e)
                tid_display = target_id if self.debug else "(已隐藏)"
                # 检测 context token 缺失
                if "context" in error_msg.lower() or "token" in error_msg.lower():
                    logger.warning(
                        f"每日股票分析适配器: 向 {tid_display} 推送失败, "
                        f"可能是缺少对话上下文。请先向机器人发送任意一条消息"
                        f'(如"你好")建立对话后再试。'
                    )
                else:
                    logger.error(f"每日股票分析适配器: 向 {tid_display} 推送失败: {e}")
                failed_targets.append(target_id)

        if failed_targets:
            ft_display = (
                failed_targets if self.debug else f"{len(failed_targets)}个(已隐藏)"
            )
            logger.warning(
                f"每日股票分析适配器: 推送完成, "
                f"{len(failed_targets)}/{len(target_ids)} 个目标失败: "
                f"{ft_display}"
            )
        elif self.debug:
            logger.info(
                f"每日股票分析适配器: 推送完成, 全部 {len(target_ids)} 个目标成功"
            )

    # ================================================================
    #  内容归一化
    # ================================================================

    @staticmethod
    def _is_html_content(content: str) -> bool:
        """检测内容是否为 HTML 格式"""
        if not content:
            return False
        return bool(re.search(r"<(!DOCTYPE|html|head|body|div|p|table)", content[:500]))

    @staticmethod
    def _normalize_content(content: str) -> str:
        """内容归一化: 如果是 HTML 则转成 Markdown，否则原样返回"""
        if not content:
            return ""
        if not DSAPusher._is_html_content(content):
            return content
        try:
            import html2text

            h = html2text.HTML2Text()
            h.body_width = 0
            h.ignore_links = True
            h.ignore_images = False
            h.ignore_emphasis = False
            h.protect_links = True
            h.unicode_snob = True
            md = h.handle(content).strip()
            return md
        except ImportError:
            logger.warning("每日股票分析适配器: html2text 未安装, 回退原始内容")
            return content

    # ================================================================
    #  模块级辅助函数
    # ================================================================


def _split_by_heading(text: str, pattern: str) -> list[str]:
    """按标题正则拆分文本"""
    lines = text.split("\n")
    sections = []
    cur = []
    for line in lines:
        if re.match(pattern, line):
            if cur:
                sections.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur))
    return sections


def _convert_pipe_tables(html: str) -> str:
    """将管道符表格转换为 HTML table"""
    lines = html.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检测管道符表格：至少含两个 |
        if "|" in line and line.count("|") >= 2:
            table_lines = []
            # 收集连续表格行
            while i < len(lines) and "|" in lines[i] and lines[i].count("|") >= 2:
                table_lines.append(lines[i])
                i += 1
            if len(table_lines) >= 2:
                html_table = _pipe_table_to_html(table_lines)
                result.append(html_table)
                continue
            else:
                # 不足两行，当普通文本处理
                for tl in table_lines:
                    result.append(tl)
                continue
        result.append(line)
        i += 1
    return "\n".join(result)


def _pipe_table_to_html(table_lines: list[str]) -> str:
    """
    将管道符表格行转换为 HTML 表格字符串。

    输入示例:
      | 分类 | 品种 |
      |---|---|
      | 股票 | 茅台 |
    输出: <table><tr><td>...</td></tr></table>
    """
    rows = []
    for line in table_lines:
        # 过滤分隔行 (|---|---|---|)
        stripped = line.strip()
        if re.match(r"^\|[-:| ]+\|$", stripped):
            continue
        cells = [c.strip() for c in stripped.split("|")]
        # 去掉首尾空 cell (管道符在行首行尾导致)
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    html = ["<table>"]
    # 第一行作为表头
    if rows:
        html.append("<tr>")
        for cell in rows[0]:
            html.append(f"<th>{cell}</th>")
        html.append("</tr>")
    # 后续行
    for row in rows[1:]:
        html.append("<tr>")
        for cell in row:
            html.append(f"<td>{cell}</td>")
        html.append("</tr>")
    html.append("</table>")
    return "\n".join(html)
