"""飞书 Webhook 播报模块"""

import json
import urllib.request
from datetime import datetime


def _send_card(webhook_url: str, header_text: str, header_color: str, content_lines: list):
    """发送飞书消息卡片"""
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
        url=webhook_url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def notify_start(webhook_url: str, room_ids: list, interval: int, output: str):
    """监控启动播报"""
    if not webhook_url:
        return
    try:
        room_str = "、".join(str(r) for r in room_ids)
        _send_card(webhook_url,
            header_text=f"📊 直播监控已启动",
            header_color="green",
            content_lines=[
                f"**直播间：** {room_str}",
                f"**轮询间隔：** {interval}s",
                f"**保存路径：** {output}",
                f"**启动时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ])
    except Exception as e:
        print(f"[Notify] 启动播报失败: {e}", flush=True)


def notify_stop(webhook_url: str, room_ids: list, started_at: datetime,
                stats: dict = None):
    """监控停止播报"""
    if not webhook_url:
        return
    try:
        duration = datetime.now() - started_at
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{hours}时{minutes}分{seconds}秒" if hours else f"{minutes}分{seconds}秒"

        room_str = "、".join(str(r) for r in room_ids)
        lines = [
            f"**直播间：** {room_str}",
            f"**运行时长：** {duration_str}",
        ]
        if stats:
            for rid, s in stats.items():
                lines.append(f"**房间 {rid}：** 看过 {s.get('watched', 'N/A')} | 点赞 {s.get('likes', 'N/A')}")

        _send_card(webhook_url,
            header_text=f"⏹ 直播监控已停止",
            header_color="red",
            content_lines=lines)
    except Exception as e:
        print(f"[Notify] 停止播报失败: {e}", flush=True)
