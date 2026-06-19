# -*- coding: utf-8 -*-
"""
端到端验证 - 5个问题全部覆盖
"""
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from gray_release import GrayReleaseEngine
from monitor_rollback import monitor_daemon, monitor_engine
from report import report_engine, weekly_scheduler
from logger import log

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    print(f"  {status} {name}")
    if detail and not condition:
        print(f"     详情: {detail}")
    return condition

def header(msg):
    print()
    print("=" * 70)
    print(f"  {msg}")
    print("=" * 70)

# ============================================================
# 问题 3: 紧急发布仓库记录
# ============================================================
header("问题 3: 紧急发布跳过灰度后也要写入仓库记录")

release_no = f"E2E-3-{int(time.time())}"
release_id = db.create_release(
    release_no=release_no, version='v3.0.0', risk_level='emergency',
    title='问题3测试', description='紧急发布', changelog='test',
    submitter='e2e', rollback_version='v1.0.0'
)
gray = GrayReleaseEngine()
result = gray.execute_full_gray_release(release_id, release_no, 'v3.0.0')

records = db.get_gray_records(release_id)
full_deployed = [r for r in records if r['status'] == 'FULL_DEPLOYED']

check("紧急发布创建8条仓库记录", len(records) == 8, f"实际{len(records)}")
check("仓库状态为 FULL_DEPLOYED", len(full_deployed) == 8, f"实际{len(full_deployed)}")

check_result = monitor_engine.check_release(release_id, release_no)
check("监控检查不会提示'暂无已部署仓库'",
      check_result.get('checked_count', 0) > 0,
      f"checked_count={check_result.get('checked_count')}")

# ============================================================
# 问题 2: monitor check --auto-rollback
# ============================================================
header("问题 2: monitor check 超阈值时自动回滚")

check_result2 = monitor_engine.check_release(release_id, release_no,
                                              force_abnormal=True, auto_rollback=True)
check("rollback_triggered 字段为 True", check_result2.get('rollback_triggered') == True)
check("返回 rollback_result", 'rollback_result' in check_result2)

rb_records = db.get_rollback_records(release_id=release_id)
check("生成回滚记录", len(rb_records) >= 1, f"实际{len(rb_records)}")

release_after = db.get_release(release_id=release_id)
check("发布状态更新为回滚相关",
      release_after['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED'),
      f"实际状态: {release_after['status']}")

report_paths = check_result2.get('rollback_result', {}).get('report_path', '')
check("生成回滚报告", report_paths and os.path.exists(report_paths.split(',')[0].strip()),
      f"report_path={report_paths}")

# ============================================================
# 问题 4: report export 真实过滤
# ============================================================
header("问题 4: report export 按条件真实过滤")

# 先创建另一个不同版本的发布和回滚
release_no2 = f"E2E-4-{int(time.time())}"
release_id2 = db.create_release(
    release_no=release_no2, version='v4.0.0-OTHER', risk_level='emergency',
    title='问题4测试-其他版本', description='', changelog='',
    submitter='e2e', rollback_version='v1.0.0'
)
gray.execute_full_gray_release(release_id2, release_no2, 'v4.0.0-OTHER')
monitor_engine.check_release(release_id2, release_no2, force_abnormal=True, auto_rollback=True)

# 按版本过滤回滚记录
path = report_engine.query_and_export(version='v3.0.0', data_type='rollbacks', export_format='json')
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

all_match = True
for r in data:
    rel = db.get_release(release_id=r['release_id'])
    if 'v3.0.0' not in rel['version']:
        all_match = False
        print(f"    不匹配: release_id={r['release_id']}, version={rel['version']}")

check(f"按 version='v3.0.0' 过滤回滚记录", all_match and len(data) > 0,
      f"记录数={len(data)}")

# 按仓库过滤监控记录
path2 = report_engine.query_and_export(warehouse_id='WH001', data_type='monitors', export_format='json')
with open(path2, 'r', encoding='utf-8') as f:
    data2 = json.load(f)

wh_set = set(r.get('warehouse_id') for r in data2 if r.get('warehouse_id'))
check(f"按 warehouse='WH001' 过滤监控记录",
      len(data2) > 0 and (not wh_set or wh_set == {'WH001'}),
      f"记录数={len(data2)}, 仓库集合={wh_set}")

# ============================================================
# 问题 5: 周报告定时任务
# ============================================================
header("问题 5: 周报告定时任务 start/stop/status/run-now")

# 先确保没在跑
if weekly_scheduler.is_running():
    weekly_scheduler.stop()
    time.sleep(1)

