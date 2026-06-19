# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 审批流程模块
根据风险级别(常规/紧急)自动生成审批流程并分配审批人
"""

from typing import List, Dict

from config import RISK_LEVELS, APPROVERS, get_current_time_str
from logger import log
from database import db


class ApprovalEngine:
    """审批流程引擎"""

    def __init__(self):
        pass

    def generate_approval_flow(self, risk_level: str) -> List[Dict]:
        """
        根据风险级别生成审批流程
        :param risk_level: 'normal' | 'emergency'
        :return: 审批人列表
        """
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"未知风险级别: {risk_level}")

        flow_config = RISK_LEVELS[risk_level]['approval_flow']
        approver_list = []

        for role in flow_config:
            role_approvers = APPROVERS.get(role, [])
            if not role_approvers:
                log.warning(f"角色 {role} 未配置审批人")
                continue
            approver = role_approvers[0]
            approver_list.append({
                'role': role,
                'role_name': self._get_role_name(role),
                'id': approver['id'],
                'name': approver['name'],
                'email': approver['email'],
                'phone': approver['phone'],
            })

        log.info(
            f"生成审批流程 [风险级别: {RISK_LEVELS[risk_level]['name']}], "
            f"共 {len(approver_list)} 位审批人: "
            + ", ".join([f"{a['role_name']}-{a['name']}" for a in approver_list])
        )
        return approver_list

    @staticmethod
    def _get_role_name(role: str) -> str:
        role_map = {
            'warehouse_director': '仓储总监',
            'supply_chain': '供应链',
            'tech': '技术',
            'emergency': '应急审批人',
        }
        return role_map.get(role, role)

    def init_approvals(self, release_id: int, release_no: str, risk_level: str) -> List[Dict]:
        """初始化审批流程，创建审批记录"""
        approver_list = self.generate_approval_flow(risk_level)
        db.create_approvals(release_id, release_no, approver_list)
        db.update_release_status(release_id, 'APPROVAL_PENDING')
        self._send_approval_notifications(release_no, approver_list)
        return approver_list

    def _send_approval_notifications(self, release_no: str, approvers: List[Dict]):
        """发送审批通知（模拟）"""
        for a in approvers:
            log.info(
                f"[通知] 发送审批通知给 {a['name']} ({a['role_name']}), "
                f"Email: {a['email']}, 电话: {a['phone']}, 发布单: {release_no}"
            )

    def approve(self, approval_id: int, approver_id: str,
                comment: str = None, passed: bool = True) -> Dict:
        """执行单个审批"""
        status = db.approve(approval_id, approver_id, comment, passed)
        approval = self._get_approval_by_id(approval_id)

        if not approval:
            return {'success': False, 'message': '审批记录不存在'}

        release = db.get_release(release_id=approval['release_id'])
        if not release:
            return {'success': False, 'message': '发布单不存在'}

        if not passed:
            db.update_release_status(release['id'], 'APPROVAL_REJECTED')
            self._notify_rejection(release, approval, comment)
            return {
                'success': True,
                'approved': False,
                'release_status': 'APPROVAL_REJECTED',
                'message': f"{approval['approver_name']} 已拒绝发布",
            }

        all_done = db.check_all_approved(release['id'])
        if all_done:
            db.update_release_status(release['id'], 'APPROVED')
            self._notify_approval_done(release)
            return {
                'success': True,
                'approved': True,
                'all_approved': True,
                'release_status': 'APPROVED',
                'message': '所有审批人已通过，发布审批完成',
            }

        return {
            'success': True,
            'approved': True,
            'all_approved': False,
            'release_status': 'APPROVAL_PENDING',
            'message': f"{approval['approver_name']} 已通过审批，等待其他审批人",
        }

    def _get_approval_by_id(self, approval_id: int):
        approvals = db._get_conn()
        with approvals as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_approval_flow(self, release_id: int) -> List[Dict]:
        """获取某个发布的完整审批流程"""
        records = db.get_approvals(release_id)
        return [
            {
                'id': r['id'],
                'role': r['approver_role'],
                'role_name': self._get_role_name(r['approver_role']),
                'approver_id': r['approver_id'],
                'approver_name': r['approver_name'],
                'status': r['status'],
                'comment': r['comment'],
                'approved_at': r['approved_at'],
            }
            for r in records
        ]

    def _notify_approval_done(self, release):
        """审批通过通知"""
        log.info(f"[通知] 发布单 {release['release_no']} 审批全部通过，准备进入发布阶段")

    def _notify_rejection(self, release, approval, comment):
        """审批拒绝通知"""
        log.info(
            f"[通知] 发布单 {release['release_no']} 被 {approval['approver_name']} 拒绝, "
            f"原因: {comment or '未说明'}"
        )

    def auto_approve_demo(self, release_id: int, release_no: str):
        """
        演示用：自动完成所有待审批
        实际生产环境应通过人工审批或 OA 对接
        """
        records = db.get_approvals(release_id)
        pending = [r for r in records if r['status'] == 'PENDING']
        log.info(f"自动审批演示: 发布单 {release_no} 待处理 {len(pending)} 条审批")
        for r in pending:
            self.approve(
                approval_id=r['id'],
                approver_id=r['approver_id'],
                comment="自动审批通过(演示模式)",
                passed=True,
            )
