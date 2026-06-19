# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 上线发布与异常回滚自动化管理系统
主入口与 CLI 界面
"""

import os
import sys
import json
import random
import argparse
from datetime import datetime, timedelta

from config import (
    WAREHOUSES, RELEASE_STATUS, RISK_LEVELS, APPROVERS,
    get_current_time_str, get_timestamp, MONITOR_THRESHOLDS
)
from logger import log
from database import db
from pre_check import PreCheckEngine
from approval import ApprovalEngine
from gray_release import GrayReleaseEngine
from monitor_rollback import monitor_engine, rollback_engine
from report import report_engine


precheck = PreCheckEngine()
approval = ApprovalEngine()
gray_release = GrayReleaseEngine()


BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║        智能仓储 WMS 系统 - 上线发布与异常回滚自动化管理          ║
║                                                                  ║
║   WMS Release & Rollback Automation Management System           ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""


def generate_release_no():
    return f"REL-{get_timestamp()}"


def cmd_submit(args):
    """提交发布申请"""
    log.new_trace()
    release_no = generate_release_no()
    version = args.version
    risk_level = args.risk
    title = args.title
    description = args.description or ''
    changelog = args.changelog or ''
    submitter = args.submitter or 'admin'
    rollback_version = args.rollback_version or 'stable_last'

    if risk_level not in RISK_LEVELS:
        print(f"错误: 风险级别必须是 {list(RISK_LEVELS.keys())} 中的一个")
        return

    release_id = db.create_release(
        release_no=release_no, version=version, risk_level=risk_level,
        title=title, description=description, changelog=changelog,
        submitter=submitter, rollback_version=rollback_version,
    )

    print(f"\n{'='*60}")
    print(f"  发布申请已提交成功!")
    print(f"{'='*60}")
    print(f"  发布单号:   {release_no}")
    print(f"  ID:         {release_id}")
    print(f"  版本:       {version}")
    print(f"  风险级别:   {RISK_LEVELS[risk_level]['name']}")
    print(f"  标题:       {title}")
    print(f"  提交人:     {submitter}")
    print(f"  回滚版本:   {rollback_version}")
    print(f"  创建时间:   {get_current_time_str()}")
    print(f"{'='*60}")

    if getattr(args, 'auto_precheck', False):
        cmd_precheck(argparse.Namespace(release_id=release_id, release_no=release_no, auto=True))


def cmd_precheck(args):
    """执行前置条件检查"""
    release_id = args.release_id
    release_no = getattr(args, 'release_no', None)

    if not release_id and not release_no:
        print("错误: 必须指定 --release-id 或 --release-no")
        return

    if release_no and not release_id:
        rel = db.get_release(release_no=release_no)
        if rel:
            release_id = rel['id']

    if not release_id:
        print("错误: 未找到对应发布单")
        return

    result = precheck.run_precheck(release_id, release_no or '')

    print(f"\n{'='*60}")
    print(f"  前置条件检查结果")
    print(f"{'='*60}")
    for item in result['items']:
        icon = "✓" if item['passed'] else "✗"
        status = "通过" if item['passed'] else "未通过"
        print(f"  {icon} {item['name']:<18} {item['value']:>8.2%} "
              f"(阈值 {item['threshold']:.0%})  [{status}]")
    print(f"{'-'*60}")
    passed_count = result['summary']['passed_count']
    total = result['summary']['total']
    print(f"  总计: {passed_count}/{total} 项通过")
    print(f"  结果: {'全部通过 ✓' if result['passed'] else '存在未通过项 ✗'}")
    print(f"{'='*60}")

    if result['passed'] and getattr(args, 'auto', False):
        cmd_init_approval(argparse.Namespace(release_id=release_id))


def cmd_init_approval(args):
    """初始化审批流程"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    if release['status'] not in ('PRECHECK_PASSED', 'APPROVAL_PENDING'):
        print(f"警告: 当前状态 [{RELEASE_STATUS.get(release['status'], release['status'])}], "
              f"可能尚未通过前置检查")

    approvers = approval.init_approvals(release['id'], release['release_no'], release['risk_level'])

    print(f"\n{'='*60}")
    print(f"  审批流程已生成")
    print(f"{'='*60}")
    print(f"  发布单号: {release['release_no']}")
    print(f"  风险级别: {RISK_LEVELS[release['risk_level']]['name']}")
    print(f"  审批人:")
    for i, a in enumerate(approvers, 1):
        print(f"    {i}. {a['role_name']:<8} - {a['name']} ({a['email']})")
    print(f"{'='*60}")

    if getattr(args, 'auto_approve', False):
        cmd_auto_approve(argparse.Namespace(release_id=release_id))


