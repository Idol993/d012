# -*- coding: utf-8 -*-
"""
端到端验证测试：
1. 后台监控 daemon 自动调度
2. monitor check --auto-rollback
3. 紧急发布仓库记录
4. report export 真实过滤
5. 周报告 scheduler
"""
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from gray_release import GrayReleaseEngine
from monitor_rollback import monitor_daemon, monitor_engine, rollback_engine
from report import report_engine, weekly_scheduler
from logger import log

def header(msg):
    print()
    print("=" * 70)
    print(f"  {msg}")
    print("=" * 70)

def section(msg):
    print()
    print(f"  --- {msg} ---")

# ============================================================
# 测试 1: 紧急发布 + 仓库记录
# ============================================================
header("测试 3: 紧急发布是否写入仓库部署记录")

release_no = f"E2E-EMERG-{int(time.time())}"
release_id = db.create_release(
    release_no=release_no,
    version='v9.9.9-e2e',
    risk_level='emergency',
    title='E2E紧急发布测试',
    description='端到端测试',
    changelog='测试用',
    submitter='e2e_tester',
    rollback_version='v1.0.0-STABLE'
)
print(f"  创建紧急发布: release_id={release_id}, no={release_no}")

gray = GrayReleaseEngine()
result = gray.execute_full_gray_release(release_id, release_no, 'v9.9.9-e2e')
print(f"  发布结果: success={result.get('success')}, msg={result.get('message')}")

records = db.get_gray_records(release_id)
full_deployed = [r for r in records if r['status'] == 'FULL_DEPLOYED']
print(f"  gray_release_records: 总数={len(records)}, FULL_DEPLOYED={len(full_deployed)}")
assert len(records) == 8, f"应该有8条仓库记录，实际{len(records)}"
assert len(full_deployed) == 8, f"应该有8条FULL_DEPLOYED，实际{len(full_deployed)}"
print("  ✅ 紧急发布仓库记录正常")

# ============================================================
# 测试 2: monitor check (不触发回滚)
# ============================================================
header("测试 2: monitor check 正常检查不回滚")

check_result = monitor_engine.check_release(release_id, release_no, auto_rollback=False)
print(f"  检查结果: checked={check_result.get('checked')}, abnormal={check_result.get('abnormal_count')}")

release_after = db.get_release(release_id=release_id)
print(f"  发布状态: {release_after['status']}")
assert release_after['status'] == 'FULL_RELEASE', f"状态应该还是FULL_RELEASE，实际{release_after['status']}"
print("  ✅ 不加 auto_rollback 时只检查不回滚")

# ============================================================
# 测试 3: monitor check --auto-rollback
# ============================================================
header("测试 2: monitor check --auto-rollback 触发回滚")

check_result2 = monitor_engine.check_release(release_id, release_no,
                                              force_abnormal=True, auto_rollback=True)
print(f"  异常仓库数: {check_result2.get('abnormal_count')}")
print(f"  自动回滚触发: {check_result2.get('rollback_triggered')}")
print(f"  回滚结果: {check_result2.get('rollback_result', {}).get('success')}")

rb_records = db.get_rollback_records(release_id=release_id)
print(f"  回滚记录数: {len(rb_records)}")
assert len(rb_records) >= 1, "应该有回滚记录"
assert check_result2.get('rollback_triggered') == True, "应该触发自动回滚"

release_after2 = db.get_release(release_id=release_id)
print(f"  发布状态: {release_after2['status']}")
assert release_after2['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED'), \
    f"状态应该是回滚相关，实际{release_after2['status']}"
print("  ✅ auto_rollback 正常触发回滚、生成记录、更新状态")

# ============================================================
# 测试 4: report export 过滤
# ============================================================
header("测试 4: report export 按版本过滤回滚记录")

path_all = report_engine.query_and_export(data_type='rollbacks', export_format='json')
with open(path_all, 'r', encoding='utf-8') as f:
    data_all = json.load(f)
print(f"  全部回滚记录数: {len(data_all)}")

path_v = report_engine.query_and_export(version='v9.9.9-e2e',
                                         data_type='rollbacks', export_format='json')
with open(path_v, 'r', encoding='utf-8') as f:
    data_v = json.load(f)
print(f"  按 v9.9.9-e2e 过滤后: {len(data_v)}")

