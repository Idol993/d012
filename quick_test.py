# -*- coding: utf-8 -*-
"""快速验证：后台 daemon 真的能自动调度检查吗？"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from gray_release import GrayReleaseEngine
from monitor_rollback import monitor_daemon

# 先停掉旧的 daemon
if monitor_daemon.is_running():
    monitor_daemon.stop()
    time.sleep(1)

# 创建一个发布并加入队列（5秒间隔）
release_no = f"QUICK-{int(time.time())}"
release_id = db.create_release(
    release_no=release_no, version='v1.0.0', risk_level='normal',
    title='快速测试', description='', changelog='',
    submitter='test', rollback_version='v0.9.0'
)
gray = GrayReleaseEngine()
gray.execute_full_gray_release(release_id, release_no, 'v1.0.0')
db.add_active_monitor(release_id, release_no, added_by='test', check_interval_seconds=5)

print(f"创建发布: {release_no} (release_id={release_id})")
print("加入监控队列，间隔 5 秒")
print()

# 启动 daemon
print("启动后台 daemon...")
result = monitor_daemon.start()
print(f"  结果: {result['message']}")
print(f"  PID: {result.get('pid')}")
print(f"  is_running: {monitor_daemon.is_running()}")
print()

# 等待并观察
print("等待自动调度（每 5 秒一次，观察 18 秒）:")
for i in range(4):
    time.sleep(5)
    am = db.get_active_monitor(release_id)
    cc = am.get('check_count', 0) if am else 0
    last = am.get('last_check_at', '-') if am else '-'
    nxt = am.get('next_check_at', '-') if am else '-'
    print(f"  第 {5*(i+1):2d} 秒: check_count={cc}, last={last}, next={nxt}")

print()

# 检查结果
am_final = db.get_active_monitor(release_id)
cc_final = am_final.get('check_count', 0) if am_final else 0

print(f"最终 check_count: {cc_final}")
print(f"daemon 仍在运行: {monitor_daemon.is_running()}")

# 列出活跃监控
print()
print("活跃监控列表:")
for am in db.list_active_monitors():
    print(f"  - {am['release_no']}: status={am['status']}, check_count={am.get('check_count', 0)}")

# 清理
print()
print("停止 daemon...")
monitor_daemon.stop()
time.sleep(1)
print(f"停止后 is_running: {monitor_daemon.is_running()}")

print()
if cc_final >= 2:
    print("✅ 验证通过：后台 daemon 真的在自动调度检查")
else:
    print(f"❌ 验证失败：check_count={cc_final}，应该至少有 2 次")
