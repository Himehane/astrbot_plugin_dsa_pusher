import asyncio
import hashlib
import hmac
import json
import re
import time

from aiohttp import web

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain


@register(
    "astrbot_plugin_dsa_pusher",
    "Himehane",
    "DSA推送器 - 接收股票分析报告并推送到聊天平台",
    "v1.2.0",
)
class DSAPusher(Star):
    """
    DSA Pusher — 接收 DSA (Daily Stock Analysis) Webhook 推送，
    支持文字/Markdown/图片三模式、多图拆分、多目标推送、微信指令查询。

    聊天指令组 /DSA:
      /DSA 任务 [n]         — 查询最近 n 个分析任务 (默认 5)
      /DSA 报告 [ID]        — 拉取指定任务报告 (默认最新)
      /DSA 复盘             — 推送最新的大盘复盘报告
      /DSA 自选股行情         — 查看自选股实时行情
      /DSA 历史分析 <代码>  — 查询个股历史分析报告
      /DSA 自选报告         — 批量推送所有自选股的历史报告
    """

    def __init__(self, context: Context, config: dict | None = None):
        config = config or {}
        super().__init__(context)
        self.webhook_port = int(config.get("webhook_port", 8080))
        self.webhook_path = config.get("webhook_path", "/stock-analysis")
        # DSA API 地址（通过 webui 端口访问）
        self.dsa_api_base = config.get("dsa_api_base", "http://127.0.0.1:19000")
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
        # 输出模式: "text" (纯文本) / "markdown" (保留MD语法) / "image" (图片)
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
                "version": "v1.2.1",
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
        Auto-detect the first connected platform that supports active push.
        Returns platform ID (e.g. 'wechatcom_official'), or None.
        """
        # 遍历 AstrBot 已连接平台实例，跳过 web/cli 等管理端
        if self._cached_platform:
            return self._cached_platform
        try:
            for inst in self.context.platform_manager.platform_insts:
                pid = inst.meta().id
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
    #  DSA API 交互 (历史报告查询)
    # ================================================================

    async def _api_get(self, path: str, timeout: int = 10) -> dict | None:
        """向 DSA API 发送 GET 请求"""
        import aiohttp

        url = f"{self.dsa_api_base.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"DSA API 返回 {resp.status}: {url}")
                        return None
                    return await resp.json()
        except Exception as e:
            logger.error(f"DSA API 请求失败 [{url}]: {e}")
            return None

    # ========================================
    # 指令组：DSA
    # ========================================
    # 用法: /DSA 任务 10
    #       /DSA 报告 abc123
    #       /DSA 复盘
    #       /DSA 自选股行情
    #       /DSA 历史分析 000001
    #       /DSA 自选报告

    @filter.command_group("DSA")
    def cmd_group(self):
        """DSA 相关指令组 / DSA-related command group"""
        pass

    @cmd_group.command("任务", aliases=["任务列表"])
    async def cmd_history_tasks(self, event: AstrMessageEvent):
        """
        查询最近分析任务列表 / Query recent analysis task list

        可选数字参数控制显示条数，默认最近 5 条。
        用法: 大盘任务 [数字]  例: 大盘任务 10

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 格式化任务列表
        """
        # 解析可选参数 n，从消息中提取数字，限制 1~20 条
        text = event.get_message_str().strip()
        n = 5  # 默认
        import re as _re

        m = _re.search(r"(\d+)", text)
        if m:
            n = int(m.group(1))
            n = max(1, min(n, 20))  # 限制范围 1~20

        data = await self._api_get("api/v1/analysis/tasks")
        if not data:
            yield event.plain_result("❌ 无法连接 DSA 服务，请检查 API 地址配置")
            return

        tasks = data.get("tasks", [])
        if not tasks:
            yield event.plain_result("📋 暂无历史任务记录")
            return

        tasks = tasks[:n]
        lines = [f"📋 共 {data.get('total', 0)} 个任务，最近 {len(tasks)} 个："]
        for t in tasks:
            name = t.get("stock_name") or t.get("stock_code", "未知")
            tid = t.get("task_id", "???")
            status = t.get("status", "未知")
            created = (t.get("created_at") or "??")[:16].replace("T", " ")
            completed = (t.get("completed_at") or "进行中")[:16].replace("T", " ")
            lines.append(f"  📌 {name}")
            lines.append(f"     ID：{tid}")
            lines.append(f"     创建：{created}")
            lines.append(f"     完成：{completed}")
            lines.append(f"     状态：{status}")
            lines.append("")

        lines.append("💡 输入「大盘报告 <ID>」拉取指定报告")
        lines.append("   输入「大盘报告」拉取最新报告")
        yield event.plain_result("\n".join(lines))

    @cmd_group.command("自选股行情")
    async def cmd_watchlist(self, event: AstrMessageEvent):
        """
        查询自选股实时行情 / Query watchlist real-time quotes

        遍历用户自选股，逐只拉取实时行情（价格、涨跌幅、最高/最低、成交量）。
        用法: 自选股行情

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 格式化行情列表文本
        """
        # Step 1: 获取自选列表（仅股票代码）
        data = await self._api_get("api/v1/stocks/watchlist")
        if not data or "stock_codes" not in data:
            yield event.plain_result("❌ 获取自选列表失败")
            return

        codes = data["stock_codes"]
        if not codes:
            yield event.plain_result("📭 自选列表为空")
            return

        lines = []
        lines.append(f"📋 自选股行情 ({len(codes)}只)")
        lines.append(f"📅 {data.get('message', '')}")
        lines.append("")

        up = "🔴"
        down = "🟢"
        flat = "⚪"

        for code in codes:
            quote = await self._api_get(f"api/v1/stocks/{code}/quote")
            if not quote:
                lines.append(f"  {code} — 获取行情失败")
                continue

            name = quote.get("stock_name", "")
            price = quote.get("current_price", 0)
            change = quote.get("change", 0)
            pct = quote.get("change_percent", 0)
            high = quote.get("high", 0)
            low = quote.get("low", 0)
            vol = quote.get("volume", 0)

            if pct > 0:
                arrow = up
                sign = "+"
            elif pct < 0:
                arrow = down
                sign = ""
            else:
                arrow = flat
                sign = ""

            if vol >= 10000:
                vol_str = f"{vol / 10000:.1f}万手"
            else:
                vol_str = f"{vol:.0f}手"

            lines.append(f"{arrow} **{name}** ({code})")
            lines.append(f"  现价 {price:.2f}  {sign}{change:.2f} ({sign}{pct:.2f}%)")
            lines.append(f"  高 {high:.2f}  低 {low:.2f}  量 {vol_str}")
            lines.append("")

        lines.append("💡 输入「大盘报告」拉取最新分析报告")
        yield event.plain_result("\n".join(lines))

    @cmd_group.command("历史分析")
    async def cmd_history_stock(self, event: AstrMessageEvent):
        """
        查询个股历史分析报告 / Query individual stock history report

        从历史记录中找到该股票最新的分析报告，通过 Markdown API 获取全文并推送。
        支持股票代码或名称匹配（如 "000001" 或 "上证指数"）。

        用法: 历史分析 <股票代码或名称>  例: 历史分析 000001

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 状态提示（查找中 / 推送中）
        """
        text = event.get_message_str().strip()
        parts = text.split(None, 1)
        if len(parts) < 2:
            yield event.plain_result(
                "❌ 用法：历史分析 <股票代码或名称>\n例：历史分析 000001"
            )
            return

        keyword = parts[1].strip()

        # Step 1: 获取历史股票列表（含最新记录 ID）
        # Step 2: 先按股票代码精确匹配，再按中文名称匹配
        data = await self._api_get("api/v1/history/stocks")
        if not data or "items" not in data:
            yield event.plain_result("❌ 获取历史个股列表失败")
            return

        # 匹配股票：先按股票代码精确匹配，再按中文名称匹配
        matched = None
        for item in data["items"]:
            if item["stock_code"] == keyword:
                matched = item
                break

        if not matched:
            for item in data["items"]:
                if item["stock_name"] == keyword:
                    matched = item
                    break

        if not matched:
            yield event.plain_result(
                f"❌ 未找到「{keyword}」的历史分析记录\n该股可能还未被分析过"
            )
            return

        stock_display = f"{matched['stock_name']}({matched['stock_code']})"
        yield event.plain_result(f"⏳ 正在获取 {stock_display} 最新报告...")

        # 获取报告 Markdown
        record_id = matched["id"]
        report_data = await self._api_get(f"api/v1/history/{record_id}/markdown")
        if not report_data or "content" not in report_data:
            yield event.plain_result(f"❌ 获取 {stock_display} 报告失败")
            return

        content = report_data["content"]
        yield event.plain_result("✅ 已获取，正在推送...")

        # 按输出模式分流
        if self.output_mode == "markdown":
            await self._process_markdown_mode(content)
        elif self.output_mode == "text":
            await self._process_text_mode(content)
        else:
            await self._process_image_mode(content)

    @cmd_group.command("自选报告", aliases=["我的自选报告"])
    async def cmd_watchlist_history(self, event: AstrMessageEvent):
        """
        批量推送所有自选股历史报告 / Batch push watchlist history reports

        遍历自选股列表，逐只查找最新分析报告并通过 Markdown API 获取全文推送。
        进度会实时反馈（如 [1/4] 正在获取上证指数报告...）。

        用法: 我的自选报告

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 逐条进度提示 + 推送完成汇总
        """
        # Step 1: 获取自选列表（仅股票代码）
        # Step 2: 获取历史股票索引，建立 code -> 记录 映射
        wl = await self._api_get("api/v1/stocks/watchlist")
        if not wl or "stock_codes" not in wl:
            yield event.plain_result("❌ 获取自选列表失败")
            return

        codes = wl["stock_codes"]
        if not codes:
            yield event.plain_result("📭 自选列表为空")
            return

        # 获取历史个股列表，建立 code -> item 映射
        hs = await self._api_get("api/v1/history/stocks")
        stock_map = {}
        if hs and "items" in hs:
            for item in hs["items"]:
                stock_map[item["stock_code"]] = item

        total = len(codes)
        yield event.plain_result(f"📋 准备推送 {total} 只自选股的历史分析报告...")

        pushed = 0
        for i, code in enumerate(codes, 1):
            if code not in stock_map:
                continue

            item = stock_map[code]
            stock_name = item["stock_name"]
            record_id = item["id"]

            yield event.plain_result(
                f"[{i}/{total}] ⏳ 正在获取 {stock_name}({code}) 报告..."
            )

            report_data = await self._api_get(f"api/v1/history/{record_id}/markdown")
            if not report_data or "content" not in report_data:
                continue

            content = report_data["content"]

            # 报告自带标题，直接推送即可
            if self.output_mode == "markdown":
                await self._process_markdown_mode(content)
            elif self.output_mode == "text":
                await self._process_text_mode(content)
            else:
                await self._process_image_mode(content)

            pushed += 1

        yield event.plain_result(
            f"✅ 完成！已推送 {pushed}/{total} 只自选股的历史分析报告"
        )

    @cmd_group.command("复盘")
    async def cmd_market_review(self, event: AstrMessageEvent):
        """
        推送最新大盘复盘报告 / Push latest market review report

        在历史分析记录中查找 stock_code="MARKET" 且 report_type="market_review"
        的最新记录，获取其 Markdown 全文并推送。无需参数。

        用法: 大盘复盘

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 状态提示（查找中 / 推送中）
        """
        yield event.plain_result("⏳ 正在查找最新大盘复盘报告...")

        # 获取最近 20 条历史记录，按 ID 倒序（最新在前）
        # 找到第一条 stock_code=MARKET 且 report_type=market_review 的记录
        data = await self._api_get("api/v1/history?limit=20")
        if not data:
            yield event.plain_result("❌ 获取历史记录失败")
            return

        # history 返回 {total, page, limit, items: [...]} 结构
        records = (
            data
            if isinstance(data, list)
            else data.get("items", data.get("history", data.get("records", [])))
        )
        target = None
        for r in records:
            if (
                r.get("stock_code") == "MARKET"
                and r.get("report_type") == "market_review"
            ):
                target = r
                break

        if not target:
            yield event.plain_result("❌ 未找到大盘复盘记录")
            return

        record_id = target["id"]
        stock_name = target.get("stock_name", "大盘复盘")
        yield event.plain_result(f"✅ 找到 {stock_name}，正在获取报告...")

        md_data = await self._api_get(f"api/v1/history/{record_id}/markdown")
        if not md_data or "content" not in md_data:
            yield event.plain_result("❌ 获取报告内容失败")
            return

        content = md_data["content"]
        yield event.plain_result("✅ 正在推送...")

        if self.output_mode == "markdown":
            await self._process_markdown_mode(content)
        elif self.output_mode == "text":
            await self._process_text_mode(content)
        else:
            await self._process_image_mode(content)

    @cmd_group.command("报告", aliases=["拉取"])
    async def cmd_pull_report(self, event: AstrMessageEvent):
        """
        拉取指定任务报告并推送 / Pull and push a specific task report

        通过任务 ID 查询分析状态接口并获取报告全文。
        不传 ID 时自动拉取最新任务；传 ID 时拉取指定任务。

        大盘复盘走 market_review_report 字段，个股走 result.report 字段。
        用法: 大盘报告        (最新任务)
             大盘报告 <ID>    (指定任务)

        Parameters:
            event (AstrMessageEvent): 用户聊天消息
        Yields:
            plain_result: 状态提示（拉取中 / 推送中）
        """
        text = event.get_message_str().strip()
        # 提取 ID 参数：不传则默认 "last"，传则用指定 ID
        parts = text.split(None, 1)
        task_id = "last"
        if len(parts) > 1:
            task_id = parts[1].strip()

        if task_id == "last":
            # 获取最新任务
            data = await self._api_get("api/v1/analysis/tasks")
            if not data or not data.get("tasks"):
                yield event.plain_result("❌ 暂无历史任务")
                return
            task_id = data["tasks"][0]["task_id"]
            yield event.plain_result("⏳ 正在拉取最新报告...")

        # 拉取报告
        data = await self._api_get(f"api/v1/analysis/status/{task_id}")
        if not data:
            yield event.plain_result(f"❌ 未找到任务 {task_id[:16]}...")
            return

        # 获取报告内容：大盘复盘走 market_review_report，个股走 result
        report = data.get("market_review_report")
        if not report:
            # 尝试取个股报告
            result = data.get("result")
            if result and isinstance(result, dict):
                report = result.get("report") or result.get("markdown")
        if not report:
            yield event.plain_result(
                f"⚠️ 任务 {data.get('stock_name', '')} 暂无报告内容"
            )
            return

        yield event.plain_result("✅ 已获取报告，正在推送...")

        # 按输出模式分流
        content = self._normalize_content(report)
        if self.output_mode == "markdown":
            await self._process_markdown_mode(content)
        elif self.output_mode == "text":
            await self._process_text_mode(content)
        else:
            await self._process_image_mode(content)

    # ================================================================
    #  入口：处理股票分析数据
    # ================================================================

    async def process_stock_analysis(self, data: dict):
        """
        处理股票分析数据(入口方法)。

        兼容两种来源链路:
          - DSA AstrBot Sender -> HTML 内容(含样式)
          - 其他来源 -> Markdown 内容

        按 output_mode 三路分流:
          - text 模式 -> 清理 MD 语法，纯文本推送(微信转发友好)
          - markdown 模式 -> 保留 MD 语法，带渲染标记推送(Telegram/WebUI)
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

            # 按输出模式分流
            if self.output_mode == "markdown":
                await self._process_markdown_mode(content)
            elif self.output_mode == "text":
                await self._process_text_mode(content)
            else:
                await self._process_image_mode(content)

        except Exception as e:
            logger.error(f"每日股票分析适配器: 处理股票分析数据时出错: {e}")
            raise

    # ================================================================
    #  文字模式 (text)
    # ================================================================

    async def _process_text_mode(self, content: str):
        """
        文字模式：清理 markdown 语法，纯文本推送。

        适合微信转发给好友 — 没有 **、##、``` 等语法符号，
        纯净的文本内容，转发后一目了然。
        """
        clean = self._md_to_plaintext(content)
        await self._send_to_targets([clean], mode="text")

    # ================================================================
    #  Markdown 模式
    # ================================================================

    async def _process_markdown_mode(self, content: str):
        """
        Markdown 模式：保留完整 markdown 语法，带渲染标记推送。

        适合支持 markdown 渲染的平台 (Telegram、AstrBot WebUI 等)。
        """
        await self._send_to_targets([content], mode="markdown")

    # ================================================================
    #  图片模式 (image)
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

                if mode == "markdown":
                    # Markdown 模式：带渲染标记推送
                    text = chunks[0] if chunks else ""
                    if text:
                        await self.context.send_message(
                            target_id,
                            MessageChain().message(text).use_markdown(),
                        )
                elif mode == "text":
                    # 纯文本模式：整篇推送，适合微信转发
                    text = chunks[0] if chunks else ""
                    if text:
                        await self.context.send_message(
                            target_id,
                            MessageChain().message(text),
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
    def _md_to_plaintext(content: str) -> str:
        """
        Markdown → 纯文本清理。

        把 markdown 语法符号去掉，保留可读内容。
        微信转发后不会出现 **、##、``` 等符号。

        清理规则:
          - # / ## / ### 标题 → 标题文字 (加空行分隔)
          - **bold** → bold
          - *italic* / _italic_ → italic
          - ~~删除线~~ → 删除线
          - [text](url) → text
          - ![alt](url) → [图片]
          - ```代码块``` → 代码内容
          - | 表格行 | → 删掉 | 管道符，保留文字
          - - / * / + 无序列表 → • 前缀
          - 1. 有序列表 → 保留数字前缀
          - > 引用块 → 去掉 > 前缀
          - --- / *** 分隔线 → 换行
        """
        if not content:
            return ""
        lines = content.split("\n")

        cleaned: list[str] = []

        in_code_block = False
        code_lines: list[str] = []
        table_buffer: list[str] = []

        for line in lines:
            # 代码块 toggle
            if line.strip().startswith("```"):
                if in_code_block:
                    # 结束代码块：输出收集的代码内容
                    if code_lines:
                        cleaned.append("\n".join(code_lines))
                        code_lines = []
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # 空行保留
            if not line.strip():
                cleaned.append("")
                continue

            # 分隔线
            if re.match(r"^[\s]*[-*_]{3,}\s*$", line):
                cleaned.append("")
                continue

            # 标题: ### xxx → xxx (加空行)
            m = re.match(r"^(#{1,6})\s+(.+)$", line)
            if m:
                # 遇到标题时先刷新表格缓冲区
                if table_buffer:
                    cleaned.extend(DSAPusher._format_table_block(table_buffer))
                    table_buffer = []
                cleaned.append("")
                cleaned.append(m.group(2).strip())
                cleaned.append("")
                continue

            # 引用: > xxx → xxx
            line = re.sub(r"^>\s*", "", line)

            # 图片: ![alt](url) → [图片]
            line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "[图片]", line)

            # 链接: [text](url) → text
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)

            # 加粗: **text** / __text__ → text
            line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            line = re.sub(r"__(.+?)__", r"\1", line)

            # 行内代码: `code` → code
            line = re.sub(r"`([^`]+)`", r"\1", line)

            # 删除线: ~~text~~ → text
            line = re.sub(r"~~(.+?)~~", r"\1", line)

            # 斜体: *text* / _text_ → text (注意避开列表符号)
            # 只在单词边界匹配，不匹配行首的列表 * 号
            line = re.sub(r"(?<!\w)\*(?!\*)(.+?)(?<!\*)\*(?!\w)", r"\1", line)
            line = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", line)

            # 表格行 → 缓冲处理（连续表格行整体格式化）
            if "|" in line and re.match(r"^\s*\|.*\|\s*$", line):
                table_buffer.append(line)
                continue

            # 非表格行 → 刷新缓冲区
            if table_buffer:
                cleaned.extend(DSAPusher._format_table_block(table_buffer))
                table_buffer = []

            # 无序列表: - xxx / * xxx / + xxx → • xxx
            line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)

            # 有序列表: 1. xxx → 保留原样
            # (无需处理)

            cleaned.append(line)

        # 刷新剩余的表格缓冲区
        if table_buffer:
            cleaned.extend(DSAPusher._format_table_block(table_buffer))

        return "\n".join(cleaned)

    @staticmethod
    def _format_table_block(table_lines: list[str]) -> list[str]:
        """将 Markdown 表格转为微信友好的纯文本卡片格式

        转换规则:
        - 所有表格统一用卡片式展示
        - 第一列作为卡片标题，其他列作为键值对列出
        - 数字排名自动与第二列合并为标题（如: "1. 餐饮业"）
        - 卡片之间用 ━━━━━ 分隔，最后一个卡片后也加分隔线

        Args:
            table_lines: Markdown 表格行列表

        Returns:
            格式化后的文本行列表
        """
        try:
            # 解析所有数据行
            rows: list[list[str]] = []
            for line in table_lines:
                # 跳过空行和无效行
                if not line.strip():
                    continue
                # 分割单元格
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                # 跳过分隔行 (如 | --- | --- |)
                if all(re.match(r"^[-:]+$", c) for c in cells if c):
                    continue
                rows.append(cells)

            if not rows:
                return []

            ncols = max(len(r) for r in rows)
            if ncols == 0:
                return []

            # 补齐列数不一致的行
            for r in rows:
                while len(r) < ncols:
                    r.append("")

            # ---- 所有表格统一卡片式展示 ----
            header = rows[0]
            result: list[str] = []

            for idx, row in enumerate(rows[1:]):  # 跳过表头
                # 第一列当标题
                title = row[0] if row[0] else f"项目{idx + 1}"

                # 如果第一列是数字（排名），和第二列合并作为标题
                # 例如: "1" + "餐饮业" → "1. 餐饮业"
                if title.isdigit() and ncols >= 3 and row[1]:
                    title = f"{title}. {row[1]}"
                    skip_col = 2  # 跳过第二列（已合并到标题）
                else:
                    skip_col = 1  # 从第二列开始列出

                lines = [f"📋 {title}", ""]

                # 其他列全部列出
                for i in range(skip_col, ncols):
                    key = header[i] if i < len(header) else f"列{i + 1}"
                    value = row[i] if i < len(row) else ""
                    if value:  # 只列出有值的列
                        lines.append(f"{key}：{value}")

                result.extend(lines)

                # 每个卡片之间加分隔线
                result.append("")
                result.append("━━━━━━━━━━━━━━━━━━")
                result.append("")

            # 去掉最后多余的空行和分隔线
            while result and result[-1] in ("", "━━━━━━━━━━━━━━━━━━"):
                result.pop()

            # 最后加一个分隔线作为结尾，后面加空行
            result.append("")
            result.append("━━━━━━━━━━━━━━━━━━")
            result.append("")

            return result

        except Exception as e:
            # 表格格式化失败时，返回原始内容
            logger.warning(f"表格格式化失败: {e}")
            return table_lines

        return result

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
      | 指数 | 点位 |
      |---|---|
      | 上证指数 | 3500 |
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
