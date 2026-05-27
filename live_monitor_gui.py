"""B站直播间数据监控 - 图形界面（暖色风格）
Tkinter 实现，零额外依赖，Mac / Windows 通用
"""

import asyncio
import json
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

# PyInstaller 打包后需在导入 bilibili_api 之前设置 SSL 证书路径
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from bilibili_api import Credential

from live_monitor_core import LiveMonitor, resolve_output_path
from live_monitor_notify import notify_start, notify_stop

# ============================================================
#  暖色主题配色
# ============================================================
COLORS = {
    "bg":              "#FEF5E7",   # 暖奶油背景
    "card":            "#FFFAF2",   # 卡片底色
    "card_border":     "#E8D5B8",   # 卡片边框
    "primary":         "#8BB382",   # 鼠尾草绿
    "primary_dark":    "#6B8E64",   # 深鼠尾草绿
    "primary_light":   "#C5DFC0",   # 浅鼠尾草绿
    "brown":           "#9C8B78",   # 暖木棕
    "brown_dark":      "#6B5C4A",   # 深棕（文字）
    "brown_light":     "#C4B5A5",   # 浅棕
    "accent":          "#F4C87A",   # 铃钱金
    "danger":          "#E88B7E",   # 柔和珊瑚红
    "text":            "#6B5C4A",   # 正文棕
    "text_light":      "#9C8B78",   # 次要文字
    "log_bg":          "#FDF5EC",   # 日志背景
    "log_text":        "#5C4B3A",   # 日志文字
    "input_bg":        "#FFFFFF",   # 输入框背景
    "input_border":    "#D4C4B0",   # 输入框边框
    "status_live":     "#8BC48B",   # 运行中绿色
    "status_offline":  "#E8D5B8",   # 已停止
    "status_error":    "#E88B7E",   # 错误
}


class ACFrame(tk.Frame):
    """暖色风格圆角卡片框"""
    def __init__(self, parent, padding=15, **kwargs):
        kwargs.setdefault("bg", COLORS["card"])
        kwargs.setdefault("highlightbackground", COLORS["card_border"])
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("relief", "solid")
        super().__init__(parent, **kwargs)
        self.inner = tk.Frame(self, bg=COLORS["card"])
        self.inner.pack(fill="both", expand=True, padx=padding, pady=padding)