def cmd_auto_approve(args):
    """自动审批（演示用）"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    approval.auto_approve_demo(release_id, release['release_no'])

    records = approval.get_approval_flow(release_id)
    print(f"\n{'='*60}")
    print(f"  审批流程状态")
    print(f"{'='*60}")
    for r in records:
        icon = "✓" if r['status'] == 'APPROVED' else ("○" if r['status'] == 'PENDING' else "✗")
        print(f"  {icon} {r['role_name']:<8} - {r['approver_name']}: "
              f"{r['status']}  {r.get('comment') or ''}")

    release = db.get_release(release_id=release_id)
    print(f"{'-'*60}")
    print(f"  当前状态: {RELEASE_STATUS.get(release['status'], release['status'])}")
    print(f"{'='*60}")

    if release['status'] == 'APPROVED' and getattr(args, 'auto_release', False):
        cmd_gray_release(argparse.Namespace(release_id=release_id))


def cmd_gray_release(args):
    """执行灰度发布"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    if release['status'] not in ('APPROVED', 'GRAY_PROGRESS', 'FULL_RELEASE'):
        print(f"警告: 当前状态 [{RELEASE_STATUS.get(release['status'], release['status'])}]")

    print(f"\n{'='*60}")
    print(f"  开始灰度发布: {release['release_no']}")
    print(f"{'='*60}")

    def on_abnormal(rid, rno, reason, result):
        log.warning(f"灰度异常, 触发自动回滚: {reason}")
        rollback_engine.execute_rollback(
            rid, rno, 'auto', f"灰度异常: {reason}", result
        )
        return True

    result = gray_release.execute_full_gray_release(
        release_id, release['release_no'], release['version'],
        on_abnormal=on_abnormal,
    )

    print(f"{'-'*60}")
    print(f"  灰度发布结果: {'成功' if result['success'] else '失败'}")
    print(f"  部署仓库数:   {len(result.get('deployed_warehouses', []))}")
    print(f"  最终状态:     {result.get('final_status', 'N/A')}")
    print(f"{'='*60}")

    if result['success'] and getattr(args, 'auto_monitor', True):
        cmd_start_monitor(argparse.Namespace(release_id=release_id))


