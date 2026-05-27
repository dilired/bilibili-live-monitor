"""B站直播间数据监控 - 图形界面
Tkinter + matplotlib 实现，Mac / Windows 通用
"""

import asyncio
import json
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

# PyInstaller 打包后需在导入 bilibili_api 之前设置 SSL 证书路径
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

from bilibili_api import Credential

from live_monitor_core import LiveMonitor, resolve_output_path, get_anchor_name, MAX_ROOMS
from live_monitor_notify import notify_start, notify_stop

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.font_manager as fm

# 配置中文字体（Mac: PingFang / Windows: Microsoft YaHei）
_CJK_FONTS = ['PingFang HK', 'Heiti TC', 'STHeiti', 'Microsoft YaHei', 'SimHei']
_available = [f.name for f in fm.fontManager.ttflist]
_found = next((f for f in _CJK_FONTS if f in _available), None)
if _found:
    plt.rcParams['font.sans-serif'] = [_found]
    plt.rcParams['axes.unicode_minus'] = False

# ============================================================
#  暖色主题配色
# ============================================================
COLORS = {
    "bg":              "#FEF5E7",
    "card":            "#FFFAF2",
    "card_border":     "#E8D5B8",
    "primary":         "#8BB382",
    "primary_dark":    "#6B8E64",
    "primary_light":   "#C5DFC0",
    "brown":           "#9C8B78",
    "brown_dark":      "#6B5C4A",
    "brown_light":     "#C4B5A5",
    "accent":          "#F4C87A",
    "danger":          "#E88B7E",
    "text":            "#6B5C4A",
    "text_light":      "#9C8B78",
    "log_bg":          "#FDF5EC",
    "log_text":        "#5C4B3A",
    "input_bg":        "#FFFFFF",
    "input_border":    "#D4C4B0",
    "status_live":     "#8BC48B",
    "status_offline":  "#E8D5B8",
    "status_error":    "#E88B7E",
    "white":           "#FFFFFF",
}


