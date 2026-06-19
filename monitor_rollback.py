# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 监控与自动回滚模块
每5分钟监控上架错误率、出库延迟、库存差异率
超过阈值自动触发回滚、生成报告、通知干系人
"""

import os
import json
import random
import time
import threading
from typing import List, Dict, Optional

from config import (
    MONITOR_THRESHOLDS, MONITOR_INTERVAL_SECONDS,
    WAREHOUSES, APPROVERS, REPORT_DIR, get_current_time_str, get_timestamp
)
from logger import log
from database import db


class MonitorEngine:
    """监控引擎"""

    def __init__(self):
        self._running = False
        self._thread = None
        self._active_releases = {}

    def _generate_mock_metrics(self, warehouse_id: str,
                               force_abnormal: bool = False) -> Dict:
        """生成模拟监控指标"""
        base_error = random.uniform(0, 0.015)
        base_delay = random.uniform(0, 0.025)
        base_diff = random.uniform(0, 0.004)

        if force_abnormal or random.random() < 0.1:
            base_error = random.uniform(0.02, 0.05)
            base_delay = random.uniform(0.03, 0.06)
            base_diff = random.uniform(0.005, 0.01)

        total_orders = random.randint(500, 5000)
        abnormal_orders = int(total_orders * (base_error + base_delay + base_diff) / 3)

        putaway_error_rate = round(base_error, 4)
        outbound_delay_rate = round(base_delay, 4)
        inventory_diff_rate = round(base_diff, 4)

        is_abnormal = (
            putaway_error_rate > MONITOR_THRESHOLDS['putaway_error_rate']
            or outbound_delay_rate > MONITOR_THRESHOLDS['outbound_delay_rate']
            or inventory_diff_rate > MONITOR_THRESHOLDS['inventory_diff_rate']
        )

        alert_details = {}
        if putaway_error_rate > MONITOR_THRESHOLDS['putaway_error_rate']:
            alert_details['putaway_error'] = (
                f"上架错误率 {putaway_error_rate:.2%} 超过阈值 "
                f"{MONITOR_THRESHOLDS['putaway_error_rate']:.2%}"
            )
        if outbound_delay_rate > MONITOR_THRESHOLDS['outbound_delay_rate']:
            alert_details['outbound_delay'] = (
                f"出库延迟率 {outbound_delay_rate:.2%} 超过阈值 "
                f"{MONITOR_THRESHOLDS['outbound_delay_rate']:.2%}"
            )
        if inventory_diff_rate > MONITOR_THRESHOLDS['inventory_diff_rate']:
            alert_details['inventory_diff'] = (
                f"库存差异率 {inventory_diff_rate:.2%} 超过阈值 "
                f"{MONITOR_THRESHOLDS['inventory_diff_rate']:.2%}"
            )

        return {
            'warehouse_id': warehouse_id,
            'warehouse_name': WAREHOUSES.get(warehouse_id, {}).get('name', ''),
            'putaway_error_rate': putaway_error_rate,
            'outbound_delay_rate': outbound_delay_rate,
            'inventory_diff_rate': inventory_diff_rate,
            'total_orders': total_orders,
            'abnormal_orders': abnormal_orders,
            'is_abnormal': is_abnormal,
            'alert_details': alert_details,
            'status': 'abnormal' if is_abnormal else 'healthy',
        }

    def check_release(self, release_id: int, release_no: str,
                      force_abnormal: bool = False,
                      auto_rollback: bool = False) -> Dict:
        """检查单个发布单的所有已部署仓库
        :param auto_rollback: 若发现异常是否自动触发回滚
        """
        release = db.get_release(release_id=release_id)
        if not release:
            return {'success': False, 'message': '发布单不存在'}

        gray_records = db.get_gray_records(release_id)
        deployed_warehouses = [
            r for r in gray_records
            if r['status'] in ('DEPLOYED', 'VERIFIED', 'FAILED', 'FULL_DEPLOYED')
        ]

        if not deployed_warehouses:
            return {'success': True, 'checked': 0, 'message': '暂无已部署仓库'}

        all_metrics = []
        abnormal_warehouses = []
        total_abnormal_orders = 0

        for gr in deployed_warehouses:
            metrics = self._generate_mock_metrics(gr['warehouse_id'], force_abnormal)
            all_metrics.append(metrics)
            db.add_monitor_record(release_id, release_no, gr['warehouse_id'], metrics)

            if metrics['is_abnormal']:
                abnormal_warehouses.append(metrics)
                total_abnormal_orders += metrics['abnormal_orders']

            wh_name = WAREHOUSES.get(gr['warehouse_id'], {}).get('name', '')
            log.info(
                f"[监控] 仓库 {gr['warehouse_id']}({wh_name}): "
                f"上架错误率={metrics['putaway_error_rate']:.2%}, "
                f"出库延迟率={metrics['outbound_delay_rate']:.2%}, "
                f"库存差异率={metrics['inventory_diff_rate']:.2%} "
                f"-> {'异常' if metrics['is_abnormal'] else '正常'}"
            )

        result = {
            'success': True,
            'release_id': release_id,
            'release_no': release_no,
            'checked_count': len(all_metrics),
            'abnormal_count': len(abnormal_warehouses),
            'is_abnormal': len(abnormal_warehouses) > 0,
            'total_abnormal_orders': total_abnormal_orders,
            'abnormal_warehouses': abnormal_warehouses,
            'all_metrics': all_metrics,
            'checked_at': get_current_time_str(),
        }

        if result['is_abnormal']:
            log.warning(
                f"[监控告警] 发布单 {release_no} 存在 "
                f"{len(abnormal_warehouses)} 个异常仓库, "
                f"异常订单 {total_abnormal_orders} 单"
            )

            if auto_rollback:
                log.warning(f"[自动回滚] 监控发现异常，触发自动回滚流程")
                rb_result = rollback_engine.execute_rollback(
                    release_id, release_no, 'auto',
                    f"监控指标异常: {len(abnormal_warehouses)} 个仓库超标",
                    monitor_result=result,
                )
                result['rollback_triggered'] = True
                result['rollback_result'] = rb_result

        return result

    def start_background_monitor(self, release_id: int, release_no: str,
                                 on_abnormal_callback=None):
        """启动后台监控线程"""
        if self._running:
            log.warning("监控已在运行中")
            return

        self._running = True
        self._active_releases[release_id] = {
            'release_no': release_no,
            'callback': on_abnormal_callback,
        }

        def _monitor_loop():
            log.info(f"启动后台监控线程，监控发布单 {release_no}，"
                     f"间隔 {MONITOR_INTERVAL_SECONDS} 秒")
            while self._running and release_id in self._active_releases:
                try:
                    result = self.check_release(release_id, release_no)
                    if result['is_abnormal'] and on_abnormal_callback:
                        should_stop = on_abnormal_callback(release_id, release_no, result)
                        if should_stop:
                            log.info(f"回调要求停止监控 {release_no}")
                            break
                except Exception as e:
                    log.error(f"监控循环异常: {str(e)}")
                time.sleep(MONITOR_INTERVAL_SECONDS)
            log.info(f"发布单 {release_no} 监控已停止")

        self._thread = threading.Thread(target=_monitor_loop, daemon=True)
        self._thread.start()

    def stop_monitor(self, release_id: int = None):
        """停止监控"""
        if release_id:
            if release_id in self._active_releases:
                del self._active_releases[release_id]
                log.info(f"已停止发布单 ID={release_id} 的监控")
        else:
            self._running = False
            self._active_releases.clear()
            log.info("已停止所有监控")


class RollbackEngine:
    """回滚引擎"""

    def __init__(self):
        self.monitor = MonitorEngine()

    def analyze_root_cause(self, monitor_result: Dict) -> str:
        """根因分析"""
        causes = []
        for wh in monitor_result.get('abnormal_warehouses', []):
            details = wh.get('alert_details', {})
            if 'putaway_error' in details:
                causes.append(f"上架错误率异常: {details['putaway_error']}")
            if 'outbound_delay' in details:
                causes.append(f"出库延迟异常: {details['outbound_delay']}")
            if 'inventory_diff' in details:
                causes.append(f"库存差异异常: {details['inventory_diff']}")

        if not causes:
            causes.append("监控指标异常，需进一步人工排查")

        return "\n".join(causes)

    def _get_stakeholders(self) -> List[Dict]:
        """获取所有干系人"""
        stakeholders = []
        for role_list in APPROVERS.values():
            stakeholders.extend(role_list)
        seen = set()
        unique = []
        for s in stakeholders:
            if s['id'] not in seen:
                seen.add(s['id'])
                unique.append(s)
        return unique

    def _send_rollback_notification(self, release_no: str, rollback_info: Dict):
        """发送回滚通知"""
        stakeholders = self._get_stakeholders()
        log.info(f"[回滚通知] 向 {len(stakeholders)} 位干系人发送回滚通知")
        for s in stakeholders:
            log.info(
                f"  通知 {s['name']}({s['email']}): "
                f"发布单 {release_no} 触发回滚, "
                f"原因: {rollback_info.get('trigger_reason', '')}"
            )

    def _generate_rollback_report(self, release_id: int, release_no: str,
                                  rollback_id: int, rollback_version: str,
                                  monitor_result: Dict, trigger_type: str,
                                  trigger_reason: str) -> str:
        """生成回滚报告"""
        release = db.get_release(release_id=release_id)
        root_cause = self.analyze_root_cause(monitor_result)

        affected_warehouses = []
        affected_orders = 0
        for wh in monitor_result.get('abnormal_warehouses', []):
            affected_warehouses.append({
                'id': wh['warehouse_id'],
                'name': wh['warehouse_name'],
                'abnormal_orders': wh['abnormal_orders'],
                'metrics': {
                    'putaway_error_rate': wh['putaway_error_rate'],
                    'outbound_delay_rate': wh['outbound_delay_rate'],
                    'inventory_diff_rate': wh['inventory_diff_rate'],
                }
            })
            affected_orders += wh.get('abnormal_orders', 0)

        gray_records = db.get_gray_records(release_id)
        all_deployed = [
            WAREHOUSES.get(r['warehouse_id'], {}).get('name', r['warehouse_id'])
            for r in gray_records
            if r['status'] in ('DEPLOYED', 'VERIFIED', 'FAILED', 'FULL_DEPLOYED')
        ]

        report_data = {
            'report_title': 'WMS 系统发布回滚报告',
            'report_no': f"RB-{get_timestamp()}",
            'generated_at': get_current_time_str(),
            'release_info': {
                'release_no': release_no,
                'version': release['version'] if release else '',
                'risk_level': release['risk_level'] if release else '',
                'title': release['title'] if release else '',
            },
            'rollback_info': {
                'rollback_id': rollback_id,
                'trigger_type': trigger_type,
                'trigger_reason': trigger_reason,
                'rollback_version': rollback_version,
                'started_at': get_current_time_str(),
            },
            'impact_assessment': {
                'affected_warehouses': affected_warehouses,
                'affected_warehouse_names': all_deployed,
                'affected_warehouse_count': len(all_deployed),
                'affected_orders': affected_orders,
            },
            'root_cause_analysis': root_cause,
            'monitor_details': monitor_result,
        }

        os.makedirs(REPORT_DIR, exist_ok=True)
        report_path = os.path.join(
            REPORT_DIR,
            f"rollback_report_{release_no}_{get_timestamp()}.json"
        )
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        txt_path = os.path.join(
            REPORT_DIR,
            f"rollback_report_{release_no}_{get_timestamp()}.txt"
        )
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(self._format_report_text(report_data))

        log.info(f"回滚报告已生成: {report_path}, {txt_path}")
        return report_path

    def _format_report_text(self, data: Dict) -> str:
        """格式化报告为文本"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  {data['report_title']}")
        lines.append("=" * 70)
        lines.append(f"报告编号: {data['report_no']}")
        lines.append(f"生成时间: {data['generated_at']}")
        lines.append("")

        ri = data['release_info']
        lines.append("-" * 50)
        lines.append("【发布信息】")
        lines.append(f"  发布单号: {ri['release_no']}")
        lines.append(f"  版本号:   {ri['version']}")
        lines.append(f"  风险级别: {ri['risk_level']}")
        lines.append(f"  标题:     {ri['title']}")
        lines.append("")

        rbi = data['rollback_info']
        lines.append("-" * 50)
        lines.append("【回滚信息】")
        lines.append(f"  回滚ID:   {rbi['rollback_id']}")
        lines.append(f"  触发类型: {rbi['trigger_type']}")
        lines.append(f"  触发原因: {rbi['trigger_reason']}")
        lines.append(f"  回滚版本: {rbi['rollback_version']}")
        lines.append(f"  开始时间: {rbi['started_at']}")
        lines.append("")

        ia = data['impact_assessment']
        lines.append("-" * 50)
        lines.append("【影响评估】")
        lines.append(f"  影响仓库数: {ia['affected_warehouse_count']}")
        lines.append(f"  影响仓库:   {', '.join(ia['affected_warehouse_names'])}")
        lines.append(f"  异常订单数: {ia['affected_orders']}")
        for wh in ia['affected_warehouses']:
            lines.append(f"    - {wh['name']}({wh['id']}): 异常订单 {wh['abnormal_orders']}")
            lines.append(
                f"      上架错误率={wh['metrics']['putaway_error_rate']:.2%}, "
                f"出库延迟率={wh['metrics']['outbound_delay_rate']:.2%}, "
                f"库存差异率={wh['metrics']['inventory_diff_rate']:.2%}"
            )
        lines.append("")

        lines.append("-" * 50)
        lines.append("【根因分析】")
        lines.append(f"  {data['root_cause_analysis']}")
        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def execute_rollback(self, release_id: int, release_no: str,
                         trigger_type: str, trigger_reason: str,
                         monitor_result: Dict = None,
                         operator: str = "system") -> Dict:
        """
        执行回滚
        :param trigger_type: 'auto' | 'manual' | 'drill'
        """
        log.warning(f"{'='*60}")
        log.warning(f"开始执行回滚: 发布单={release_no}, 触发类型={trigger_type}, "
                    f"原因={trigger_reason}")
        log.warning(f"{'='*60}")

        release = db.get_release(release_id=release_id)
        if not release:
            return {'success': False, 'message': '发布单不存在'}

        rollback_version = release.get('rollback_version') or 'stable_last'
        db.update_release_status(release_id, 'ROLLBACK_TRIGGERED')

        rollback_id = db.create_rollback_record(
            release_id, release_no, trigger_type, trigger_reason,
            rollback_version, operator
        )

        if not monitor_result:
            monitor_result = self.monitor.check_release(release_id, release_no)

        report_path = self._generate_rollback_report(
            release_id, release_no, rollback_id, rollback_version,
            monitor_result, trigger_type, trigger_reason
        )

        self._send_rollback_notification(release_no, {
            'trigger_reason': trigger_reason,
            'rollback_version': rollback_version,
        })

        db.update_release_status(release_id, 'ROLLBACK_IN_PROGRESS')

        gray_records = db.get_gray_records(release_id)
        affected_warehouse_ids = [
            r['warehouse_id'] for r in gray_records
            if r['status'] in ('DEPLOYED', 'VERIFIED', 'FAILED', 'FULL_DEPLOYED')
        ]
        affected_names = [
            WAREHOUSES.get(wid, {}).get('name', wid)
            for wid in affected_warehouse_ids
        ]

        rollback_success = True
        rolled_back = []
        for wid in affected_warehouse_ids:
            wh_name = WAREHOUSES.get(wid, {}).get('name', wid)
            log.info(f"[回滚] 正在将仓库 {wid}({wh_name}) 回滚到版本 {rollback_version}")
            time.sleep(0.3)
            success = random.random() > 0.05
            if success:
                rolled_back.append(wid)
                log.info(f"[回滚] 仓库 {wid} 回滚成功")
            else:
                rollback_success = False
                log.error(f"[回滚] 仓库 {wid} 回滚失败")

        affected_orders = monitor_result.get('total_abnormal_orders', 0)
        root_cause = self.analyze_root_cause(monitor_result)

        db.update_rollback_record(
            rollback_id,
            status='ROLLBACK_SUCCESS' if rollback_success else 'ROLLBACK_FAILED',
            affected_warehouses=json.dumps(affected_names, ensure_ascii=False),
            affected_orders=affected_orders,
            root_cause=root_cause,
            report_path=report_path,
        )

        final_status = 'ROLLBACK_SUCCESS' if rollback_success else 'ROLLBACK_FAILED'
        db.update_release_status(release_id, final_status)

        result = {
            'success': rollback_success,
            'rollback_id': rollback_id,
            'release_no': release_no,
            'rollback_version': rollback_version,
            'rolled_back_warehouses': rolled_back,
            'affected_warehouses': affected_names,
            'affected_orders': affected_orders,
            'root_cause': root_cause,
            'report_path': report_path,
            'trigger_type': trigger_type,
            'trigger_reason': trigger_reason,
        }

        log.warning(f"回滚{'成功' if rollback_success else '失败'}: "
                    f"回滚了 {len(rolled_back)} 个仓库, "
                    f"影响 {affected_orders} 个订单")

        log.audit("回滚执行", operator, release_no,
                  details=result)

        log.info("回滚完成，加入活跃监控队列守护稳定版本...")
        db.add_active_monitor(release_id, release_no, added_by=operator)
        log.info(f"发布单 {release_no} 已加入活跃监控队列，等待守护进程检查")

        return result

    def create_rollback_drill_plan(self, target_release_no: str = None,
                                   target_warehouses: List[str] = None,
                                   operator: str = "operator") -> Dict:
        """创建回滚演练计划"""
        drill_no = f"DRILL-{get_timestamp()}"
        title = f"回滚演练-{get_current_time_str()}"

        if target_release_no:
            release = db.get_release(release_no=target_release_no)
            if not release:
                return {'success': False, 'message': '目标发布单不存在'}

        target_whs = target_warehouses or list(WAREHOUSES.keys())[:2]

        plan = {
            'drill_objective': '验证回滚流程的有效性与时效性',
            'preparation': [
                '确认稳定版本可用',
                '确认仓库列表与优先级',
                '通知相关干系人',
                '准备回滚报告模板',
            ],
            'steps': [
                {'step': 1, 'action': '模拟监控异常告警', 'duration': '1分钟'},
                {'step': 2, 'action': '触发自动回滚流程', 'duration': '2分钟'},
                {'step': 3, 'action': '逐仓库执行版本回滚', 'duration': '5分钟'},
                {'step': 4, 'action': '验证回滚后系统稳定性', 'duration': '3分钟'},
                {'step': 5, 'action': '生成演练报告与改进点', 'duration': '2分钟'},
            ],
            'target_warehouses': target_whs,
            'rollback_version': 'stable_last',
            'expected_duration': '13分钟',
            'success_criteria': [
                '所有仓库成功回滚到稳定版本',
                '回滚总耗时 < 10分钟',
                '回滚后监控指标全部正常',
                '演练报告完整生成',
            ],
        }

        drill_id = db.create_drill(
            drill_no=drill_no,
            title=title,
            operator=operator,
            plan=plan,
            target_release_no=target_release_no,
            target_warehouses=target_whs,
        )

        log.info(f"已创建回滚演练计划: {drill_no}, ID={drill_id}")
        log.audit("创建回滚演练", operator, drill_no,
                  details={"target_release": target_release_no,
                           "target_warehouses": target_whs})

        return {
            'success': True,
            'drill_id': drill_id,
            'drill_no': drill_no,
            'title': title,
            'plan': plan,
        }

    def execute_rollback_drill(self, drill_id: int, operator: str = "operator") -> Dict:
        """执行回滚演练"""
        drill = db.get_drill(drill_id=drill_id)
        if not drill:
            return {'success': False, 'message': '演练计划不存在'}

        log.info(f"开始执行回滚演练: {drill['drill_no']}")
        db.update_drill(drill_id, status='IN_PROGRESS', started_at=get_current_time_str())

        execution_log = []
        execution_log.append(f"[{get_current_time_str()}] 演练开始")

        plan = json.loads(drill['plan']) if drill.get('plan') else {}
        steps = plan.get('steps', [])

        for step in steps:
            time.sleep(0.2)
            step_result = random.random() > 0.1
            log_msg = (f"[{get_current_time_str()}] 步骤{step['step']}: "
                       f"{step['action']} -> {'成功' if step_result else '失败'}")
            execution_log.append(log_msg)
            log.info(log_msg)
            if not step_result:
                execution_log.append(f"[{get_current_time_str()}] 演练在步骤{step['step']}失败")
                break

        all_passed = all("-> 成功" in log for log in execution_log[1:])
        result = 'SUCCESS' if all_passed else 'FAILED'

        os.makedirs(REPORT_DIR, exist_ok=True)
        report_path = os.path.join(
            REPORT_DIR, f"drill_report_{drill['drill_no']}_{get_timestamp()}.txt"
        )
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write(f"回滚演练报告 - {drill['drill_no']}\n")
            f.write("=" * 60 + "\n")
            f.write(f"演练标题: {drill['title']}\n")
            f.write(f"操作人:   {drill['operator']}\n")
            f.write(f"演练结果: {result}\n\n")
            f.write("执行日志:\n")
            for log_line in execution_log:
                f.write(f"  {log_line}\n")
            f.write("\n改进建议:\n")
            f.write("  1. 定期组织演练，保持团队熟练度\n")
            f.write("  2. 优化回滚脚本，减少人工干预\n")
            f.write("  3. 完善监控告警，加快异常发现速度\n")

        db.update_drill(
            drill_id,
            status='FINISHED',
            result=result,
            execution_log="\n".join(execution_log),
            report_path=report_path,
        )

        log.info(f"回滚演练完成，结果={result}，报告: {report_path}")
        log.audit("执行回滚演练", operator, drill['drill_no'],
                  details={"result": result})

        return {
            'success': True,
            'drill_id': drill_id,
            'drill_no': drill['drill_no'],
            'result': result,
            'execution_log': execution_log,
            'report_path': report_path,
        }