def cmd_start_monitor(args):
    """启动监控"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    def on_abnormal(rid, rno, monitor_result):
        release_now = db.get_release(release_id=rid)
        if release_now and release_now['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED'):
            return True
        log.warning(f"监控发现异常，触发自动回滚: {rno}")
        rollback_engine.execute_rollback(
            rid, rno, 'auto', '监控指标超过阈值', monitor_result
        )
        return True

    print(f"\n启动监控: 发布单 {release['release_no']}")
    print(f"监控阈值:")
    print(f"  上架错误率阈值: {MONITOR_THRESHOLDS['putaway_error_rate']:.2%}")
    print(f"  出库延迟率阈值: {MONITOR_THRESHOLDS['outbound_delay_rate']:.2%}")
    print(f"  库存差异率阈值: {MONITOR_THRESHOLDS['inventory_diff_rate']:.2%}")

    monitor_engine.start_background_monitor(
        release_id, release['release_no'], on_abnormal
    )
    print(f"监控已启动，每5分钟检查一次，异常将自动触发回滚...")


def cmd_check_monitor(args):
    """立即执行一次监控检查"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    force_abnormal = getattr(args, 'force_abnormal', False)
    result = monitor_engine.check_release(
        release_id, release['release_no'], force_abnormal=force_abnormal
    )

    print(f"\n{'='*60}")
    print(f"  监控检查结果: {release['release_no']}")
    print(f"{'='*60}")
    for m in result.get('all_metrics', []):
        icon = "✗" if m['is_abnormal'] else "✓"
        print(f"  {icon} {m['warehouse_id']}({m['warehouse_name']}):")
        print(f"      上架错误率: {m['putaway_error_rate']:.2%} "
              f"{'[超阈值]' if m['putaway_error_rate'] > MONITOR_THRESHOLDS['putaway_error_rate'] else ''}")
        print(f"      出库延迟率: {m['outbound_delay_rate']:.2%} "
              f"{'[超阈值]' if m['outbound_delay_rate'] > MONITOR_THRESHOLDS['outbound_delay_rate'] else ''}")
        print(f"      库存差异率: {m['inventory_diff_rate']:.2%} "
              f"{'[超阈值]' if m['inventory_diff_rate'] > MONITOR_THRESHOLDS['inventory_diff_rate'] else ''}")
        print(f"      总订单数:   {m['total_orders']}, 异常订单: {m['abnormal_orders']}")
    print(f"{'-'*60}")
    print(f"  异常仓库数: {result['abnormal_count']}/{result['checked_count']}")
    print(f"  异常订单数: {result.get('total_abnormal_orders', 0)}")
    print(f"  总体状态:   {'异常' if result['is_abnormal'] else '正常'}")
    print(f"{'='*60}")


def cmd_rollback(args):
    """手动触发回滚"""
    release_id = args.release_id
    release = db.get_release(release_id=release_id)
    if not release:
        print("错误: 未找到对应发布单")
        return

    reason = args.reason or '手动触发回滚'
    operator = args.operator or 'manual_operator'

    print(f"\n{'='*60}")
    print(f"  手动回滚确认")
    print(f"{'='*60}")
    print(f"  发布单号: {release['release_no']}")
    print(f"  当前版本: {release['version']}")
    print(f"  回滚版本: {release.get('rollback_version', 'stable_last')}")
    print(f"  回滚原因: {reason}")
    print(f"{'='*60}")

    if not getattr(args, 'yes', False):
        confirm = input("\n确认执行回滚? (yes/no): ")
        if confirm.lower() != 'yes':
            print("已取消回滚")
            return

    monitor_engine.stop_monitor(release_id)
    result = rollback_engine.execute_rollback(
        release_id, release['release_no'], 'manual', reason, operator=operator
    )

    print(f"\n回滚结果: {'成功' if result['success'] else '失败'}")
    print(f"影响仓库: {', '.join(result['affected_warehouses'])}")
    print(f"影响订单: {result['affected_orders']} 单")
    print(f"根因分析: {result['root_cause']}")
    print(f"报告路径: {result['report_path']}")


