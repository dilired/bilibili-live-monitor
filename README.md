# B站直播数据监控

B站直播间数据监控工具，定时采集人气值、看过人数、点赞数，流式写入 CSV。

## 快速开始

### 直接下载（推荐）

到 [Releases](https://github.com/dilired/bilibili-live-monitor/releases) 下载对应平台版本：
- **Mac**：`BiliLiveMonitor-mac.zip` → 解压后双击运行
- **Windows**：`BiliLiveMonitor.exe` → 双击运行

打开后输入直播间 ID，点击「开始监控」即可。

### 命令行使用

```bash
git clone https://github.com/dilired/bilibili-live-monitor.git
cd bilibili-live-monitor
pip install bilibili-api-python

# 监控单个房间
python kubo_test_watch.py -r 13308358

# 监控多个房间
python kubo_test_watch.py -r 26044264 13308358 -i 60 -o ./data/
```

### 后台运行

```bash
nohup python kubo_test_watch.py -r 13308358 -o ./data/ > monitor.log 2>&1 &
tail -f monitor.log  # 查看日志
```

## CSV 输出

| timestamp | room_id | popularity | popularity_text | watched_num | watched_text | likes |
|-----------|---------|------------|-----------------|-------------|-------------|-------|
| 2026-05-26 16:30:00 | 13308358 | 165000 | 16.5万人气 | 33437 | 3.3万人看过 | 28998 |

用 Excel / Pandas 打开即可绘制增长曲线。

## 自己打包

```bash
# Mac
bash build_mac.sh

# Windows
build_windows.bat
```