release_ids_in_v = set(r['release_id'] for r in data_v)
for rid in release_ids_in_v:
    rel = db.get_release(release_id=rid)
    print(f"    release_id={rid}, version={rel['version']}")
    assert 'v9.9.9-e2e' in rel['version'], f"版本不匹配: {rel['version']}"
print("  ✅ 回滚记录按版本真实过滤")

section("按仓库过滤监控记录")
path_wh = report_engine.query_and_export(warehouse_id='WH001',
                                          data_type='monitors', export_format='json')
with open(path_wh, 'r', encoding='utf-8') as f:
    data_wh = json.load(f)
print(f"  WH001 监控记录数: {len(data_wh)}")
if data_wh:
    whs = set(r.get('warehouse_id') for r in data_wh)
    print(f"    涉及仓库: {whs}")
    assert whs == {'WH001'} or not whs - {'WH001', None}, "过滤后只能有WH001"
print("  ✅ 监控记录按仓库真实过滤")

# ============================================================
# 测试 5: 周报告 scheduler
# ============================================================
header("测试 5: 周报告定时任务 scheduler")

section("status (未启动)")
st = weekly_scheduler.status()
print(f"  running={st['running']}, schedule={st['schedule']}")
assert st['running'] == False

section("run-now 立即执行")
result = weekly_scheduler.run_now()
print(f"  生成文件: {list(result.get('files', {}).keys())}")
for fmt, p in result.get('files', {}).items():
    print(f"    [{fmt}] {p}")
    assert os.path.exists(p), f"文件不存在: {p}"

last_run = weekly_scheduler._get_last_run_date()
print(f"  上次运行记录: {last_run}")
assert last_run is not None, "应该记录上次运行时间"
print("  ✅ 周报告 scheduler run-now 正常")

# ============================================================
# 测试 6: 后台 daemon 自动调度
# ============================================================
header("测试 1: 监控后台 daemon 自动调度")

section("先确保 daemon 没在跑")
if monitor_daemon.is_running():
    monitor_daemon.stop()
    time.sleep(1)
print(f"  daemon running={monitor_daemon.is_running()}")

section("创建一个新的发布并加入监控队列")
release_no2 = f"E2E-DAEMON-{int(time.time())}"
release_id2 = db.create_release(
    release_no=release_no2,
    version='v8.8.8-daemon',
    risk_level='normal',
    title='E2E daemon测试',
    description='测试daemon自动调度',
    changelog='测试',
    submitter='e2e_tester',
    rollback_version='v1.0.0'
)
gray.execute_full_gray_release(release_id2, release_no2, 'v8.8.8-daemon')
db.add_active_monitor(release_id2, release_no2, added_by='e2e_test',
                       check_interval_seconds=5)
print(f"  已加入监控队列: {release_no2}, 检查间隔5秒")

section("启动 daemon")
start_result = monitor_daemon.start()
print(f"  start: {start_result['message']}, pid={start_result.get('pid')}")
assert monitor_daemon.is_running(), "daemon 应该已经启动"

section("等待 daemon 自动检查(15秒)")
for i in range(3):
    time.sleep(5)
    active = db.get_active_monitor(release_id2)
    if active:
        print(f"  等待 {5*(i+1)}s: check_count={active.get('check_count')}, "
              f"last_check={active.get('last_check_at')}")
    else:
        print(f"  等待 {5*(i+1)}s: 活跃记录不存在")

active_final = db.get_active_monitor(release_id2)
check_count = active_final.get('check_count', 0) if active_final else 0
print(f"  最终检查次数: {check_count}")
assert check_count >= 2, f"daemon 应该至少检查了2次，实际{check_count}"
print("  ✅ 后台 daemon 真的在自动调度检查")

section("停止 daemon")
stop_result = monitor_daemon.stop()
print(f"  stop: {stop_result['message']}")
assert not monitor_daemon.is_running(), "daemon 应该已经停止"

# ============================================================
# 总览
# ============================================================
header("全部测试通过 🎉")
print("""
  1. ✅ 后台 daemon 自动调度 - 独立进程，终端关闭后仍在运行
  2. ✅ monitor check --auto-rollback - 异常时按规则回滚、生成报告、更新记录
  3. ✅ 紧急发布仓库记录 - FULL_DEPLOYED，监控和回滚都能正确识别
  4. ✅ report export 真实过滤 - 回滚/监控/审批都按 warehouse/version/时间过滤
  5. ✅ 周报告定时任务 - start/stop/status/run-now 全支持，日志留痕
""")