class MonitorDaemon:
    """监控守护进程 - 后台常驻，基于 active_monitors 表调度"""

    def __init__(self):
        self._running = False
        self.pid_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'data', 'monitor_daemon.pid'
        )

    def is_running(self) -> bool:
        """检查守护进程是否在运行"""
        if not os.path.exists(self.pid_file):
            return False
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
            import ctypes
            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(1024, 0, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        except Exception:
            return False

    def get_pid(self):
        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, 'r') as f:
                    return int(f.read().strip())
            except:
                return None
        return None

    def start(self) -> Dict:
        """启动后台守护进程"""
        if self.is_running():
            return {'success': False, 'message': '监控守护进程已在运行', 'pid': self.get_pid()}

        import sys
        import subprocess

        script_path = os.path.abspath(__file__)
        project_dir = os.path.dirname(script_path)
        main_path = os.path.join(project_dir, 'main.py')

        python_exe = sys.executable

        if os.name == 'nt':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [python_exe, main_path, 'monitor', 'daemon', 'run'],
                cwd=project_dir,
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [python_exe, main_path, 'monitor', 'daemon', 'run'],
                cwd=project_dir,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        import time
        for _ in range(10):
            time.sleep(0.5)
            if self.is_running():
                break

        pid = self.get_pid()
        log.info(f"监控守护进程已启动, PID={pid}")
        log.audit("启动监控守护进程", "system", "monitor_daemon",
                  details={"pid": pid})
        return {'success': True, 'message': '监控守护进程已启动', 'pid': pid}

    def stop(self) -> Dict:
        """停止守护进程"""
        if not self.is_running():
            return {'success': True, 'message': '监控守护进程未在运行'}

        pid = self.get_pid()
        try:
            if os.name == 'nt':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                process = kernel32.OpenProcess(1, 0, pid)
                if process:
                    kernel32.TerminateProcess(process, 0)
                    kernel32.CloseHandle(process)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            log.warning(f"停止守护进程异常: {e}")

        try:
            if os.path.exists(self.pid_file):
                os.remove(self.pid_file)
        except:
            pass

        log.info(f"监控守护进程已停止, PID={pid}")
        log.audit("停止监控守护进程", "system", "monitor_daemon",
                  details={"pid": pid})
        return {'success': True, 'message': '监控守护进程已停止', 'pid': pid}

    def status(self) -> Dict:
        """获取守护进程状态"""
        running = self.is_running()
        active_monitors = db.list_active_monitors(status='RUNNING')
        return {
            'running': running,
            'pid': self.get_pid() if running else None,
            'active_monitor_count': len(active_monitors),
            'active_monitors': active_monitors,
        }

    def run_loop(self):
        """守护进程主循环 - 阻塞运行"""
        pid = os.getpid()
        with open(self.pid_file, 'w') as f:
            f.write(str(pid))

        log.info(f"[守护进程] 监控守护进程启动, PID={pid}")
        log.audit("监控守护进程启动", "system", "monitor_daemon",
                  details={"pid": pid})

        self._running = True
        import signal

        def _handle_signal(signum, frame):
            log.info(f"[守护进程] 收到信号 {signum}, 准备退出")
            self._running = False

        if os.name != 'nt':
            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)

        try:
            while self._running:
                try:
                    self._tick()
                except Exception as e:
                    log.error(f"[守护进程] 循环异常: {str(e)}", exc_info=True)
                time.sleep(10)
        finally:
            try:
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)
            except:
                pass
            log.info("[守护进程] 监控守护进程已退出")

    def _tick(self):
        """单次调度：检查所有到期的活跃监控"""
        to_check = db.get_monitors_to_check()
        if not to_check:
            return

        log.debug(f"[守护进程] 本轮需检查 {len(to_check)} 个发布单")

        for am in to_check:
            release_id = am['release_id']
            release_no = am['release_no']

            release = db.get_release(release_id=release_id)
            if not release:
                db.remove_active_monitor(release_id)
                continue

            if release['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED',
                                     'RELEASE_SUCCESS', 'CANCELLED',
                                     'APPROVAL_REJECTED', 'PRECHECK_FAILED'):
                if release['status'] in ('RELEASE_SUCCESS',):
                    pass
                if release['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED',
                                         'CANCELLED', 'APPROVAL_REJECTED',
                                         'PRECHECK_FAILED'):
                    db.remove_active_monitor(release_id)
                    log.info(f"[守护进程] 发布单 {release_no} 状态为 {release['status']}, 移除监控")
                    continue

            try:
                result = monitor_engine.check_release(
                    release_id, release_no, auto_rollback=True
                )

                if result.get('rollback_triggered'):
                    log.warning(
                        f"[守护进程] 发布单 {release_no} 已触发自动回滚, "
                        f"继续监控稳定版本"
                    )

            except Exception as e:
                log.error(f"[守护进程] 检查发布单 {release_no} 异常: {e}")

            db.update_monitor_check(release_id)


monitor_engine = MonitorEngine()
rollback_engine = RollbackEngine()
monitor_daemon = MonitorDaemon()
