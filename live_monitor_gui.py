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

from live_monitor_core import LiveMonitor, resolve_output_path, get_room_brief, record_session, MAX_ROOMS
from live_monitor_notify import notify_start, notify_stop, notify_add_room, notify_remove_room

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.font_manager as fm
import numpy as np
import platform

# 跨平台 UI 字体
_SYS = platform.system()
if _SYS == "Windows":
    _FF = "Microsoft YaHei UI"
else:
    _FF = "Helvetica Neue"
UI_FONT = (_FF, 11)
UI_FONT_BOLD = (_FF, 11, "bold")
UI_FONT_TITLE = (_FF, 20, "bold")
UI_FONT_CARD = (_FF, 22, "bold")
UI_FONT_SMALL = (_FF, 10)
UI_FONT_LOG = (_FF, 12, "bold")
UI_FONT_HINT = (_FF, 14)
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
                         font=(UI_FONT[0], self.font_size, "bold"))

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
    CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".bili_live_monitor.json")

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("B站直播数据监控")
        self.root.geometry("820x700")
        self.root.minsize(720, 550)
        self.root.configure(bg=COLORS["bg"])

        self.running = False
        self.monitors = {}        # room_id -> LiveMonitor
        self._active_count = 0    # 仍在运行的 monitor 数量 (归零=全部下播)
        self.thread = None
        self.msg_queue = queue.Queue()
        self.started_at = None
        self.room_names = {}      # room_id -> anchor_name
        self.room_live_start = {} # room_id -> live_start_time
        self.room_ids = []        # ordered list of active room IDs
        self._mgmt_rows = {}      # room_id -> mgmt tab row widgets
        self._mgmt_rows_frame = None

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._load_config()
        # 字段变更自动保存（比依赖窗口关闭事件更可靠）
        self.room_entry.bind("<FocusOut>", lambda e: self._save_config())
        self.interval_entry.bind("<FocusOut>", lambda e: self._save_config())
        self.oe.bind("<FocusOut>", lambda e: self._save_config())
        self.operator_entry.bind("<FocusOut>", lambda e: self._save_config())
        self.webhook_entry.bind("<FocusOut>", lambda e: self._save_config())
        self._poll_queue()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        # ---- 标题 ----
        tf = tk.Frame(self.root, bg=COLORS["bg"])
        tf.pack(fill="x", padx=25, pady=(18, 6))
        tk.Label(tf, text="B站直播数据监控", font=UI_FONT_TITLE,
                 fg=COLORS["primary_dark"], bg=COLORS["bg"]).pack(anchor="w")
        tk.Label(tf, text="实时采集人气 / 看过人数 / 点赞数，流式写入 CSV · 上限 20 个房间",
                 font=UI_FONT_SMALL, fg=COLORS["text_light"],
                 bg=COLORS["bg"]).pack(anchor="w")

        # ---- 设置栏 (单行紧凑) ----
        sf = tk.Frame(self.root, bg=COLORS["card"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1, bd=0)
        sf.pack(fill="x", padx=25, pady=(8, 6))

        # 行1: 房间ID + 间隔 + 路径
        r1 = tk.Frame(sf, bg=COLORS["card"])
        r1.pack(fill="x", padx=15, pady=(12, 4))
        tk.Label(r1, text="房间ID", font=UI_FONT_BOLD,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.room_entry = tk.Entry(r1, width=28, font=UI_FONT,
                                   bg=COLORS["input_bg"], fg=COLORS["text"],
                                   highlightbackground=COLORS["input_border"],
                                   highlightthickness=1, relief="flat", bd=0)
        self.room_entry.pack(side="left", padx=(6, 14), ipady=3)
        self.room_entry.insert(0, "13308358")

        tk.Label(r1, text="间隔", font=UI_FONT_BOLD,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.interval_var = tk.StringVar(value="60")
        self.interval_entry = tk.Entry(r1, textvariable=self.interval_var, width=5,
                      font=UI_FONT, bg=COLORS["input_bg"], fg=COLORS["text"],
                      highlightbackground=COLORS["input_border"],
                      highlightthickness=1, relief="flat", bd=0)
        self.interval_entry.pack(side="left", padx=(6, 4), ipady=3)
        tk.Label(r1, text="秒", font=UI_FONT,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left", padx=(0, 14))

        tk.Label(r1, text="保存", font=UI_FONT_BOLD,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.output_var = tk.StringVar(value="./data/")
        self.oe = tk.Entry(r1, textvariable=self.output_var, width=18,
                           font=UI_FONT, bg=COLORS["input_bg"], fg=COLORS["text"],
                           highlightbackground=COLORS["input_border"],
                           highlightthickness=1, relief="flat", bd=0)
        self.oe.pack(side="left", padx=(6, 4), ipady=3)
        self.browse_btn = ACButton(r1, text="浏览", color=COLORS["brown"],
                                   width=60, height=30, font_size=11,
                                   command=self._browse_output)
        self.browse_btn.pack(side="left")

        # 行2: Webhook + 按钮
        r2 = tk.Frame(sf, bg=COLORS["card"])
        r2.pack(fill="x", padx=15, pady=(2, 12))
        tk.Label(r2, text="监测人", font=UI_FONT_BOLD,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.operator_var = tk.StringVar()
        self.operator_entry = tk.Entry(r2, textvariable=self.operator_var, width=8,
                      font=UI_FONT, bg=COLORS["input_bg"], fg=COLORS["text"],
                      highlightbackground=COLORS["input_border"],
                      highlightthickness=1, relief="flat", bd=0)
        self.operator_entry.pack(side="left", padx=(6, 14), ipady=3)

        tk.Label(r2, text="飞书通知", font=UI_FONT_BOLD,
                 fg=COLORS["text"], bg=COLORS["card"]).pack(side="left")
        self.webhook_var = tk.StringVar()
        self.webhook_entry = tk.Entry(r2, textvariable=self.webhook_var, width=40,
                      font=UI_FONT, bg=COLORS["input_bg"], fg=COLORS["text"],
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
                                width=120, height=36, font_size=11,
                                command=self._add_room)
        self.add_btn.pack(side="right", padx=(0, 6))

        # ---- Tab 图表区 ----
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=25, pady=(0, 6))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab",
                        font=UI_FONT_BOLD,
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
        tk.Label(lf, text="运行日志", font=UI_FONT_LOG,
                 fg=COLORS["brown_dark"], bg=COLORS["card"]).pack(anchor="w", padx=15, pady=(8, 4))

        lc = tk.Frame(lf, bg=COLORS["log_bg"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1, bd=0)
        lc.pack(fill="both", padx=15, pady=(0, 10))

        self.log_text = tk.Text(lc, font=UI_FONT_SMALL, height=6,
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
                                     font=UI_FONT_SMALL,
                                     fg="#FEF5E7", bg=COLORS["brown_dark"])
        self.status_label.pack(side="left")
        self.room_count_label = tk.Label(self.sf2, text="", font=UI_FONT_SMALL,
                                         fg=COLORS["brown_light"], bg=COLORS["brown_dark"])
        self.room_count_label.pack(side="right", padx=15)

    def _show_empty_tab(self):
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        self._mgmt_rows = {}
        self._mgmt_rows_frame = None
        f = tk.Frame(self.notebook, bg=COLORS["card"])
        self.notebook.add(f, text="无房间")
        tk.Label(f, text="输入直播间 ID，点击「开始监控」",
                 font=UI_FONT_HINT, fg=COLORS["text_light"],
                 bg=COLORS["card"]).pack(expand=True)

    def _create_mgmt_tab(self):
        """房间管理 Tab：集中展示所有房间状态，支持单独停止"""
        f = tk.Frame(self.notebook, bg=COLORS["card"])
        self.notebook.insert(0, f, text="房间管理")

        header = tk.Frame(f, bg=COLORS["card"])
        header.pack(fill="x", padx=10, pady=(10, 4))
        for text, w in [("房间", 18), ("状态", 8), ("看过", 8), ("点赞", 8), ("观众", 8)]:
            tk.Label(header, text=text, font=UI_FONT_BOLD, width=w, anchor="w",
                     fg=COLORS["brown_dark"], bg=COLORS["card"]).pack(side="left")

        outer = tk.Frame(f, bg=COLORS["card"])
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        mgmt_canvas = tk.Canvas(outer, bg=COLORS["card"], highlightthickness=0)
        mgmt_canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(outer, orient="vertical", command=mgmt_canvas.yview)
        sb.pack(side="right", fill="y")
        mgmt_canvas.configure(yscrollcommand=sb.set)

        rows_frame = tk.Frame(mgmt_canvas, bg=COLORS["card"])
        mgmt_canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        rows_frame.bind("<Configure>",
                         lambda e: mgmt_canvas.configure(scrollregion=mgmt_canvas.bbox("all")))

        self._mgmt_rows_frame = rows_frame
        self._mgmt_rows = {}

    def _add_mgmt_row(self, room_id: int):
        if self._mgmt_rows_frame is None:
            return
        name = self.room_names.get(room_id, f"房间{room_id}")
        row = tk.Frame(self._mgmt_rows_frame, bg=COLORS["card"])
        row.pack(fill="x", pady=2)

        name_label = tk.Label(row, text=f"{name}（{room_id}）", font=UI_FONT, width=18,
                              anchor="w", fg=COLORS["text"], bg=COLORS["card"])
        name_label.pack(side="left")
        status_label = tk.Label(row, text="直播中", font=UI_FONT, width=8, anchor="w",
                                fg=COLORS["primary_dark"], bg=COLORS["card"])
        status_label.pack(side="left")
        watched_label = tk.Label(row, text="-", font=UI_FONT, width=8, anchor="w",
                                 fg=COLORS["text"], bg=COLORS["card"])
        watched_label.pack(side="left")
        likes_label = tk.Label(row, text="-", font=UI_FONT, width=8, anchor="w",
                               fg=COLORS["text"], bg=COLORS["card"])
        likes_label.pack(side="left")
        audience_label = tk.Label(row, text="-", font=UI_FONT, width=8, anchor="w",
                                  fg=COLORS["text"], bg=COLORS["card"])
        audience_label.pack(side="left")
        stop_btn = ACButton(row, text="停止", color=COLORS["danger"], width=70, height=28,
                            font_size=10,
                            command=lambda rid=room_id: self._confirm_remove_room(rid))
        stop_btn.pack(side="left", padx=(6, 0))

        self._mgmt_rows[room_id] = {
            "frame": row, "name_label": name_label, "status_label": status_label,
            "watched_label": watched_label, "likes_label": likes_label,
            "audience_label": audience_label, "stop_btn": stop_btn,
        }

    def _remove_mgmt_row(self, room_id: int):
        row = self._mgmt_rows.pop(room_id, None)
        if row:
            row["frame"].destroy()

    def _confirm_remove_room(self, room_id: int):
        name = self.room_names.get(room_id, f"房间{room_id}")
        if len(self.room_ids) <= 1:
            hint = "\n（这是最后一个房间，停止后将结束本次监控）"
        else:
            hint = "\n（其他房间不受影响）"
        if messagebox.askyesno("确认停止", f"确定要停止监控 {name}（{room_id}）吗？{hint}",
                               parent=self.root):
            self._remove_room(room_id, reason="manual")

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
        tk.Label(c1, text="人气值", font=UI_FONT_SMALL,
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        pop_label = tk.Label(c1, text="-", font=UI_FONT_CARD,
                             fg=COLORS["primary_dark"], bg=COLORS["white"])
        pop_label.pack(pady=(0, 8))

        # 看过卡片
        c2 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c2.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(c2, text="看过人数", font=UI_FONT_SMALL,
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        watched_label = tk.Label(c2, text="-", font=UI_FONT_CARD,
                                 fg=COLORS["primary_dark"], bg=COLORS["white"])
        watched_label.pack(pady=(0, 8))

        # 点赞卡片
        c3 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c3.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(c3, text="点赞数", font=UI_FONT_SMALL,
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        likes_label = tk.Label(c3, text="-", font=UI_FONT_CARD,
                               fg=COLORS["primary_dark"], bg=COLORS["white"])
        likes_label.pack(pady=(0, 8))

        # 观众数卡片
        c4 = tk.Frame(cards, bg=COLORS["white"], highlightbackground=COLORS["card_border"],
                      highlightthickness=1)
        c4.pack(side="left", fill="x", expand=True)
        tk.Label(c4, text="实时观众", font=UI_FONT_SMALL,
                 fg=COLORS["text_light"], bg=COLORS["white"]).pack(pady=(8, 0))
        audience_label = tk.Label(c4, text="-", font=UI_FONT_CARD,
                                  fg=COLORS["accent"], bg=COLORS["white"])
        audience_label.pack(pady=(0, 8))

        # 开播时间
        live_start_label = tk.Label(f, text="", font=UI_FONT_SMALL,
                                    fg=COLORS["text_light"], bg=COLORS["card"])
        live_start_label.pack(anchor="w", padx=10, pady=(0, 6))

        # 图表
        fig = Figure(figsize=(7, 2.5), dpi=95, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        ax.set_facecolor(COLORS["card"])
        ax.spines['top'].set_visible(False)
        ax.tick_params(colors=COLORS["text_light"], labelsize=8)
        ax.set_ylabel("观看人数", color=COLORS["primary_dark"], fontsize=8)

        # 副坐标轴：观众数（右侧）
        ax2 = ax.twinx()
        ax2.yaxis.set_label_position("right")
        ax2.yaxis.tick_right()
        ax2.set_ylabel("观众数", color=COLORS["accent"], fontsize=8)
        ax2.tick_params(colors=COLORS["accent"], labelsize=8)
        ax2.spines['right'].set_color(COLORS["accent"])

        canvas_frame = tk.Frame(f, bg=COLORS["card"])
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        canvas = FigureCanvasTkAgg(fig, master=canvas_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # 存储组件引用
        tab_data = {
            "frame": f, "fig": fig, "ax": ax, "ax2": ax2, "canvas": canvas,
            "pop_label": pop_label, "watched_label": watched_label,
            "likes_label": likes_label,
            "audience_label": audience_label,
            "live_start_label": live_start_label,
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
        self._active_count = len(room_ids)

        # 创建管理 Tab + 所有房间 Tab
        self._create_mgmt_tab()
        for rid in room_ids:
            self._create_room_tab(rid)
            self._add_mgmt_row(rid)

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

        # 预取主播名和开播时间
        self.room_live_start = {}  # room_id -> live_start_time str
        for rid in room_ids:
            try:
                name, live_start = asyncio.run(get_room_brief(rid))
                if name:
                    self.room_names[rid] = name
                if live_start:
                    self.room_live_start[rid] = live_start
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
            m.on_write_blocked = self._on_write_blocked
            self.monitors[rid] = m

        self.thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.thread.start()

        # 飞书启动播报 (名字稍后回填)
        self._save_config()
        self.started_at = datetime.now()
        room_info = [(rid, self.room_names.get(rid, ''),
                      self.room_live_start.get(rid, '')) for rid in room_ids]
        notify_start(self.webhook_var.get().strip(), room_info, interval, output,
                    operator=self.operator_var.get().strip())

    def _stop(self, reason="manual"):
        if not self.running:
            return
        webhook = self.webhook_var.get().strip()
        stopped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        started_ts = self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else ""

        if self.started_at:
            room_info = [(rid, self.room_names.get(rid, ''),
                          self.room_live_start.get(rid, '')) for rid in self.room_ids]
            final_stats = {}
            for rid, m in self.monitors.items():
                final_stats[rid] = {"watched": m._last_watched, "likes": m._last_likes,
                                    "audience": m._max_audience}
                out_dir = os.path.dirname(m.output) or "."
                record_session(out_dir, rid, self.room_names.get(rid, ''),
                             started_ts, stopped_at, reason,
                             m._last_watched, m._max_likes,
                             self.room_live_start.get(rid, ''))
            try:
                notify_stop(webhook, room_info, self.started_at, final_stats,
                           operator=self.operator_var.get().strip())
            except Exception as e:
                self._log(f"停止播报发送失败: {e}", "offline")

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
        self._add_mgmt_row(new_id)

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
        m.on_write_blocked = self._on_write_blocked
        self.monitors[new_id] = m
        self._active_count += 1

        # 在新线程中启动
        t = threading.Thread(target=self._run_single_monitor, args=(m,), daemon=True)
        t.start()

        # 获取主播名和开播时间用于飞书通知
        try:
            aname, live_start = asyncio.run(get_room_brief(new_id))
            if aname:
                self.room_names[new_id] = aname
        except Exception:
            pass

        self._log(f"[+] 动态添加房间 {new_id}")
        self.room_count_label.config(text=f"房间: {len(self.room_ids)}  |  间隔: {interval}s")

        # 飞书通知
        notify_add_room(self.webhook_var.get().strip(), new_id,
                        self.room_names.get(new_id, ""),
                        self.room_live_start.get(new_id, ""),
                        operator=self.operator_var.get().strip())

    def _remove_room(self, room_id: int, reason="manual"):
        """停止单个房间的监控，不影响其他房间"""
        if room_id not in self.monitors:
            return

        # 只剩最后一个房间时，等价于全部停止
        if len(self.room_ids) <= 1:
            self._stop(reason=reason)
            return

        m = self.monitors[room_id]
        m.stop()

        stats = {"watched": m._last_watched, "likes": m._last_likes,
                 "audience": m._max_audience}
        if self.started_at:
            out_dir = os.path.dirname(m.output) or "."
            record_session(out_dir, room_id, self.room_names.get(room_id, ''),
                         self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         reason, m._last_watched, m._max_likes,
                         self.room_live_start.get(room_id, ''))

        # 从当前监控集合中移除；_active_count 由该 monitor 协程退出后的
        # monitor_done 消息统一递减，此处不重复扣减，避免双重计数
        del self.monitors[room_id]
        self.room_ids = [r for r in self.room_ids if r != room_id]

        # 移除该房间的图表 Tab
        td = self._tab_data.pop(room_id, None)
        if td:
            for tab_id in self.notebook.tabs():
                if self.notebook.nametowidget(tab_id) is td["frame"]:
                    self.notebook.forget(tab_id)
                    break

        self._remove_mgmt_row(room_id)

        name = self.room_names.get(room_id, str(room_id))
        self._log(f"[-] 已停止监控房间 {name}（{room_id}）", "offline")
        self.room_count_label.config(
            text=f"房间: {len(self.room_ids)}  |  间隔: {self.interval_var.get()}s")

        try:
            notify_remove_room(self.webhook_var.get().strip(), room_id, name,
                               reason=reason, stats=stats,
                               operator=self.operator_var.get().strip())
        except Exception as e:
            self._log(f"停止播报发送失败: {e}", "offline")

    def _run_async_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _wrap(m):
                try:
                    await m.run()
                except Exception as e:
                    self.msg_queue.put(("error", f"房间{m.room_id}异常: {e}"))
                finally:
                    self.msg_queue.put(("monitor_done", m.room_id))

            tasks = [loop.create_task(_wrap(m)) for m in self.monitors.values()]
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
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
            self.msg_queue.put(("monitor_done", monitor.room_id))

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

    def _on_write_blocked(self, room_id):
        self.msg_queue.put(("write_blocked", room_id))

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
                        f"观众: {data.get('audience_count', '-')} | "
                        f"点赞: {data['likes']}"
                    )
                    # 更新卡片
                    td = self._tab_data.get(room_id)
                    if td:
                        td["pop_label"].config(text=data.get("popularity_text", "-"))
                        td["watched_label"].config(text=data.get("watched_num", "-"))
                        td["likes_label"].config(text=data.get("likes", "-"))
                        td["audience_label"].config(text=data.get("audience_count", "-"))
                        # 更新开播时间
                        lst = data.get("live_start_time", "")
                        if lst:
                            try:
                                dt = datetime.strptime(lst, "%Y-%m-%d %H:%M:%S")
                                dur = int((datetime.now() - dt).total_seconds())
                                h, r = divmod(dur, 3600)
                                m, s = divmod(r, 60)
                                dur_str = f"{h}时{m}分{s}秒" if h else f"{m}分{s}秒"
                                td["live_start_label"].config(
                                    text=f"开播: {lst} | 已播: {dur_str}")
                            except Exception:
                                td["live_start_label"].config(text=f"开播: {lst}")
                        elif not data.get("is_live"):
                            td["live_start_label"].config(text="已下播")

                    # 更新房间管理 Tab
                    mrow = self._mgmt_rows.get(room_id)
                    if mrow:
                        mrow["watched_label"].config(text=data.get("watched_num", "-"))
                        mrow["likes_label"].config(text=data.get("likes", "-"))
                        mrow["audience_label"].config(text=data.get("audience_count", "-"))
                        mrow["status_label"].config(
                            text="直播中" if data.get("is_live") else "已下播",
                            fg=COLORS["primary_dark"] if data.get("is_live") else COLORS["danger"])

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
                        ax2 = td["ax2"]
                        ax.clear()
                        ax2.clear()
                        ax.set_facecolor(COLORS["card"])
                        ax.spines['top'].set_visible(False)
                        ax.spines['right'].set_visible(False)

                        # 观众数
                        audience = [p.get('audience_count', 0) or 0 for p in pts]

                        # 插值平滑
                        x_orig = np.arange(len(watched))
                        x_smooth = np.linspace(0, len(watched)-1, max(len(watched)*3, 20))
                        y_smooth = np.interp(x_smooth, x_orig, watched)
                        a_smooth = np.interp(x_smooth, x_orig, audience)

                        ax.plot(x_smooth, y_smooth, color=COLORS["primary"],
                                linewidth=2.5, solid_capstyle='round',
                                solid_joinstyle='round', label='看过人数')
                        ax.fill_between(x_smooth, y_smooth, alpha=0.08,
                                        color=COLORS["primary"])
                        ax.set_ylim(y_min, y_max)
                        ax.tick_params(colors=COLORS["text_light"], labelsize=8)
                        ax.set_ylabel("看过人数", color=COLORS["primary_dark"], fontsize=8)

                        # 副轴：观众数
                        if any(a > 0 for a in audience):
                            a_max = max(audience)
                            a_min = min(a for a in audience if a > 0) if any(a > 0 for a in audience) else 0
                            a_range = a_max - a_min
                            a_pad = max(a_range * 0.3, 5) if a_range > 0 else 10
                            ax2.set_ylim(max(0, a_min - a_pad), a_max + a_pad)

                        ax2.plot(x_smooth, a_smooth, color=COLORS["accent"],
                                 linewidth=2, linestyle='--', label='观众数')
                        ax2.set_ylabel("观众数", color=COLORS["accent"], fontsize=8)
                        ax2.yaxis.set_label_position("right")
                        ax2.yaxis.tick_right()
                        ax2.tick_params(colors=COLORS["accent"], labelsize=8)

                        # x 轴
                        step = max(1, len(times) // 5)
                        ax.set_xticks(range(0, len(times), step))
                        ax.set_xticklabels([times[i] for i in range(0, len(times), step)],
                                           fontsize=7)

                        # 图例
                        lines1, labels1 = ax.get_legend_handles_labels()
                        lines2, labels2 = ax2.get_legend_handles_labels()
                        ax.legend(lines1 + lines2, labels1 + labels2,
                                  loc='upper left', fontsize=7,
                                  facecolor=COLORS["card"], edgecolor=COLORS["card_border"])

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
                    self.root.after(0, lambda rid=room_id: self._remove_room(rid, reason="offline"))

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
                    mrow = self._mgmt_rows.get(room_id)
                    if mrow:
                        mrow["name_label"].config(text=f"{name}（{room_id}）")
                    self._log(f"[房间{room_id}] 主播: {name}")

                elif msg_type == "write_blocked":
                    room_id = payload
                    self._log(f"[房间{room_id}] CSV文件被占用，写入暂停", "offline")
                    # 飞书提醒一次
                    webhook = self.webhook_var.get().strip()
                    name = self.room_names.get(room_id, str(room_id))
                    from live_monitor_notify import _send_card
                    try:
                        _send_card(webhook,
                            header_text="⚠️ CSV 写入异常",
                            header_color="red",
                            content_lines=[
                                f"**房间：** {name}（{room_id}）",
                                f"**原因：** CSV 文件被其他程序占用，数据暂时无法写入",
                                f"**建议：** 关闭 Excel 或其他打开该文件的程序后自动恢复",
                            ])
                    except Exception:
                        pass

                elif msg_type == "monitor_done":
                    self._active_count -= 1
                    if self._active_count <= 0 and self.running:
                        self.root.after(0, lambda: self._stop(reason="offline"))

                elif msg_type == "stopped":
                    # async loop 线程已退出；如果还有活跃 monitor 则等它们
                    if self._active_count <= 0 and self.running:
                        self.root.after(0, self._on_all_stopped)

        except queue.Empty:
            pass

        self.root.after(300, self._poll_queue)

    def _load_config(self):
        try:
            with open(self.CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            self.room_entry.delete(0, "end")
            self.room_entry.insert(0, cfg.get("rooms", "13308358"))
            self.output_var.set(cfg.get("output", "./data/"))
            self.webhook_var.set(cfg.get("webhook", ""))
            self.operator_var.set(cfg.get("operator", ""))
            self.interval_var.set(str(cfg.get("interval", 60)))
        except Exception:
            pass

    def _save_config(self):
        try:
            cfg = {
                "rooms": self.room_entry.get().strip(),
                "output": self.output_var.get().strip(),
                "webhook": self.webhook_var.get().strip(),
                "operator": self.operator_var.get().strip(),
                "interval": self.interval_var.get().strip(),
            }
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        if self.running:
            self._stop(reason="manual")
        self.root.destroy()

    def _on_all_stopped(self):
        if self.running:
            self._stop(reason="offline")

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
        self.operator_entry.configure(state=state)
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
