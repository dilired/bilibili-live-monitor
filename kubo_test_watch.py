"""B站直播间数据监控脚本
定时轮询直播间的人气值、看过人数、点赞数，流式写入本地 CSV 文件。
支持同时监控多个直播间。

用法:
    python kubo_test_watch.py -r 13308358 23083532
    python kubo_test_watch.py -r 13308358 -i 60 -o ./data/
    python kubo_test_watch.py -r 13308358 -o live.csv   # 多房间合并到一个文件

打包为 exe:
    pip install pyinstaller
    pyinstaller --onefile kubo_test_watch.py
"""

import argparse
import asyncio
import csv
import os
from datetime import datetime
from bilibili_api import live, Credential

# ========== 默认配置 ==========
DEFAULT_INTERVAL = 60          # 轮询间隔（秒）
DEFAULT_OUTPUT = "live_data.csv"
# =============================


def parse_args():
    p = argparse.ArgumentParser(description="B站直播间数据监控")
    p.add_argument("-r", "--rooms", type=int, nargs="+", required=True, help="直播间 ID，支持多个，空格分隔")
    p.add_argument("-i", "--interval", type=int, default=DEFAULT_INTERVAL, help=f"轮询间隔秒数 (默认 {DEFAULT_INTERVAL})")
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help=f"输出 CSV 路径或目录 (默认 {DEFAULT_OUTPUT})")
    p.add_argument("--sessdata", default="", help="B站 SESSDATA (可选，匿名也能用)")
    return p.parse_args()


def resolve_output_path(output: str, room_id: int, multi_room: bool) -> str:
    """多房间时 output 视为目录，单房间时 output 就是文件路径"""
    if multi_room:
        os.makedirs(output, exist_ok=True)
        return os.path.join(output, f"live_{room_id}.csv")
    return output


def init_csv(path: str):
    """CSV 不存在则创建并写入表头"""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "room_id", "popularity", "popularity_text",
                             "watched_num", "watched_text", "likes"])


def append_row(path: str, room_id: int, popularity: dict, watched: dict, like_info: dict):
    """流式追加一行到 CSV"""
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


OFFLINE_TIMEOUT = 300  # 下播后持续 5 分钟仍未开播则退出


async def poll_single(room_id: int, interval: int, output: str, credential: Credential):
    """轮询单个直播间"""
    init_csv(output)
    room = live.LiveRoom(room_display_id=room_id, credential=credential)

    last_watched = None
    last_likes = None
    offline_seconds = 0      # 累计下播时长
    was_live = True          # 上一次是否在直播

    while True:
        try:
            info = await room.get_room_info()
            room_info = info.get("room_info", {})
            live_status = room_info.get("live_status", 1)
            is_live = (live_status == 1)

            # 处理下播状态
            if not is_live:
                if was_live:
                    # 刚下播
                    offline_seconds = 0
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] [房间{room_id}] 已下播，等待 {OFFLINE_TIMEOUT // 60} 分钟内重新开播...", flush=True)
                offline_seconds += interval
                if offline_seconds >= OFFLINE_TIMEOUT:
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] [房间{room_id}] 下播超过 {OFFLINE_TIMEOUT // 60} 分钟，停止监控", flush=True)
                    return
            else:
                offline_seconds = 0

            was_live = is_live

            popularity = info.get("popularity", {})
            watched = info.get("watched_show", {})
            like_info = info.get("like_info_v3", {})

            watched_num = watched.get("num")
            total_likes = like_info.get("total_likes", 0)

            # 流式写入 CSV（下播期间也写，保留完整记录）
            append_row(output, room_id, popularity, watched, like_info)

            # 终端输出
            ts = datetime.now().strftime("%H:%M:%S")
            pop_text = popularity.get("popularity_text", "N/A")
            watched_text = watched.get("text_large", "N/A")
            print(f"[{ts}] [房间{room_id}] 人气: {pop_text} | 看过: {watched_text} (num={watched_num}) | 点赞: {total_likes}", flush=True)

            if last_watched is not None and watched_num != last_watched:
                print(f"  >>> 看过人数变化: {last_watched} -> {watched_num} (+{watched_num - last_watched})", flush=True)
            if last_likes is not None and total_likes != last_likes:
                print(f"  >>> 点赞数变化: {last_likes} -> {total_likes} (+{total_likes - last_likes})", flush=True)

            last_watched = watched_num
            last_likes = total_likes

        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] [房间{room_id}] 获取失败: {e}", flush=True)

        await asyncio.sleep(interval)


async def run(room_ids: list[int], interval: int, output: str, credential: Credential):
    multi = len(room_ids) > 1

    for rid in room_ids:
        path = resolve_output_path(output, rid, multi)
        print(f"房间 {rid} -> {os.path.abspath(path)}", flush=True)

    tasks = []
    for rid in room_ids:
        path = resolve_output_path(output, rid, multi)
        tasks.append(poll_single(rid, interval, path, credential))

    await asyncio.gather(*tasks)


def main():
    args = parse_args()
    credential = Credential(
        sessdata=args.sessdata, bili_jct="", buvid3="", dedeuserid="", ac_time_value="",
    ) if args.sessdata else None

    print(f"房间: {args.rooms}  轮询间隔: {args.interval}s  Ctrl+C 停止", flush=True)
    try:
        asyncio.run(run(args.rooms, args.interval, args.output, credential))
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    main()
