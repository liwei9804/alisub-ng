#!/bin/bash
# alisub-ng 守护脚本 - 检测进程挂掉后自动重启并记录原因
# 用法: nohup ./watchdog.sh &

APP_DIR="/vol1/@apphome/trim.openclaw/data/workspace/alisub-ng"
LOG="$APP_DIR/logs/watchdog.log"
CHECK_INTERVAL=30  # 每30秒检查一次

cd "$APP_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
}

log_crash_detail() {
    log "--- 崩溃详情 ---"
    # 系统内存状态
    log "内存: $(free -h | awk '/Mem:/{print "总="$2, "已用="$3, "可用="$7}')"
    # 系统负载
    log "负载: $(cat /proc/loadavg)"
    # 检查 OOM killer 记录（最近1分钟内）
    local oom=$(dmesg 2>/dev/null | grep -i "oom\|killed process" | tail -3)
    if [ -n "$oom" ]; then
        log "OOM记录: $oom"
    fi
    # 最后的应用日志
    local last_log=$(tail -3 "$APP_DIR/logs/app.log" 2>/dev/null)
    if [ -n "$last_log" ]; then
        log "最后日志: $last_log"
    fi
    log "---"
}

start_app() {
    export PORT=8003
    export PYTHONUNBUFFERED=1
    nohup python3 app.py >> logs/run.log 2>&1 &
    APP_PID=$!
    log "✅ alisub-ng 已启动 (PID=$APP_PID)"
}

# 先杀掉可能残留的旧进程
pkill -f "python3 app.py" 2>/dev/null
sleep 2

log "===== 守护脚本启动 ====="
start_app

while true; do
    sleep $CHECK_INTERVAL

    # 检查进程是否还活着
    if ! pgrep -f "python3 app.py" > /dev/null; then
        # 获取退出码
        wait $APP_PID 2>/dev/null
        EXIT_CODE=$?
        log "⚠️ 检测到 alisub-ng 进程已退出 (退出码=$EXIT_CODE)"
        log_crash_detail
        start_app
    fi
done
