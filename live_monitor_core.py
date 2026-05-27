"""B站直播间数据监控 - 核心轮询逻辑（GUI / CLI 共用）"""

import asyncio
import csv
import os
import re
from datetime import datetime

# PyInstaller 打包后 SSL 证书需显式指定
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from bilibili_api import live, Credential

OFFLINE_TIMEOUT = 300  # 下播后 5 分钟仍未开播则退出
MAX_ROOMS = 10           # 同时监控房间数上限


async def get_anchor_name(room_id: int) -> str:
    """获取主播名"""
    room = live.LiveRoom(room_display_id=room_id)
    info = await room.get_room_info()
    return info.get('anchor_info', {}).get('base_info', {}).get('uname', '')


def sanitize_filename(name: str) -> str:
    """去除文件名中的不安全字符"""
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '', name)
    name = name.replace(' ', '_').strip('._')
    return name or 'unknown'


def ensure_csv(path: str):
    """CSV 不存在则创建并写入表头"""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "room_id", "popularity", "popularity_text",
                             "watched_num", "watched_text", "likes"])


def write_row(path: str, room_id: int, popularity: dict, watched: dict, like_info: dict):
    """流式追加一行"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            ts, room_id,
            popularity.get("popularity", ""),
            popularity.get("popularity_text", ""),
            watched.get("num", ""),
            watched.get("text_large", ""),
            like_info.get("total_likes", ""),
        ])


def resolve_output_path(output: str, room_id: int, multi_room: bool) -> str:
    """如果 output 是目录或以 / 结尾，自动生成文件名（临时用 room_id，后续会加上主播名）"""
    is_dir = multi_room or output.endswith(("/", "\\"))
    if not is_dir and os.path.exists(output):
        is_dir = os.path.isdir(output)
    if is_dir:
        os.makedirs(output, exist_ok=True)
        return os.path.join(output, f"live_{room_id}.csv")
    return output


class LiveMonitor:
    """单个直播间的监控器，提供回调钩子供 GUI/CLI 使用"""

    def __init__(self, room_id: int, output: str, interval: int = 60,
                 credential: Credential = None):
        self.room_id = room_id
        self.output = output
        self.interval = interval
        self.credential = credential
        self.anchor_name = ''       # 主播名，启动后获取
        self._running = False
        self._last_watched = None
        self._last_likes = None
        self._offline_seconds = 0
        self._was_live = True
        self._error_count = 0
        self._last_error_time = None
        self.buffer = []            # 当前会话数据点: [{timestamp, watched_num, popularity, likes}, ...]

        # 回调钩子
        self.on_data = None      # (room_id, data_dict) -> None
        self.on_change = None    # (room_id, field, old, new, delta) -> None
        self.on_offline = None   # (room_id, seconds) -> None
        self.on_offline_end = None  # (room_id) -> None
        self.on_error = None     # (room_id, error) -> None
        self.on_relive = None    # (room_id) -> None  重新开播
        self.on_name = None      # (room_id, name) -> None  主播名就绪

    async def run(self):
        """异步轮询循环，直到停止或超时下播"""
        room = live.LiveRoom(room_display_id=self.room_id, credential=self.credential)
        self._running = True

        # 首次获取主播名，更新 CSV 文件名
        try:
            info = await room.get_room_info()
            uname = info.get('anchor_info', {}).get('base_info', {}).get('uname', '')
            if uname:
                self.anchor_name = uname
                safe_name = sanitize_filename(uname)
                output_dir = os.path.dirname(self.output) or '.'
                self.output = os.path.join(output_dir, f"live_{self.room_id}_{safe_name}.csv")
                if self.on_name:
                    self.on_name(self.room_id, uname)
        except Exception:
            pass

        ensure_csv(self.output)

        while self._running:
            try:
                info = await room.get_room_info()
            except Exception as e:
                self._error_count += 1
                now = datetime.now()
                # 连续错误发生时，最多每 30 秒上报一次，避免刷屏卡 UI
                if self._last_error_time is None or \
                   (now - self._last_error_time).total_seconds() >= 30:
                    if self.on_error:
                        repeat = f" (已连续失败 {self._error_count} 次)" if self._error_count > 1 else ""
                        self.on_error(self.room_id, str(e) + repeat)
                    self._last_error_time = now
                    self._error_count = 0
                for _ in range(self.interval):
                    if not self._running:
                        return
                    await asyncio.sleep(1)
                continue

            room_info = info.get("room_info", {})
            live_status = room_info.get("live_status", 1)
            is_live = (live_status == 1)

            # 下播处理
            if not is_live:
                if self._was_live and self.on_offline:
                    self.on_offline(self.room_id, 0)
                    self._offline_seconds = 0
                self._offline_seconds += self.interval
                if self._offline_seconds >= OFFLINE_TIMEOUT:
                    if self.on_offline_end:
                        self.on_offline_end(self.room_id)
                    return
            else:
                if not self._was_live and self.on_relive:
                    self.on_relive(self.room_id)
                self._offline_seconds = 0
            self._was_live = is_live

            popularity = info.get("popularity", {})
            watched = info.get("watched_show", {})
            like_info = info.get("like_info_v3", {})

            watched_num = watched.get("num")
            total_likes = like_info.get("total_likes", 0)

            # 写 CSV
            write_row(self.output, self.room_id, popularity, watched, like_info)

            # 数据回调
            data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "popularity": popularity.get("popularity"),
                "popularity_text": popularity.get("popularity_text", "N/A"),
                "watched_num": watched_num,
                "watched_text": watched.get("text_large", "N/A"),
                "likes": total_likes,
                "is_live": is_live,
            }
            if self.on_data:
                self.on_data(self.room_id, data)

            # 追加当前会话 Buffer
            self.buffer.append(data)

            # 变化检测
            if self._last_watched is not None and watched_num != self._last_watched:
                if self.on_change:
                    self.on_change(self.room_id, "watched", self._last_watched,
                                   watched_num, watched_num - self._last_watched)
            if self._last_likes is not None and total_likes != self._last_likes:
                if self.on_change:
                    self.on_change(self.room_id, "likes", self._last_likes,
                                   total_likes, total_likes - self._last_likes)

            self._last_watched = watched_num
            self._last_likes = total_likes

            # 可中断的 sleep：每秒检查一次 _running，响应停止指令
            for _ in range(self.interval):
                if not self._running:
                    return
                await asyncio.sleep(1)

    def stop(self):
        self._running = False