st1 = weekly_scheduler.status()
check("初始状态为未运行", st1['running'] == False)

start_r = weekly_scheduler.start()
check("start 启动成功", start_r.get('success') == True, start_r.get('message'))
check("启动后 is_running=True", weekly_scheduler.is_running())

st2 = weekly_scheduler.status()
check("status 显示运行中", st2['running'] == True)
check("status 显示 pid", st2.get('pid') is not None)
check("status 显示调度规则", '每周一' in st2.get('schedule', ''))

stop_r = weekly_scheduler.stop()
check("stop 停止成功", stop_r.get('success') == True)
time.sleep(1)
check("停止后 is_running=False", not weekly_scheduler.is_running())

# run-now
run_r = weekly_scheduler.run_now()
check("run-now 生成 TXT", 'txt' in run_r.get('files', {}))
check("run-now 生成 XLSX", 'xlsx' in run_r.get('files', {}))
check("run-now 生成 PDF", 'pdf' in run_r.get('files', {}))
check("run-now 记录上次运行时间", weekly_scheduler._get_last_run_date() is not None)

for fmt, p in run_r.get('files', {}).items():
    check(f"run-now {fmt} 文件存在", os.path.exists(p), p)

# ============================================================
# 问题 1: 后台监控 daemon 自动调度
# ============================================================
header("问题 1: 后台监控 daemon 持久化运行 + 自动调度")

# 先确保 daemon 没在跑
if monitor_daemon.is_running():
    monitor_daemon.stop()
    time.sleep(1)

check("daemon 初始未运行", not monitor_daemon.is_running())

# 创建发布并加入监控队列
release_no3 = f"E2E-1-{int(time.time())}"
release_id3 = db.create_release(
    release_no=release_no3, version='v1.0.0', risk_level='normal',
    title='问题1测试', description='', changelog='',
    submitter='e2e', rollback_version='v0.9.0'
)
gray.execute_full_gray_release(release_id3, release_no3, 'v1.0.0')
db.add_active_monitor(release_id3, release_no3, added_by='e2e', check_interval_seconds=5)

check("加入活跃监控队列成功", db.get_active_monitor(release_id3) is not None)

# 启动 daemon
start_r = monitor_daemon.start()
check("daemon start 成功", start_r.get('success') == True)
check("daemon 进程在运行", monitor_daemon.is_running())

# 等待自动调度检查
print("  等待 daemon 自动调度检查 (约 15 秒)...")
for i in range(3):
    time.sleep(5)
    am = db.get_active_monitor(release_id3)
    cc = am.get('check_count', 0) if am else 0
    print(f"    {5*(i+1)}s: check_count={cc}")

am_final = db.get_active_monitor(release_id3)
cc_final = am_final.get('check_count', 0) if am_final else 0
check(f"daemon 至少自动检查 2 次 (间隔5秒)", cc_final >= 2, f"实际 check_count={cc_final}")

# 测试 stop
stop_r = monitor_daemon.stop()
check("daemon stop 成功", stop_r.get('success') == True)
time.sleep(1)
check("daemon 已停止", not monitor_daemon.is_running())

# 测试 monitor list 命令能看到队列
am_list = db.list_active_monitors(status=None)
check("list_active_monitors 返回数据", len(am_list) > 0)

# 测试 remove
db.remove_active_monitor(release_id3)
check("remove 后队列中不存在", db.get_active_monitor(release_id3) is None)

# ============================================================
# 总结
# ============================================================
header("端到端测试总结")
total = len(results)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = total - passed

print()
for name, status, detail in results:
    print(f"  {status} {name}")

print()
print(f"总计: {total} 项, 通过 {passed} 项, 失败 {failed} 项")

if failed > 0:
    print()
    print("失败项详情:")
    for name, status, detail in results:
        if status == FAIL:
            print(f"  ❌ {name}: {detail}")
    sys.exit(1)
else:
    print()
    print("🎉 全部 5 个问题验证通过!")
    print()
    print("  1. ✅ 监控后台 daemon - 独立进程持久运行, 自动调度检查")
    print("  2. ✅ monitor check --auto-rollback - 异常时按流程回滚, 更新记录")
    print("  3. ✅ 紧急发布仓库记录 - FULL_DEPLOYED, 监控回滚全覆盖")
    print("  4. ✅ report export 真实过滤 - 按 warehouse/version 精确过滤")
    print("  5. ✅ 周报告定时任务 - start/stop/status/run-now 全支持")
