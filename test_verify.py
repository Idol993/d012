# -*- coding: utf-8 -*-
"""
命令行验收测试
1. 老 monitor 写法
2. 新 monitor check 写法
3. 紧急发布回滚 - 检查回滚报告影响仓库数
4. report scheduler run-now - 检查日志
"""
import sys
import os
import time
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from gray_release import GrayReleaseEngine
from monitor_rollback import monitor_engine, rollback_engine
from report import weekly_scheduler
from logger import log

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    print(f"  {status} {name}")
    if detail:
        print(f"     {detail}")
    return condition

def header(msg):
    print()
    print("=" * 70)
    print(f"  {msg}")
    print("=" * 70)

# 找到一个有 FULL_DEPLOYED 的发布单（release_id=4）
TEST_RELEASE_ID = 4
release = db.get_release(release_id=TEST_RELEASE_ID)
print(f"测试用发布单: {release['release_no']} (id={TEST_RELEASE_ID})")

# ============================================================
# 1. 老 monitor 写法
# ============================================================
header("1. 老写法: monitor --release-id ID --check")

ret = subprocess.run(
    ['python', 'main.py', 'monitor',
     '--release-id', str(TEST_RELEASE_ID), '--check'],
    capture_output=True, text=True, cwd=os.path.dirname(__file__)
)
output = ret.stdout + ret.stderr
check("老写法命令执行成功", ret.returncode == 0, f"exit_code={ret.returncode}")
check("输出包含监控检查结果", "监控检查结果" in output,
      "输出无'监控检查结果'")
check("输出包含异常仓库数", "异常仓库数" in output,
      "输出无'异常仓库数'")

# ============================================================
# 2. 新 monitor check 写法
# ============================================================
header("2. 新写法: monitor check --release-id ID")

ret2 = subprocess.run(
    ['python', 'main.py', 'monitor', 'check',
     '--release-id', str(TEST_RELEASE_ID)],
    capture_output=True, text=True, cwd=os.path.dirname(__file__)
)
output2 = ret2.stdout + ret2.stderr
check("新写法命令执行成功", ret2.returncode == 0, f"exit_code={ret2.returncode}")
check("输出包含监控检查结果", "监控检查结果" in output2,
      "输出无'监控检查结果'")

# ============================================================
# 3. 紧急发布 + 回滚，检查回滚报告影响仓库数
# ============================================================
header("3. 紧急发布回滚 - 检查报告影响仓库数")

release_no = f"VERIFY-EMERG-{int(time.time())}"
release_id = db.create_release(
    release_no=release_no, version='v7.7.7', risk_level='emergency',
    title='验证紧急发布', description='验证回滚报告影响仓库数',
    changelog='test', submitter='verify', rollback_version='v1.0.0'
)
gray = GrayReleaseEngine()
gray.execute_full_gray_release(release_id, release_no, 'v7.7.7')

# 检查已部署仓库数
gray_recs = db.get_gray_records(release_id)
full_deployed = [r for r in gray_recs if r['status'] == 'FULL_DEPLOYED']
check(f"紧急发布有 {len(full_deployed)} 个 FULL_DEPLOYED 仓库",
      len(full_deployed) == 8, f"实际 {len(full_deployed)}")

# 触发回滚
check_result = monitor_engine.check_release(release_id, release_no,
                                              force_abnormal=True, auto_rollback=True)
check("自动回滚已触发", check_result.get('rollback_triggered') == True)

rb_result = check_result.get('rollback_result', {})
rb_report_path = rb_result.get('report_path', '').split(',')[0].strip()

# 读取回滚报告，检查影响仓库数
if rb_report_path and os.path.exists(rb_report_path):
    with open(rb_report_path, 'r', encoding='utf-8') as f:
        report_data = json.load(f)
    ia = report_data.get('impact_assessment', {})
    wh_count = ia.get('affected_warehouse_count', 0)
    wh_names = ia.get('affected_warehouse_names', [])
    check(f"回滚报告影响仓库数 = 8", wh_count == 8, f"实际 {wh_count}")
    check(f"回滚报告影响仓库名称数 = 8", len(wh_names) == 8,
          f"实际 {len(wh_names)}: {wh_names}")
    check("报告路径存在", True, rb_report_path)
else:
    check("回滚报告文件存在", False, rb_report_path)

# 检查 rollback_records 里的 affected_warehouses
rb_records = db.get_rollback_records(release_id=release_id)
if rb_records:
    affected = rb_records[0].get('affected_warehouses', '')
    if isinstance(affected, str):
        try:
            affected_list = json.loads(affected)
        except:
            affected_list = [affected]
    else:
        affected_list = affected or []
    check(f"rollback_records 里影响仓库数 = 8",
          len(affected_list) == 8, f"实际 {len(affected_list)}")

# ============================================================
# 4. report scheduler run-now
# ============================================================
header("4. report scheduler run-now - 检查日志")

# 清掉上次运行记录，保证 run-now 会重新生成
last_run_file = os.path.join(os.path.dirname(__file__), 'data', 'weekly_report_last_run.txt')
if os.path.exists(last_run_file):
    os.remove(last_run_file)

ret4 = subprocess.run(
    ['python', 'main.py', 'report', 'scheduler', 'run-now'],
    capture_output=True, text=True, cwd=os.path.dirname(__file__)
)
output4 = ret4.stdout + ret4.stderr
check("run-now 命令执行成功", ret4.returncode == 0, f"exit_code={ret4.returncode}")
check("输出包含生成文件", '生成完成' in output4 or 'txt' in output4,
      "输出无文件信息")

# 检查日志文件里有没有记录
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
log_files = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')], reverse=True)
if log_files:
    latest_log = os.path.join(log_dir, log_files[0])
    with open(latest_log, 'r', encoding='utf-8') as f:
        log_content = f.read()
    check("日志里有'手动触发周报告生成'", '手动触发周报告生成' in log_content)
    check("日志里有 PDF 路径", '.pdf' in log_content or 'PDF' in log_content)
    check("日志里有 XLSX 路径", '.xlsx' in log_content or 'XLSX' in log_content)
    check("日志里有 TXT 路径", '.txt' in log_content or 'TXT' in log_content)
    check("日志里有 JSON 路径", '.json' in log_content or 'JSON' in log_content)
    check("审计日志有记录", '手动触发生成周报告' in log_content)
else:
    check("日志文件存在", False)

# ============================================================
# 总结
# ============================================================
header("验收总结")
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
    print("失败项:")
    for name, status, detail in results:
        if status == FAIL:
            print(f"  ❌ {name}: {detail}")
    sys.exit(1)
else:
    print()
    print("🎉 全部验收通过!")