def cmd_drill(args):
    """回滚演练相关"""
    sub = getattr(args, 'drill_subcommand', None)

    if sub == 'create':
        target_release_no = args.target_release
        target_warehouses = args.warehouses.split(',') if args.warehouses else None
        operator = args.operator or 'drill_operator'

        result = rollback_engine.create_rollback_drill_plan(
            target_release_no=target_release_no,
            target_warehouses=target_warehouses,
            operator=operator,
        )

        if not result.get('success'):
            print(f"创建失败: {result.get('message')}")
            return

        plan = result['plan']
        print(f"\n{'='*60}")
        print(f"  回滚演练计划已创建")
        print(f"{'='*60}")
        print(f"  演练编号:   {result['drill_no']}")
        print(f"  演练ID:     {result['drill_id']}")
        print(f"  目标发布:   {target_release_no or '模拟'}")
        print(f"  目标仓库:   {', '.join(plan.get('target_warehouses', []))}")
        print(f"  预计耗时:   {plan.get('expected_duration')}")
        print(f"  演练步骤:")
        for step in plan.get('steps', []):
            print(f"    {step['step']}. {step['action']} ({step['duration']})")
        print(f"  成功标准:")
        for sc in plan.get('success_criteria', []):
            print(f"    - {sc}")
        print(f"{'='*60}")

    elif sub == 'execute':
        drill_id = args.drill_id
        operator = args.operator or 'drill_operator'

        result = rollback_engine.execute_rollback_drill(drill_id, operator)

        if not result.get('success'):
            print(f"执行失败: {result.get('message')}")
            return

        print(f"\n{'='*60}")
        print(f"  回滚演练执行结果")
        print(f"{'='*60}")
        print(f"  演练编号: {result['drill_no']}")
        print(f"  演练结果: {'成功' if result['result'] == 'SUCCESS' else '失败'}")
        print(f"  报告路径: {result['report_path']}")
        print(f"  执行日志:")
        for line in result['execution_log']:
            print(f"    {line}")
        print(f"{'='*60}")

    elif sub == 'list':
        drills = db.list_drills(limit=args.limit or 20)
        print(f"\n{'='*70}")
        print(f"  回滚演练列表 (最近 {len(drills)} 条)")
        print(f"{'='*70}")
        print(f"  {'编号':<22} {'标题':<25} {'状态':<12} {'结果':<10} {'创建时间'}")
        for d in drills:
            print(f"  {d['drill_no']:<22} {d['title'][:23]:<25} "
                  f"{d['status']:<12} {(d.get('result') or '-'):<10} {d['created_at']}")
        print(f"{'='*70}")


def cmd_report(args):
    """报告相关"""
    sub = getattr(args, 'report_subcommand', None)

    if sub == 'weekly':
        print("正在生成周统计报告...")
        result = report_engine.generate_weekly_report()

        print(f"\n{'='*60}")
        print(f"  周统计报告生成完成")
        print(f"{'='*60}")
        print(f"  统计周期: {result['week_range']}")
        s = result['summary']
        print(f"  总发布数: {s['total_releases']}")
        print(f"  成功数:   {s['success_count']}, 成功率: {s['success_rate']:.2f}%")
        print(f"  回滚数:   {s['rollback_count']}, 回滚率: {s['rollback_rate']:.2f}%")
        print(f"  常规发布: {s['normal_count']}, 紧急发布: {s['emergency_count']}")
        print(f"{'-'*60}")
        print(f"  生成文件:")
        for fmt, path in result['files'].items():
            print(f"    [{fmt.upper()}] {path}")
        print(f"{'='*60}")

    elif sub == 'export':
        start_time = args.start_time
        end_time = args.end_time
        status = args.status
        risk_level = args.risk
        warehouse_id = args.warehouse
        version = args.version
        export_format = args.format or 'csv'
        data_type = args.data_type or 'releases'

        path = report_engine.query_and_export(
            start_time=start_time, end_time=end_time,
            status=status, risk_level=risk_level,
            warehouse_id=warehouse_id, version=version,
            export_format=export_format, data_type=data_type,
        )
        print(f"\n导出完成: {path}")


def cmd_list(args):
    """查询发布列表"""
    status = args.status
    risk_level = args.risk
    limit = args.limit or 20

    releases = db.list_releases(
        status=status, risk_level=risk_level, limit=limit
    )

    print(f"\n{'='*90}")
    print(f"  发布列表 (共 {len(releases)} 条)")
    print(f"{'='*90}")
    print(f"  {'发布单号':<20} {'版本':<12} {'风险':<8} {'状态':<16} "
          f"{'提交人':<10} {'创建时间':<20}")
    for r in releases:
        print(
            f"  {r['release_no']:<20} {r['version']:<12} "
            f"{RISK_LEVELS.get(r['risk_level'], {}).get('name', r['risk_level']):<8} "
            f"{RELEASE_STATUS.get(r['status'], r['status']):<16} "
            f"{r['submitter']:<10} {r['created_at']:<20}"
        )
    print(f"{'='*90}")


