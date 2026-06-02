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


def record_session(output_dir: str, room_id: int, anchor_name: str,
                   started_at: str, ended_at: str, end_reason: str,
                   final_watched, max_likes: int, live_start: str = ""):
    """记录一次监控 session 到 sessions.csv（追加模式）"""
    path = os.path.join(output_dir, "sessions_v2.csv")
    existed = os.path.exists(path)
    os.makedirs(output_dir, exist_ok=True)
    try:
        with _open_with_retry(path, "a") as f:
            writer = csv.writer(f)
            if not existed:
                writer.writerow(["session_start", "session_end", "room_id",
                                 "anchor_name", "end_reason",
                                 "final_watched", "max_likes", "live_start"])
            writer.writerow([started_at, ended_at, room_id, anchor_name or '',
                             end_reason, final_watched or '', max_likes or '',
                             live_start])
    except PermissionError:
        pass


def _open_with_retry(path, mode, encoding="utf-8-sig", retries=3, delay=0.5):
    """带重试的文件打开，解决 Windows Excel 锁文件问题"""
    import time
    for i in range(retries):
        try:
            return open(path, mode, newline="", encoding=encoding)
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(delay)
    return None


async def get_anchor_name(room_id: int) -> str:
    """获取主播名"""
    room = live.LiveRoom(room_display_id=room_id)
    info = await room.get_room_info()
    return info.get('anchor_info', {}).get('base_info', {}).get('uname', '')


async def get_room_brief(room_id: int) -> tuple:
    """获取主播名和开播时间"""
    room = live.LiveRoom(room_display_id=room_id)
    info = await room.get_room_info()
    name = info.get('anchor_info', {}).get('base_info', {}).get('uname', '')
    live_ts = info.get('room_info', {}).get('live_start_time', 0)
    live_start = datetime.fromtimestamp(live_ts).strftime("%Y-%m-%d %H:%M:%S") if live_ts else ""
    return name, live_start


def sanitize_filename(name: str) -> str:
    """去除文件名中的不安全字符"""
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '', name)
    name = name.replace(' ', '_').strip('._')
    return name or 'unknown'


def ensure_csv(path: str):
    """CSV 不存在则创建并写入表头（重试防 Windows 文件锁）"""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _open_with_retry(path, "w") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "room_id", "popularity", "popularity_text",
                             "watched_num", "watched_text", "likes", "audience_count",
                             "live_start_time"])


