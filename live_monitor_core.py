"""B站直播间数据监控 - 核心轮询逻辑（GUI / CLI 共用）"""

import asyncio
import csv
import os
from datetime import datetime
from bilibili_api import live, Credential

OFFLINE_TIMEOUT = 300  # 下播后 5 分钟仍未开播则退出


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
    """多房间时 output 视为目录"""
    if multi_room:
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
        self._running = False
        self._last_watched = None
        self._last_likes = None
        self._offline_seconds = 0
        self._was_live = True

        # 回调钩子
        self.on_data = None      # (room_id, data_dict) -> None
        self.on_change = None    # (room_id, field, old, new, delta) -> None
        self.on_offline = None   # (room_id, seconds) -> None
        self.on_offline_end = None  # (room_id) -> None
        self.on_error = None     # (room_id, error) -> None
        self.on_relive = None    # (room_id) -> None  重新开播

    async def run(self):
        """异步轮询循环，直到停止或超时下播"""
        ensure_csv(self.output)
        room = live.LiveRoom(room_display_id=self.room_id, credential=self.credential)
        self._running = True

        while self._running:
            try:
                info = await room.get_room_info()
            except Exception as e:
                if self.on_error:
                    self.on_error(self.room_id, str(e))
                await asyncio.sleep(self.interval)
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

            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False