def cmd_detail(args):
    """查看发布详情"""
    release_id = args.release_id
    release_no = getattr(args, 'release_no', None)

    release = db.get_release(release_id=release_id, release_no=release_no)
    if not release:
        print("未找到对应发布单")
        return

    print(f"\n{'='*60}")
    print(f"  发布详情")
    print(f"{'='*60}")
    print(f"  发布单号:   {release['release_no']}")
    print(f"  ID:         {release['id']}")
    print(f"  版本:       {release['version']}")
    print(f"  风险级别:   {RISK_LEVELS.get(release['risk_level'], {}).get('name', release['risk_level'])}")
    print(f"  状态:       {RELEASE_STATUS.get(release['status'], release['status'])}")
    print(f"  标题:       {release['title']}")
    print(f"  描述:       {release['description']}")
    print(f"  变更日志:   {release['changelog']}")
    print(f"  提交人:     {release['submitter']}")
    print(f"  回滚版本:   {release.get('rollback_version', '')}")
    print(f"  创建时间:   {release['created_at']}")
    print(f"  更新时间:   {release['updated_at']}")
    if release.get('finished_at'):
        print(f"  完成时间:   {release['finished_at']}")
    print(f"{'-'*60}")

    prechecks = db.get_precheck_records(release['id'])
    if prechecks:
        print(f"  前置检查:")
        for pc in prechecks:
            icon = "✓" if pc['passed'] else "✗"
            print(f"    {icon} {pc['check_item']}: {pc['check_value']:.2%} "
                  f"(阈值 {pc['threshold']:.0%})")
    print(f"{'-'*60}")

    approvals = approval.get_approval_flow(release['id'])
    if approvals:
        print(f"  审批流程:")
        for a in approvals:
            icon = "✓" if a['status'] == 'APPROVED' else ("○" if a['status'] == 'PENDING' else "✗")
            print(f"    {icon} {a['role_name']} - {a['approver_name']}: {a['status']}")
            if a.get('comment'):
                print(f"       备注: {a['comment']}")
    print(f"{'-'*60}")

    rollbacks = db.get_rollback_records(release_id=release['id'])
    if rollbacks:
        print(f"  回滚记录:")
        for rb in rollbacks:
            print(f"    [{rb['trigger_type']}] {rb['status']} - 影响订单 {rb.get('affected_orders', 0)}")
            if rb.get('root_cause'):
                print(f"       根因: {rb['root_cause']}")
    print(f"{'='*60}")


