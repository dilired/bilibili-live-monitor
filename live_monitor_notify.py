"""飞书 Webhook 播报模块"""

import json
import urllib.request
from datetime import datetime


def _send_card(webhook_url: str, header_text: str, header_color: str, content_lines: list):
    elements = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(content_lines)}
    }, {
        "tag": "hr"
    }, {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"BiliLiveMonitor · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}]
    }]

    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": header_text},
                "template": header_color,
            },
            "elements": elements,
        },
    }

    req = urllib.request.Request(
        url=webhook_url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read().decode()[:200]}")


def _duration_str(seconds: int) -> str:
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}时{m}分{s}秒"
    return f"{m}分{s}秒"


def notify_start(webhook_url: str, room_info: list, interval: int, output: str):
    """监控启动播报  room_info: [(room_id, anchor_name, live_start), ...]"""
    if not webhook_url:
        return
    room_lines = []
    for item in room_info:
        rid, name, live_start = item if len(item) == 3 else (item[0], item[1], "")
        label = f"{name}" if name else f"房间 {rid}"
        extra = ""
        if live_start:
            try:
                dt = datetime.strptime(live_start, "%Y-%m-%d %H:%M:%S")
                dur = int((datetime.now() - dt).total_seconds())
                extra = f"｜已播 {_duration_str(dur)}（{live_start} 开播）"
            except Exception:
                extra = f"｜开播 {live_start}"
        room_lines.append(f"- {label}（{rid}）{extra}")
    room_str = "\n".join(room_lines)

    _send_card(webhook_url,
        header_text="📊 直播监控已启动",
        header_color="green",
        content_lines=[
            f"**监控房间：**\n{room_str}",
            f"**轮询间隔：** {interval}s",
            f"**保存路径：** {output}",
            f"**启动时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ])


def notify_stop(webhook_url: str, room_info: list, started_at: datetime,
                stats: dict = None):
    """监控停止播报"""
    if not webhook_url:
        return
    duration = datetime.now() - started_at
    dur_str = _duration_str(int(duration.total_seconds()))

    room_lines = []
    for item in room_info:
        rid, name = item[0], item[1] if len(item) >= 2 else ""
        live_start = item[2] if len(item) >= 3 else ""
        label = f"{name}" if name else f"房间 {rid}"
        s = stats.get(rid, {}) if stats else {}
        w = s.get('watched', 'N/A')
        a = s.get('audience', 'N/A')
        extra = f"｜开播 {live_start}" if live_start else ""
        audience_part = f"｜观众 **{a}**" if a != 'N/A' else ""
        room_lines.append(f"- {label}（{rid}）｜看过 **{w}**{audience_part}{extra}")
    room_str = "\n".join(room_lines)

    _send_card(webhook_url,
        header_text="⏹ 直播监控已停止",
        header_color="red",
        content_lines=[
            f"**运行时长：** {dur_str}",
            f"**监控结果：**\n{room_str}",
        ])