class ACButton(tk.Canvas):
    """暖色风格按钮 — 圆角、悬停变色"""
    def __init__(self, parent, text, command=None, color=COLORS["primary"],
                 width=140, height=38, font_size=12, **kwargs):
        super().__init__(parent, width=width, height=height,
                         bg=COLORS["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.color = color
        self.color_hover = self._darken(color)
        self.width = width
        self.height = height
        self.radius = 10
        self.current_color = color
        self.text = text
        self.font_size = font_size

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self):
        self.delete("all")
        r = self.radius
        w, h = self.width, self.height
        c = self.current_color
        # 圆角矩形
        self.create_arc(0, 0, r*2, r*2, start=90, extent=90, fill=c, outline=c)
        self.create_arc(w-r*2, 0, w, r*2, start=0, extent=90, fill=c, outline=c)
        self.create_arc(w-r*2, h-r*2, w, h, start=270, extent=90, fill=c, outline=c)
        self.create_arc(0, h-r*2, r*2, h, start=180, extent=90, fill=c, outline=c)
        self.create_rectangle(r, 0, w-r, h, fill=c, outline=c)
        self.create_rectangle(0, r, w, h-r, fill=c, outline=c)
        # 文字
        self.create_text(w//2, h//2, text=self.text, fill="white",
                         font=("Helvetica Neue", self.font_size, "bold"))

    def _darken(self, hex_color):
        """颜色加深"""
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        return f"#{max(0, r-20):02x}{max(0, g-20):02x}{max(0, b-20):02x}"

    def _on_enter(self, e):
        self.current_color = self.color_hover
        self._draw()

    def _on_leave(self, e):
        self.current_color = self.color
        self._draw()

    def _on_click(self, e):
        if self.command:
            self.command()

    def set_state(self, disabled=False):
        if disabled:
            self.current_color = COLORS["brown_light"]
            self.color = COLORS["brown_light"]
            self.unbind("<Enter>")
            self.unbind("<Leave>")
            self.unbind("<Button-1>")
        else:
            self.current_color = self.color
            self.bind("<Enter>", self._on_enter)
            self.bind("<Leave>", self._on_leave)
            self.bind("<Button-1>", self._on_click)
        self._draw()


class LiveMonitorGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("B站直播数据监控")
        self.root.geometry("700x600")
        self.root.minsize(620, 480)
        self.root.configure(bg=COLORS["bg"])

        self.running = False
        self.monitors = []
        self.monitor_threads = []
        self.msg_queue = queue.Queue()
        self.started_at = None       # 启动时间，用于停止播报
        self.notify_rooms = []       # 上次启动的房间列表

        self._build_ui()
        self._poll_queue()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        # ---- 标题 ----
        title_frame = tk.Frame(self.root, bg=COLORS["bg"])
        title_frame.pack(fill="x", padx=30, pady=(25, 5))

        tk.Label(title_frame, text="B站直播数据监控",
                 font=("Helvetica Neue", 22, "bold"), fg=COLORS["primary_dark"],
                 bg=COLORS["bg"]).pack(anchor="w")
        tk.Label(title_frame, text="B站直播间数据采集工具  ·  定时轮询人气 / 看过人数 / 点赞",
                 font=("Helvetica Neue", 11), fg=COLORS["text_light"],
                 bg=COLORS["bg"]).pack(anchor="w", pady=(2, 0))

        # ---- 设置卡片 ----
        settings_card = ACFrame(self.root, padding=18)
        settings_card.pack(fill="x", padx=30, pady=(15, 10))

        tk.Label(settings_card.inner, text="监控设置",
                 font=("Helvetica Neue", 13, "bold"), fg=COLORS["brown_dark"],
                 bg=COLORS["card"]).pack(anchor="w", pady=(0, 12))

        # 房间 ID
        row1 = tk.Frame(settings_card.inner, bg=COLORS["card"])
        row1.pack(fill="x", pady=3)
        tk.Label(row1, text="直播间 ID", width=10, anchor="w",
                 font=("Helvetica Neue", 12), fg=COLORS["text"],
                 bg=COLORS["card"]).pack(side="left")
        self.room_entry = tk.Entry(row1, font=("Helvetica Neue", 12),
                                   bg=COLORS["input_bg"], fg=COLORS["text"],
                                   insertbackground=COLORS["text"],
                                   highlightbackground=COLORS["input_border"],
                                   highlightthickness=1, relief="flat", bd=0)
        self.room_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.room_entry.insert(0, "13308358")
        tk.Label(row1, text="多个用空格分隔", font=("Helvetica Neue", 10),
                 fg=COLORS["text_light"], bg=COLORS["card"]).pack(side="left", padx=(8, 0))

        # 轮询间隔 + 保存路径
        row2 = tk.Frame(settings_card.inner, bg=COLORS["card"])
        row2.pack(fill="x", pady=3)
        tk.Label(row2, text="轮询间隔", width=10, anchor="w",
                 font=("Helvetica Neue", 12), fg=COLORS["text"],
                 bg=COLORS["card"]).pack(side="left")
        self.interval_var = tk.StringVar(value="60")
        interval_entry = tk.Entry(row2, textvariable=self.interval_var, width=6,
                                  font=("Helvetica Neue", 12),
                                  bg=COLORS["input_bg"], fg=COLORS["text"],
                                  insertbackground=COLORS["text"],
                                  highlightbackground=COLORS["input_border"],
                                  highlightthickness=1, relief="flat", bd=0)
        interval_entry.pack(side="left", ipady=4)
        tk.Label(row2, text="秒", font=("Helvetica Neue", 12),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left", padx=(4, 25))

        # 保存路径
        row3 = tk.Frame(settings_card.inner, bg=COLORS["card"])
        row3.pack(fill="x", pady=3)
        tk.Label(row3, text="保存路径", width=10, anchor="w",
                 font=("Helvetica Neue", 12), fg=COLORS["text"],
                 bg=COLORS["card"]).pack(side="left")
        self.output_var = tk.StringVar(value="./data/")
        self.output_entry = tk.Entry(row3, textvariable=self.output_var,
                                     font=("Helvetica Neue", 12),
                                     bg=COLORS["input_bg"], fg=COLORS["text"],
                                     insertbackground=COLORS["text"],
                                     highlightbackground=COLORS["input_border"],
                                     highlightthickness=1, relief="flat", bd=0)
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.browse_btn = ACButton(row3, text="浏览...", color=COLORS["brown"],
                                   width=70, height=32, font_size=11,
                                   command=self._browse_output)
        self.browse_btn.pack(side="left", padx=(8, 0))

        # 飞书 Webhook
        row4 = tk.Frame(settings_card.inner, bg=COLORS["card"])
        row4.pack(fill="x", pady=3)
        tk.Label(row4, text="飞书通知", width=10, anchor="w",
                 font=("Helvetica Neue", 12), fg=COLORS["text"],
                 bg=COLORS["card"]).pack(side="left")
        self.webhook_var = tk.StringVar(value="")
        webhook_entry = tk.Entry(row4, textvariable=self.webhook_var,
                                 font=("Helvetica Neue", 12),
                                 bg=COLORS["input_bg"], fg=COLORS["text"],
                                 insertbackground=COLORS["text"],
                                 highlightbackground=COLORS["input_border"],
                                 highlightthickness=1, relief="flat", bd=0)
        webhook_entry.pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(row4, text="可选，启动/停止时飞书播报", font=("Helvetica Neue", 10),
                 fg=COLORS["text_light"], bg=COLORS["card"]).pack(side="left", padx=(8, 0))

        # ---- 按钮区 ----
        btn_frame = tk.Frame(self.root, bg=COLORS["bg"])
        btn_frame.pack(fill="x", padx=30, pady=(0, 10))

        self.start_btn = ACButton(btn_frame, text="开始监控", color=COLORS["primary"],
                                  width=130, height=40, font_size=13,
                                  command=self._start)
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ACButton(btn_frame, text="停止监控", color=COLORS["danger"],
                                 width=130, height=40, font_size=13,
                                 command=self._stop)
        self.stop_btn.pack(side="left")
        self.stop_btn.set_state(disabled=True)

        # ---- 日志卡片 ----
        log_card = ACFrame(self.root, padding=15)
        log_card.pack(fill="both", expand=True, padx=30, pady=(0, 5))

        tk.Label(log_card.inner, text="运行日志",
                 font=("Helvetica Neue", 13, "bold"), fg=COLORS["brown_dark"],
                 bg=COLORS["card"]).pack(anchor="w", pady=(0, 8))

        log_container = tk.Frame(log_card.inner, bg=COLORS["log_bg"],
                                 highlightbackground=COLORS["card_border"],
                                 highlightthickness=1, bd=0)
        log_container.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_container, font=("Helvetica Neue", 11),
                                bg=COLORS["log_bg"], fg=COLORS["log_text"],
                                wrap="word", relief="flat", bd=0,
                                padx=12, pady=10,
                                insertbackground=COLORS["text"])
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(log_container, orient="vertical",
                                 command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # 配置文本标签颜色
        self.log_text.tag_configure("change", foreground="#8BB382")
        self.log_text.tag_configure("offline", foreground="#E88B7E")
        self.log_text.tag_configure("time", foreground="#9C8B78")

        # ---- 状态栏 ----
        self.status_frame = tk.Frame(self.root, bg=COLORS["brown_dark"], height=32)
        self.status_frame.pack(fill="x", side="bottom")

        self.status_dot = tk.Canvas(self.status_frame, width=12, height=12,
                                    bg=COLORS["brown_dark"], highlightthickness=0)
        self.status_dot.pack(side="left", padx=(15, 6))
        self._draw_dot(COLORS["status_offline"])

        self.status_label = tk.Label(self.status_frame, text="就绪  |  点击「开始监控」启动",
                                     font=("Helvetica Neue", 10),
                                     fg="#FEF5E7", bg=COLORS["brown_dark"])
        self.status_label.pack(side="left")

        self.room_count_label = tk.Label(self.status_frame, text="",
                                         font=("Helvetica Neue", 10),
                                         fg=COLORS["brown_light"], bg=COLORS["brown_dark"])
        self.room_count_label.pack(side="right", padx=15)

    def _draw_dot(self, color):
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, 11, 11, fill=color, outline=color)

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择 CSV 保存目录")
        if path:
            self.output_var.set(path + "/")

    # ==================== 控制逻辑 ====================

    def _start(self):
        room_text = self.room_entry.get().strip()
        if not room_text:
            messagebox.showwarning("提示", "请输入直播间 ID", parent=self.root)
            return

        try:
            room_ids = [int(x.strip()) for x in room_text.replace(",", " ").split()]
        except ValueError:
            messagebox.showerror("错误", "直播间 ID 格式不正确，请输入数字", parent=self.root)
            return

        try:
            interval = int(self.interval_var.get())
            if interval < 10:
                messagebox.showwarning("提示", "轮询间隔不能小于 10 秒", parent=self.root)
                return
        except ValueError:
            messagebox.showerror("错误", "轮询间隔请输入数字", parent=self.root)
            return

        output = self.output_var.get().strip() or "./data/"

        self.running = True
        self._set_inputs_enabled(False)
        self.start_btn.set_state(disabled=True)
        self.stop_btn.set_state(disabled=False)
        self._draw_dot(COLORS["status_live"])
        self.status_label.config(text="监控运行中 ...")
        self.room_count_label.config(text=f"房间: {len(room_ids)}  |  间隔: {interval}s")

        self._log("=" * 50, "time")
        self._log(f"开始监控 {len(room_ids)} 个直播间: {room_ids}")
        self._log(f"轮询间隔: {interval}s  |  保存路径: {output}")
        self._log("=" * 50, "time")

        # 每个房间一个 Monitor，共用后台 asyncio 线程
        self.monitors = []
        multi = len(room_ids) > 1
        for rid in room_ids:
            path = resolve_output_path(output, rid, multi)
            monitor = LiveMonitor(rid, path, interval=interval)
            monitor.on_data = self._on_data
            monitor.on_change = self._on_change
            monitor.on_offline = self._on_offline
            monitor.on_offline_end = self._on_offline_end
            monitor.on_error = self._on_error
            monitor.on_relive = self._on_relive
            self.monitors.append(monitor)

        thread = threading.Thread(target=self._run_async_loop, daemon=True)
        thread.start()

        # 飞书启动播报
        self.started_at = datetime.now()
        self.notify_rooms = room_ids
        notify_start(self.webhook_var.get().strip(), room_ids, interval, output)

    def _stop(self):
        # 飞书停止播报
        if self.started_at and self.notify_rooms:
            # 收集各房间最终数据
            final_stats = {}
            for m in self.monitors:
                final_stats[m.room_id] = {
                    "watched": m._last_watched,
                    "likes": m._last_likes,
                }
            notify_stop(self.webhook_var.get().strip(), self.notify_rooms,
                       self.started_at, final_stats)

        self._log("正在停止监控...")
        for m in self.monitors:
            m.stop()
        self.monitors = []
        self.running = False
        self._set_inputs_enabled(True)
        self.start_btn.set_state(disabled=False)
        self.stop_btn.set_state(disabled=True)
        self._draw_dot(COLORS["status_offline"])
        self.status_label.config(text="已停止")
        self.room_count_label.config(text="")
        self._log("监控已停止")

    def _set_inputs_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.room_entry.configure(state=state)
        self.output_entry.configure(state=state)
        # interval entry
        for child in self.root.winfo_children():
            if isinstance(child, tk.Entry):
                try:
                    child.configure(state=state)
                except Exception:
                    pass

    def _run_async_loop(self):
        """后台线程：运行 asyncio 事件循环"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                asyncio.gather(*[m.run() for m in self.monitors])
            )
        except Exception as e:
            self.msg_queue.put(("error", f"监控异常: {e}"))
        finally:
            loop.close()
            self.msg_queue.put(("stopped", None))

    # ==================== 回调 ====================

    def _on_data(self, room_id, data):
        self.msg_queue.put(("data", (room_id, data)))

    def _on_change(self, room_id, field, old, new, delta):
        self.msg_queue.put(("change", (room_id, field, old, new, delta)))

    def _on_offline(self, room_id, seconds):
        self.msg_queue.put(("offline", (room_id, 0)))

    def _on_offline_end(self, room_id):
        self.msg_queue.put(("offline_end", room_id))

    def _on_error(self, room_id, error):
        self.msg_queue.put(("error", f"[房间{room_id}] 获取失败: {error}"))

    def _on_relive(self, room_id):
        self.msg_queue.put(("relive", room_id))

    # ==================== 消息处理 ====================

    def _poll_queue(self):
        """主线程定期检查消息队列，更新 UI"""
        try:
            while True:
                msg_type, payload = self.msg_queue.get_nowait()

                if msg_type == "data":
                    room_id, data = payload
                    self._log(
                        f"[{data['timestamp'][11:19]}] "
                        f"[房间{room_id}] "
                        f"人气: {data['popularity_text']} | "
                        f"看过: {data['watched_text']} (num={data['watched_num']}) | "
                        f"点赞: {data['likes']}"
                    )

                elif msg_type == "change":
                    room_id, field, old, new, delta = payload
                    sign = "+" if delta >= 0 else ""
                    label = "看过人数" if field == "watched" else "点赞数"
                    self._log(f"  >>> {label}变化: {old} -> {new} ({sign}{delta})", "change")

                elif msg_type == "offline":
                    room_id, _ = payload
                    self._log(f"[房间{room_id}] 已下播，等待 5 分钟内重新开播...", "offline")

                elif msg_type == "offline_end":
                    room_id = payload
                    self._log(f"[房间{room_id}] 下播超过 5 分钟，停止监控", "offline")

                elif msg_type == "relive":
                    room_id = payload
                    self._log(f"[房间{room_id}] 重新开播！")

                elif msg_type == "error":
                    self._log(payload, "offline")

                elif msg_type == "stopped":
                    self.root.after(0, self._on_all_stopped)

        except queue.Empty:
            pass

        self.root.after(200, self._poll_queue)

    def _on_all_stopped(self):
        """所有监控器都停止后自动更新 UI"""
        if self.running:
            self._stop()

    def _log(self, msg, tag=None):
        """追加日志到文本区域"""
        now = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{now}] {msg}\n", tag)
        self.log_text.see("end")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    LiveMonitorGUI().run()