def cmd_demo(args):
    """运行完整演示流程"""
    print(BANNER)
    print("开始运行完整的 WMS 发布-灰度-监控-回滚 演示流程...\n")

    release_no = generate_release_no()
    version = f"v2.{datetime.now().strftime('%m%d')}.{random.randint(1, 99)}"

    print(f"\n[1/7] 提交发布申请...")
    release_id = db.create_release(
        release_no=release_no, version=version, risk_level='normal',
        title='WMS 库存管理模块优化',
        description='优化库位分配算法，提升出库效率',
        changelog='1. 优化库位推荐算法\n2. 修复入库验收Bug\n3. 提升AGV调度性能',
        submitter='developer01', rollback_version='v2.05.12.stable',
    )
    print(f"  ✓ 发布单已创建: {release_no}")

    print(f"\n[2/7] 执行前置条件检查...")
    precheck.run_precheck(release_id, release_no, force_pass=True)
    release = db.get_release(release_id=release_id)
    if release['status'] == 'PRECHECK_FAILED':
        print("  ✗ 前置检查未通过，演示终止")
        return
    print(f"  ✓ 前置检查通过")

    print(f"\n[3/7] 初始化审批流程并自动审批...")
    approval.init_approvals(release_id, release_no, 'normal')
    approval.auto_approve_demo(release_id, release_no)
    release = db.get_release(release_id=release_id)
    print(f"  ✓ 审批完成，当前状态: {RELEASE_STATUS.get(release['status'], release['status'])}")

    print(f"\n[4/7] 执行灰度发布...")
    result = gray_release.execute_full_gray_release(release_id, release_no, version)
    print(f"  ✓ 灰度发布{'成功' if result['success'] else '失败'}")

    print(f"\n[5/7] 执行监控检查 (模拟异常)...")
    monitor_result = monitor_engine.check_release(release_id, release_no, force_abnormal=True)
    print(f"  异常仓库数: {monitor_result['abnormal_count']}, "
          f"异常订单: {monitor_result['total_abnormal_orders']}")

    print(f"\n[6/7] 触发自动回滚...")
    rollback_result = rollback_engine.execute_rollback(
        release_id, release_no, 'auto', '监控指标异常', monitor_result
    )
    print(f"  ✓ 回滚{'成功' if rollback_result['success'] else '失败'}")
    print(f"  报告: {rollback_result['report_path']}")

    print(f"\n[7/7] 创建并执行回滚演练...")
    drill_result = rollback_engine.create_rollback_drill_plan(
        target_release_no=release_no, operator='demo_user'
    )
    print(f"  ✓ 演练计划已创建: {drill_result['drill_no']}")
    exec_result = rollback_engine.execute_rollback_drill(
        drill_result['drill_id'], operator='demo_user'
    )
    print(f"  ✓ 演练完成，结果: {exec_result['result']}")

    print(f"\n{'='*60}")
    print(f"  演示流程全部完成!")
    print(f"  发布单号: {release_no}")
    print(f"  回滚报告: {rollback_result['report_path']}")
    print(f"  演练报告: {exec_result['report_path']}")
    print(f"  查看详情: python main.py detail --release-no {release_no}")
    print(f"{'='*60}")


