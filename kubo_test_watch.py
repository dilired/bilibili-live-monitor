"""B站直播间数据监控 - 命令行版本
用法:
    python kubo_test_watch.py -r 13308358
    python kubo_test_watch.py -r 26044264 13308358 -i 60 -o ./data/
"""

import argparse
import asyncio
from datetime import datetime
from bilibili_api import Credential
from live_monitor_core import LiveMonitor, resolve_output_path

DEFAULT_INTERVAL = 60
DEFAULT_OUTPUT = "live_data.csv"


def parse_args():
    p = argparse.ArgumentParser(description="B站直播间数据监控")
    p.add_argument("-r", "--rooms", type=int, nargs="+", required=True, help="直播间 ID")
    p.add_argument("-i", "--interval", type=int, default=DEFAULT_INTERVAL, help="轮询间隔秒数")
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="CSV 输出路径")
    p.add_argument("--sessdata", default="", help="B站 SESSDATA (可选)")
    return p.parse_args()


def on_data(room_id, data):
    print(f"[{data['timestamp'][11:19]}] [房间{room_id}] "
          f"人气: {data['popularity_text']} | "
          f"看过: {data['watched_text']} (num={data['watched_num']}) | "
          f"点赞: {data['likes']}", flush=True)


def on_change(room_id, field, old, new, delta):
    sign = "+" if delta >= 0 else ""
    label = "看过人数" if field == "watched" else "点赞数"
    print(f"  >>> {label}变化: {old} -> {new} ({sign}{delta})", flush=True)


def on_offline(room_id, seconds):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [房间{room_id}] 已下播，等待 5 分钟内重新开播...", flush=True)


def on_offline_end(room_id):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [房间{room_id}] 下播超过 5 分钟，停止监控", flush=True)


def on_error(room_id, error):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [房间{room_id}] 获取失败: {error}", flush=True)


def on_relive(room_id):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [房间{room_id}] 重新开播！", flush=True)


async def run(room_ids, interval, output, credential):
    multi = len(room_ids) > 1
    monitors = []
    for rid in room_ids:
        path = resolve_output_path(output, rid, multi)
        print(f"房间 {rid} -> {path}")
        m = LiveMonitor(rid, path, interval=interval, credential=credential)
        m.on_data = on_data
        m.on_change = on_change
        m.on_offline = on_offline
        m.on_offline_end = on_offline_end
        m.on_error = on_error
        m.on_relive = on_relive
        monitors.append(m)

    print(f"共 {len(room_ids)} 个房间，间隔 {interval}s，Ctrl+C 停止", flush=True)
    await asyncio.gather(*[m.run() for m in monitors])


def main():
    args = parse_args()
    credential = Credential(
        sessdata=args.sessdata, bili_jct="", buvid3="", dedeuserid="", ac_time_value="",
    ) if args.sessdata else None

    try:
        asyncio.run(run(args.rooms, args.interval, args.output, credential))
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    main()