def write_row(path: str, room_id: int, popularity: dict, watched: dict,
              like_info: dict, live_start: str = "", audience_count=0):
    """流式追加一行（重试防 Windows 文件锁）"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _open_with_retry(path, "a") as f:
            writer = csv.writer(f)
            writer.writerow([
                ts, room_id,
                popularity.get("popularity", ""),
                popularity.get("popularity_text", ""),
                watched.get("num", ""),
                watched.get("text_large", ""),
                like_info.get("total_likes", ""),
                audience_count,
                live_start,
            ])
    except PermissionError:
        return False  # Windows 文件被锁
    return True


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
        self.live_start_time = ''   # 开播时间（格式化字符串）
        self._running = False
        self._last_watched = None
        self._last_likes = None
        self._last_audience = 0
        self._max_audience = 0
        self._max_likes = 0
        self._ws_watched = None      # WS 修正: {"num": int, "text_large": str, "text_small": str}
        self._offline_seconds = 0
        self._was_live = True
        self._error_count = 0
        self._last_error_time = None
        self._write_fail_count = 0  # 连续写入失败计数
        self._write_blocked_notified = False  # 是否已发送锁文件通知
        self.buffer = []            # 当前会话数据点: [{timestamp, watched_num, popularity, likes}, ...]

        # 回调钩子
        self.on_data = None      # (room_id, data_dict) -> None
        self.on_change = None    # (room_id, field, old, new, delta) -> None
        self.on_offline = None   # (room_id, seconds) -> None
        self.on_offline_end = None  # (room_id) -> None
        self.on_error = None     # (room_id, error) -> None
        self.on_relive = None    # (room_id) -> None  重新开播
        self.on_name = None      # (room_id, name) -> None  主播名就绪
        self.on_write_blocked = None   # (room_id) -> None  CSV被锁

    async def run(self):
        """异步轮询循环（HTTP）+ WebSocket 看过人数修正"""
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
                self.output = os.path.join(output_dir, f"live_{self.room_id}_{safe_name}_v2.csv")
                if self.on_name:
                    self.on_name(self.room_id, uname)
        except Exception:
            pass

        ensure_csv(self.output)

        # 启动 WebSocket 连接（优先获取真实看过人数）
        ws_task = asyncio.create_task(self._run_ws())

        try:
            while self._running:
                try:
                    info = await room.get_room_info()
                except Exception as e:
                    self._error_count += 1
                    now = datetime.now()
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

                # 开播时间
                live_ts = room_info.get("live_start_time", 0)
                if live_ts:
                    self.live_start_time = datetime.fromtimestamp(live_ts).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    self.live_start_time = ""

                # 房间观众数（真实在线人数）
                try:
                    audience_count = (info.get('room_rank_info', {})
                                      .get('user_rank_entry', {})
                                      .get('user_contribution_rank_entry', {})
                                      .get('count', 0))
                except Exception:
                    audience_count = 0

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
                        self.buffer.clear()
                        self._max_audience = 0
                        self._max_likes = 0
                        self._ws_watched = None  # 重开播后重置 WS 缓存
                    self._offline_seconds = 0
                self._was_live = is_live

                popularity = info.get("popularity", {})
                http_watched = info.get("watched_show", {})
                like_info = info.get("like_info_v3", {})

                # 看过人数: WS 优先（真实值），HTTP 兜底
                if self._ws_watched:
                    watched_num = self._ws_watched["num"]
                    watched_text = self._ws_watched.get("text_large", "N/A")
                    write_watched = self._ws_watched
                else:
                    watched_num = http_watched.get("num")
                    watched_text = http_watched.get("text_large", "N/A")
                    write_watched = http_watched

                total_likes = like_info.get("total_likes", 0)

                # 写 CSV
                if write_row(self.output, self.room_id, popularity, write_watched, like_info,
                             self.live_start_time, audience_count):
                    self._write_fail_count = 0
                    self._write_blocked_notified = False
                else:
                    self._write_fail_count += 1
                    if self._write_fail_count >= 5:
                        msg = f"CSV 写入持续失败，请检查文件是否被其他程序打开 ({self.output})"
                        if self.on_error:
                            self.on_error(self.room_id, msg)
                        if not self._write_blocked_notified and self.on_write_blocked:
                            self.on_write_blocked(self.room_id)
                            self._write_blocked_notified = True

                # 数据回调
                data = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "popularity": popularity.get("popularity"),
                    "popularity_text": popularity.get("popularity_text", "N/A"),
                    "watched_num": watched_num,
                    "watched_text": watched_text,
                    "likes": total_likes,
                    "audience_count": audience_count,
                    "is_live": is_live,
                    "live_start_time": self.live_start_time,
                }
                if self.on_data:
                    self.on_data(self.room_id, data)

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
                self._last_audience = audience_count
                if audience_count and audience_count > self._max_audience:
                    self._max_audience = audience_count
                if total_likes and total_likes > self._max_likes:
                    self._max_likes = total_likes

                # 可中断的 sleep
                for _ in range(self.interval):
                    if not self._running:
                        return
                    await asyncio.sleep(1)
        finally:
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

    async def _run_ws(self):
        """WebSocket 连接管理：持续接收 WATCHED_CHANGE，断线自动重连"""
        while self._running:
            dm = None
            try:
                dm = live.LiveDanmaku(
                    room_display_id=self.room_id,
                    credential=self.credential,
                )
                dm.add_event_listener("WATCHED_CHANGE", self._on_ws_watched)
                await dm.connect()
            except Exception as e:
                if self._running and self.on_error:
                    self.on_error(self.room_id, f"WebSocket: {e}")
            finally:
                if dm is not None:
                    try:
                        await dm.disconnect()
                    except Exception:
                        pass
            if not self._running:
                break
            await asyncio.sleep(5)

    def _on_ws_watched(self, callback_info):
        """WS WATCHED_CHANGE 回调：更新真实看过人数"""
        inner_data = callback_info.get("data", {}).get("data", {})
        num = inner_data.get("num")
        if num is not None:
            self._ws_watched = {
                "num": num,
                "text_large": inner_data.get("text_large", ""),
                "text_small": inner_data.get("text_small", ""),
            }

    def stop(self):
        self._running = False