def build_parser():
    parser = argparse.ArgumentParser(
        description='智能仓储 WMS 系统 - 上线发布与异常回滚自动化管理系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py submit --version v2.1.0 --risk normal --title "功能迭代"
  python main.py precheck --release-id 1
  python main.py approve --release-id 1 --auto-approve
  python main.py release --release-id 1
  python main.py monitor --release-id 1 --check
  python main.py rollback --release-id 1 --reason "严重Bug" --yes
  python main.py drill create
  python main.py drill execute --drill-id 1
  python main.py report weekly
  python main.py report export --start-time 2025-01-01 --end-time 2025-12-31 --format xlsx
  python main.py demo
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    p_submit = subparsers.add_parser('submit', help='提交发布申请')
    p_submit.add_argument('--version', required=True, help='版本号')
    p_submit.add_argument('--risk', required=True, choices=['normal', 'emergency'], help='风险级别')
    p_submit.add_argument('--title', required=True, help='发布标题')
    p_submit.add_argument('--description', help='发布描述')
    p_submit.add_argument('--changelog', help='变更日志')
    p_submit.add_argument('--submitter', default='admin', help='提交人')
    p_submit.add_argument('--rollback-version', help='回滚版本')
    p_submit.add_argument('--auto-precheck', action='store_true', help='自动执行前置检查')
    p_submit.set_defaults(func=cmd_submit)

    p_precheck = subparsers.add_parser('precheck', help='执行前置条件检查')
    p_precheck.add_argument('--release-id', type=int, help='发布单ID')
    p_precheck.add_argument('--release-no', help='发布单号')
    p_precheck.add_argument('--auto', action='store_true', help='通过后自动进入审批')
    p_precheck.set_defaults(func=cmd_precheck)

    p_approve = subparsers.add_parser('approve', help='审批流程相关')
    p_approve.add_argument('--release-id', type=int, required=True, help='发布单ID')
    p_approve.add_argument('--auto-approve', action='store_true', help='自动完成所有审批(演示)')
    p_approve.add_argument('--auto-release', action='store_true', help='通过后自动开始发布')
    p_approve.set_defaults(func=cmd_init_approval)

    p_release = subparsers.add_parser('release', help='执行灰度发布')
    p_release.add_argument('--release-id', type=int, required=True, help='发布单ID')
    p_release.add_argument('--auto-monitor', action='store_true', default=True, help='自动启动监控')
    p_release.set_defaults(func=cmd_gray_release)

    p_monitor = subparsers.add_parser('monitor', help='监控相关')
    p_monitor.add_argument('--release-id', type=int, required=True, help='发布单ID')
    p_monitor.add_argument('--check', action='store_true', help='立即执行一次检查')
    p_monitor.add_argument('--start', action='store_true', help='启动后台监控')
    p_monitor.add_argument('--force-abnormal', action='store_true', help='模拟异常')
    p_monitor.set_defaults(func=lambda args: (cmd_start_monitor(args) if args.start else cmd_check_monitor(args)))

    p_rollback = subparsers.add_parser('rollback', help='手动回滚')
    p_rollback.add_argument('--release-id', type=int, required=True, help='发布单ID')
    p_rollback.add_argument('--reason', help='回滚原因')
    p_rollback.add_argument('--operator', default='manual', help='操作人')
    p_rollback.add_argument('--yes', action='store_true', help='自动确认')
    p_rollback.set_defaults(func=cmd_rollback)

    p_drill = subparsers.add_parser('drill', help='回滚演练')
    drill_sub = p_drill.add_subparsers(dest='drill_subcommand')
    p_dc = drill_sub.add_parser('create', help='创建演练计划')
    p_dc.add_argument('--target-release', help='目标发布单号(可选)')
    p_dc.add_argument('--warehouses', help='目标仓库ID(逗号分隔, 可选)')
    p_dc.add_argument('--operator', default='drill_operator', help='操作人')
    p_de = drill_sub.add_parser('execute', help='执行演练')
    p_de.add_argument('--drill-id', type=int, required=True, help='演练ID')
    p_de.add_argument('--operator', default='drill_operator', help='操作人')
    p_dl = drill_sub.add_parser('list', help='列出演练记录')
    p_dl.add_argument('--limit', type=int, help='数量限制')
    p_drill.set_defaults(func=cmd_drill)

    p_report = subparsers.add_parser('report', help='报告与导出')
    report_sub = p_report.add_subparsers(dest='report_subcommand')
    report_sub.add_parser('weekly', help='生成周统计报告')
    p_export = report_sub.add_parser('export', help='按条件导出')
    p_export.add_argument('--start-time', help='开始时间 YYYY-MM-DD')
    p_export.add_argument('--end-time', help='结束时间 YYYY-MM-DD')
    p_export.add_argument('--status', help='状态筛选')
    p_export.add_argument('--risk', help='风险级别')
    p_export.add_argument('--warehouse', help='仓库ID')
    p_export.add_argument('--version', help='版本模糊匹配')
    p_export.add_argument('--format', choices=['csv', 'xlsx', 'json', 'txt'], help='导出格式')
    p_export.add_argument('--data-type', choices=['releases', 'rollbacks', 'monitors', 'approvals'], help='数据类型')
    p_report.set_defaults(func=cmd_report)

    p_list = subparsers.add_parser('list', help='查询发布列表')
    p_list.add_argument('--status', help='状态筛选')
    p_list.add_argument('--risk', help='风险级别')
    p_list.add_argument('--limit', type=int, help='数量限制')
    p_list.set_defaults(func=cmd_list)

    p_detail = subparsers.add_parser('detail', help='查看发布详情')
    p_detail.add_argument('--release-id', type=int, help='发布单ID')
    p_detail.add_argument('--release-no', help='发布单号')
    p_detail.set_defaults(func=cmd_detail)

    p_demo = subparsers.add_parser('demo', help='运行完整演示流程')
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main():
    print(BANNER)
    parser = build_parser()

    if len(sys.argv) < 2:
        parser.print_help()
        return

    args = parser.parse_args()

    if hasattr(args, 'func'):
        try:
            args.func(args)
        except KeyboardInterrupt:
            print("\n用户中断")
        except Exception as e:
            log.error(f"命令执行异常: {str(e)}", exc_info=True)
            print(f"\n错误: {str(e)}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