class ACButton(tk.Canvas):
    """暖色圆角按钮"""
    def __init__(self, parent, text, command=None, color=COLORS["primary"],
                 width=130, height=36, font_size=12, **kwargs):
        super().__init__(parent, width=width, height=height,
                         bg=COLORS["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.color = color
        self.color_hover = self._darken(color)
        self.width = width
        self.height = height
        self.radius = 9
        self.current_color = color
        self.text = text
        self.font_size = font_size
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self):
        self.delete("all")
        r, w, h, c = self.radius, self.width, self.height, self.current_color
        self.create_arc(0, 0, r*2, r*2, start=90, extent=90, fill=c, outline=c)
        self.create_arc(w-r*2, 0, w, r*2, start=0, extent=90, fill=c, outline=c)
        self.create_arc(w-r*2, h-r*2, w, h, start=270, extent=90, fill=c, outline=c)
        self.create_arc(0, h-r*2, r*2, h, start=180, extent=90, fill=c, outline=c)
        self.create_rectangle(r, 0, w-r, h, fill=c, outline=c)
        self.create_rectangle(0, r, w, h-r, fill=c, outline=c)
        self.create_text(w//2, h//2, text=self.text, fill="white",
                         font=("Helvetica Neue", self.font_size, "bold"))

    def _darken(self, hx):
        r, g, b = int(hx[1:3],16), int(hx[3:5],16), int(hx[5:7],16)
        return f"#{max(0,r-20):02x}{max(0,g-20):02x}{max(0,b-20):02x}"

    def _on_enter(self, e): self.current_color = self.color_hover; self._draw()
    def _on_leave(self, e): self.current_color = self.color; self._draw()
    def _on_click(self, e):
        if self.command: self.command()

    def set_state(self, disabled=False):
        if disabled:
            self.current_color = COLORS["brown_light"]
            self.unbind("<Enter>"); self.unbind("<Leave>"); self.unbind("<Button-1>")
        else:
            self.current_color = self.color
            self.bind("<Enter>", self._on_enter); self.bind("<Leave>", self._on_leave)
            self.bind("<Button-1>", self._on_click)
        self._draw()


class LiveMonitorGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("B站直播数据监控")
        self.root.geometry("820x700")
        self.root.minsize(720, 550)
        self.root.configure(bg=COLORS["bg"])

        self.running = False
        self.monitors = {}        # room_id -> LiveMonitor
        self.thread = None
        self.msg_queue = queue.Queue()
        self.started_at = None
        self.room_names = {}      # room_id -> anchor_name
        self.room_ids = []        # ordered list of active room IDs

        self._build_ui()
        self._poll_queue()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        # ---- 标题 ----
        tf = tk.Frame(self.root, bg=COLORS["bg"])
        tf.pack(fill="x", padx=25, pady=(18, 6))
        tk.Label(tf, text="B站直播数据监控", font=("Helvetica Neue", 20, "bold"),
                 fg=COLORS["primary_dark"], bg=COLORS["bg"]).pack(anchor="w")
        tk.Label(tf, text="实时采集人气 / 看过人数 / 点赞数，流式写入 CSV · 上限 10 个房间",
                 font=("Helvetica Neue", 10), fg=COLORS["text_light"],
                 bg=COLORS["bg"]).pack(anchor="w")

        # ---- 设置栏 (单行紧凑) ----
        sf = tk.Frame(self.root, bg=COLORS["card"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1, bd=0)
        sf.pack(fill="x", padx=25, pady=(8, 6))

        # 行1: 房间ID + 间隔 + 路径
        r1 = tk.Frame(sf, bg=COLORS["card"])
        r1.pack(fill="x", padx=15, pady=(12, 4))
        tk.Label(r1, text="房间ID", font=("Helvetica Neue", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.room_entry = tk.Entry(r1, width=28, font=("Helvetica Neue", 11),
                                   bg=COLORS["input_bg"], fg=COLORS["text"],
                                   highlightbackground=COLORS["input_border"],
                                   highlightthickness=1, relief="flat", bd=0)
        self.room_entry.pack(side="left", padx=(6, 14), ipady=3)
        self.room_entry.insert(0, "13308358")

        tk.Label(r1, text="间隔", font=("Helvetica Neue", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.interval_var = tk.StringVar(value="60")
        self.interval_entry = tk.Entry(r1, textvariable=self.interval_var, width=5,
                      font=("Helvetica Neue", 11), bg=COLORS["input_bg"], fg=COLORS["text"],
                      highlightbackground=COLORS["input_border"],
                      highlightthickness=1, relief="flat", bd=0)
        self.interval_entry.pack(side="left", padx=(6, 4), ipady=3)
        tk.Label(r1, text="秒", font=("Helvetica Neue", 11),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left", padx=(0, 14))

        tk.Label(r1, text="保存", font=("Helvetica Neue", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.output_var = tk.StringVar(value="./data/")
        self.oe = tk.Entry(r1, textvariable=self.output_var, width=18,
                           font=("Helvetica Neue", 11), bg=COLORS["input_bg"], fg=COLORS["text"],
                           highlightbackground=COLORS["input_border"],
                           highlightthickness=1, relief="flat", bd=0)
        self.oe.pack(side="left", padx=(6, 4), ipady=3)
        self.browse_btn = ACButton(r1, text="浏览", color=COLORS["brown"],
                                   width=55, height=28, font_size=10,
                                   command=self._browse_output)
        self.browse_btn.pack(side="left")

        # 行2: Webhook + 按钮
        r2 = tk.Frame(sf, bg=COLORS["card"])
        r2.pack(fill="x", padx=15, pady=(2, 12))
        tk.Label(r2, text="飞书通知", font=("Helvetica Neue", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.webhook_var = tk.StringVar()
        self.webhook_entry = tk.Entry(r2, textvariable=self.webhook_var, width=40,
                      font=("Helvetica Neue", 11), bg=COLORS["input_bg"], fg=COLORS["text"],
                      highlightbackground=COLORS["input_border"],
                      highlightthickness=1, relief="flat", bd=0)
        self.webhook_entry.pack(side="left", padx=(6, 0), ipady=3, fill="x", expand=True)

        self.start_btn = ACButton(r2, text="开始监控", color=COLORS["primary"],
                                  command=self._start)
        self.start_btn.pack(side="right", padx=(0, 6))
        self.stop_btn = ACButton(r2, text="停止", color=COLORS["danger"],
                                 command=self._stop)
        self.stop_btn.pack(side="right", padx=(0, 6))
        self.stop_btn.set_state(disabled=True)
        self.add_btn = ACButton(r2, text="+ 添加房间", color=COLORS["brown"],
                                width=100, command=self._add_room)
        self.add_btn.pack(side="right", padx=(0, 6))

        # ---- Tab 图表区 ----
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=25, pady=(0, 6))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab",
                        font=("Helvetica Neue", 11, "bold"),
                        padding=[22, 8],
                        background=COLORS["bg"],
                        foreground=COLORS["text_light"],
                        borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["card"])],
                  foreground=[("selected", COLORS["primary_dark"])],
                  expand=[("selected", [2, 2, 2, 0])])

        # 默认空白页
        self._show_empty_tab()

        # ---- 日志 ----
        lf = tk.Frame(self.root, bg=COLORS["card"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1, bd=0)
        lf.pack(fill="x", padx=25, pady=(0, 5))
        tk.Label(lf, text="运行日志", font=("Helvetica Neue", 12, "bold"),
                 fg=COLORS["brown_dark"], bg=COLORS["card"]).pack(anchor="w", padx=15, pady=(8, 4))

        lc = tk.Frame(lf, bg=COLORS["log_bg"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1, bd=0)
        lc.pack(fill="both", padx=15, pady=(0, 10))

        self.log_text = tk.Text(lc, font=("Helvetica Neue", 10), height=6,
                                bg=COLORS["log_bg"], fg=COLORS["log_text"],
                                wrap="word", relief="flat", bd=0, padx=10, pady=6,
                                state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lc, orient="vertical", command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.tag_configure("change", foreground="#8BB382")
        self.log_text.tag_configure("offline", foreground="#E88B7E")
        self.log_text.tag_configure("time", foreground="#9C8B78")

        # ---- 状态栏 ----
        self.sf2 = tk.Frame(self.root, bg=COLORS["brown_dark"], height=28)
        self.sf2.pack(fill="x", side="bottom")
        self.status_dot = tk.Canvas(self.sf2, width=10, height=10,
                                    bg=COLORS["brown_dark"], highlightthickness=0)
        self.status_dot.pack(side="left", padx=(15, 6))
        self.status_dot.create_oval(1, 1, 9, 9, fill=COLORS["status_offline"], outline="")
        self.status_label = tk.Label(self.sf2, text="就绪",
                                     font=("Helvetica Neue", 10),
                                     fg="#FEF5E7", bg=COLORS["brown_dark"])
        self.status_label.pack(side="left")
        self.room_count_label = tk.Label(self.sf2, text="", font=("Helvetica Neue", 10),
                                         fg=COLORS["brown_light"], bg=COLORS["brown_dark"])
        self.room_count_label.pack(side="right", padx=15)

    def _show_empty_tab(self):
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        f = tk.Frame(self.notebook, bg=COLORS["card"])
        self.notebook.add(f, text="无房间")
        tk.Label(f, text="输入直播间 ID，点击「开始监控」",
                 font=("Helvetica Neue", 14), fg=COLORS["text_light"],
                 bg=COLORS["card"]).pack(expand=True)

    def _create_room_tab(self, room_id: int):
        """为一个房间创建图表 Tab"""
        # 移除占位页
        for tab_id in self.notebook.tabs():
            tab_text = self.notebook.tab(tab_id, "text")
            if tab_text == "无房间":
                self.notebook.forget(tab_id)

        name = self.room_names.get(room_id, f"房间{room_id}")
        f = tk.Frame(self.notebook, bg=COLORS["card"])
        self.notebook.add(f, text=f"{name}")

        # 指标卡片
        cards = tk.Frame(f, bg=COLORS["card"])
        cards.pack(fill="x", padx=10, pady=(10, 4))

        # 人气卡片
        c1 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c1.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(c1, text="人气值", font=("Helvetica Neue", 10),
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        pop_label = tk.Label(c1, text="-", font=("Helvetica Neue", 22, "bold"),
                             fg=COLORS["primary_dark"], bg=COLORS["white"])
        pop_label.pack(pady=(0, 8))

        # 看过卡片
        c2 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c2.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(c2, text="看过人数", font=("Helvetica Neue", 10),
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        watched_label = tk.Label(c2, text="-", font=("Helvetica Neue", 22, "bold"),
                                 fg=COLORS["primary_dark"], bg=COLORS["white"])
        watched_label.pack(pady=(0, 8))

        # 点赞卡片
        c3 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c3.pack(side="left", fill="x", expand=True)
        tk.Label(c3, text="点赞数", font=("Helvetica Neue", 10),
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        likes_label = tk.Label(c3, text="-", font=("Helvetica Neue", 22, "bold"),
                               fg=COLORS["primary_dark"], bg=COLORS["white"])
        likes_label.pack(pady=(0, 8))

        # 图表
        fig = Figure(figsize=(7, 2.5), dpi=95, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(COLORS["card"])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.tick_params(colors=COLORS["text_light"], labelsize=8)
        ax.set_xlabel("时间", color=COLORS["text_light"], fontsize=8)
        ax.set_ylabel("观看人数", color=COLORS["text_light"], fontsize=8)

        canvas_frame = tk.Frame(f, bg=COLORS["card"])
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        canvas = FigureCanvasTkAgg(fig, master=canvas_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # 存储组件引用
        tab_data = {
            "frame": f, "fig": fig, "ax": ax, "canvas": canvas,
            "pop_label": pop_label, "watched_label": watched_label,
            "likes_label": likes_label,
        }
        self._tab_data[room_id] = tab_data

    # ==================== 控制逻辑 ====================

    def _parse_room_ids(self, text):
        try:
            return [int(x.strip()) for x in text.replace(",", " ").split() if x.strip()]
        except ValueError:
            return None

    def _validate(self, room_ids=None):
        if room_ids is None:
            room_ids = self._parse_room_ids(self.room_entry.get().strip())
            if room_ids is None:
                messagebox.showerror("错误", "直播间 ID 格式不正确", parent=self.root)
                return None, None, None

        total = len(self.room_ids) + len([r for r in room_ids if r not in self.room_ids])
        if total > MAX_ROOMS:
            messagebox.showwarning("提示", f"最多同时监控 {MAX_ROOMS} 个房间", parent=self.root)
            return None, None, None

        try:
            interval = int(self.interval_var.get())
            if interval < 10:
                messagebox.showwarning("提示", "间隔不能小于 10 秒", parent=self.root)
                return None, None, None
        except ValueError:
            messagebox.showerror("错误", "轮询间隔请输入数字", parent=self.root)
            return None, None, None

        output = self.output_var.get().strip() or "./data/"
        return room_ids, interval, output

    def _start(self):
        room_ids, interval, output = self._validate()
        if room_ids is None:
            return

        # 清理旧 Tab
        self._show_empty_tab()

        self.room_ids = room_ids
        self.room_names = {}
        self.monitors = {}
        self._tab_data = {}

        # 创建所有 Tab
        for rid in room_ids:
            self._create_room_tab(rid)

        self.running = True
        self._set_inputs_enabled(False)
        self.start_btn.set_state(disabled=True)
        self.stop_btn.set_state(disabled=False)
        self.status_label.config(text="监控运行中 ...")
        self.room_count_label.config(text=f"房间: {len(room_ids)}  |  间隔: {interval}s")

        self._log("=" * 40, "time")
        self._log(f"开始监控 {len(room_ids)} 个直播间: {room_ids}")
        self._log(f"间隔: {interval}s  |  保存: {output}")
        self._log("=" * 40, "time")

        # 预取主播名（用于飞书播报和 Tab 标题）
        for rid in room_ids:
            try:
                name = asyncio.run(get_anchor_name(rid))
                if name:
                    self.room_names[rid] = name
            except Exception:
                pass

        multi = len(room_ids) > 1
        for rid in room_ids:
            path = resolve_output_path(output, rid, multi)
            m = LiveMonitor(rid, path, interval=interval)
            m.on_data = self._on_data
            m.on_change = self._on_change
            m.on_offline = self._on_offline
            m.on_offline_end = self._on_offline_end
            m.on_error = self._on_error
            m.on_relive = self._on_relive
            m.on_name = self._on_name
            self.monitors[rid] = m

        self.thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.thread.start()

        # 飞书启动播报 (名字稍后回填)
        self.started_at = datetime.now()
        room_info = [(rid, self.room_names.get(rid, '')) for rid in room_ids]
        notify_start(self.webhook_var.get().strip(), room_info, interval, output)

    def _stop(self):
        webhook = self.webhook_var.get().strip()
        if self.started_at:
            room_info = [(rid, self.room_names.get(rid, '')) for rid in self.room_ids]
            final_stats = {}
            for rid, m in self.monitors.items():
                final_stats[rid] = {"watched": m._last_watched, "likes": m._last_likes}
            notify_stop(webhook, room_info, self.started_at, final_stats)

        self._log("正在停止监控...")
        for m in self.monitors.values():
            m.stop()
        self.monitors = {}
        self.running = False
        self.room_ids = []
        self.started_at = None
        self._tab_data = {}
        self._show_empty_tab()
        self._set_inputs_enabled(True)
        self.start_btn.set_state(disabled=False)
        self.stop_btn.set_state(disabled=True)
        self.status_label.config(text="已停止")
        self.room_count_label.config(text="")
        self._log("监控已停止")

    def _add_room(self):
        if not self.running:
            messagebox.showinfo("提示", "请先开始监控", parent=self.root)
            return

        new_id = simpledialog.askinteger("添加房间", "输入直播间 ID:", parent=self.root)
        if new_id is None:
            return

        if new_id in self.monitors:
            messagebox.showwarning("提示", f"房间 {new_id} 已在监控中", parent=self.root)
            return

        if len(self.monitors) >= MAX_ROOMS:
            messagebox.showwarning("提示", f"最多同时监控 {MAX_ROOMS} 个房间", parent=self.root)
            return

        self.room_ids.append(new_id)
        self._create_room_tab(new_id)

        output = self.output_var.get().strip() or "./data/"
        interval = int(self.interval_var.get())
        path = resolve_output_path(output, new_id, len(self.room_ids) > 1)
        m = LiveMonitor(new_id, path, interval=interval)
        m.on_data = self._on_data
        m.on_change = self._on_change
        m.on_offline = self._on_offline
        m.on_offline_end = self._on_offline_end
        m.on_error = self._on_error
        m.on_relive = self._on_relive
        m.on_name = self._on_name
        self.monitors[new_id] = m

        # 在新线程中启动 (join 到现有 gather)
        t = threading.Thread(target=self._run_single_monitor, args=(m,), daemon=True)
        t.start()

        self._log(f"[+] 动态添加房间 {new_id}")
        self.room_count_label.config(text=f"房间: {len(self.room_ids)}  |  间隔: {interval}s")

    def _run_async_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                asyncio.gather(*[m.run() for m in self.monitors.values()])
            )
        except Exception as e:
            self.msg_queue.put(("error", f"监控异常: {e}"))
        finally:
            loop.close()
            self.msg_queue.put(("stopped", None))

    def _run_single_monitor(self, monitor):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(monitor.run())
        except Exception as e:
            self.msg_queue.put(("error", f"房间{monitor.room_id}异常: {e}"))
        finally:
            loop.close()

    # ==================== 回调 ====================

    def _on_data(self, room_id, data):
        self.msg_queue.put(("data", (room_id, data)))
        # 更新图表 (带限流，用单独的队列消息)
        self.msg_queue.put(("chart_update", room_id))

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

    def _on_name(self, room_id, name):
        self.msg_queue.put(("name", (room_id, name)))

    # ==================== 消息处理 ====================

    def _poll_queue(self):
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
                    # 更新卡片
                    td = self._tab_data.get(room_id)
                    if td:
                        td["pop_label"].config(text=data.get("popularity_text", "-"))
                        td["watched_label"].config(text=data.get("watched_num", "-"))
                        td["likes_label"].config(text=data.get("likes", "-"))

                elif msg_type == "chart_update":
                    room_id = payload
                    td = self._tab_data.get(room_id)
                    if td and room_id in self.monitors:
                        m = self.monitors[room_id]
                        if not m.buffer:
                            continue
                        pts = m.buffer[-200:]
                        times = [p['timestamp'][11:19] for p in pts]
                        watched = [p['watched_num'] or 0 for p in pts]

                        # 动态 Y 轴：小变化也能明显看出趋势
                        w_min, w_max = min(watched), max(watched)
                        w_range = w_max - w_min
                        if w_range == 0:
                            w_range = max(1, w_max * 0.001)
                        padding = max(w_range * 0.3, 5)
                        y_min = max(0, w_min - padding)
                        y_max = w_max + padding

                        ax = td["ax"]
                        ax.clear()
                        ax.set_facecolor(COLORS["card"])
                        ax.spines['top'].set_visible(False)
                        ax.spines['right'].set_visible(False)
                        ax.plot(times, watched, color=COLORS["primary"], linewidth=2)
                        ax.fill_between(range(len(times)), watched, alpha=0.1,
                                        color=COLORS["primary"])
                        ax.set_ylim(y_min, y_max)
                        ax.tick_params(colors=COLORS["text_light"], labelsize=8)
                        step = max(1, len(times) // 5)
                        ax.set_xticks(range(0, len(times), step))
                        ax.set_xticklabels([times[i] for i in range(0, len(times), step)],
                                           fontsize=7)
                        ax.set_ylabel("观看人数", color=COLORS["text_light"], fontsize=8)
                        td["fig"].tight_layout()
                        td["canvas"].draw()

                elif msg_type == "change":
                    room_id, field, old, new, delta = payload
                    sign = "+" if delta >= 0 else ""
                    label = "看过人数" if field == "watched" else "点赞数"
                    self._log(f"  >>> {label}变化: {old} -> {new} ({sign}{delta})", "change")

                elif msg_type == "offline":
                    room_id, _ = payload
                    self._log(f"[房间{room_id}] 已下播，等待 5 分钟重新开播...", "offline")

                elif msg_type == "offline_end":
                    room_id = payload
                    self._log(f"[房间{room_id}] 下播超过 5 分钟，停止监控", "offline")

                elif msg_type == "relive":
                    room_id = payload
                    self._log(f"[房间{room_id}] 重新开播！")

                elif msg_type == "error":
                    self._log(payload, "offline")

                elif msg_type == "name":
                    room_id, name = payload
                    self.room_names[room_id] = name
                    # 更新 Tab 标题
                    for tab_id in self.notebook.tabs():
                        if self.notebook.tab(tab_id, "text") == f"房间{room_id}":
                            self.notebook.tab(tab_id, text=f"{name}")
                    self._log(f"[房间{room_id}] 主播: {name}")

                elif msg_type == "stopped":
                    self.root.after(0, self._on_all_stopped)

        except queue.Empty:
            pass

        self.root.after(300, self._poll_queue)

    def _on_all_stopped(self):
        if self.running:
            self._stop()

    # ==================== 辅助方法 ====================

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择 CSV 保存目录")
        if path:
            self.output_var.set(path + "/")

    def _set_inputs_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.room_entry.configure(state=state)
        self.oe.configure(state=state)
        self.interval_entry.configure(state=state)
        self.webhook_entry.configure(state=state)

    def _log(self, msg, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n", tag)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    LiveMonitorGUI().run()
