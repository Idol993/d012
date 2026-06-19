# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 配置管理模块
"""

import os
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')
EXPORT_DIR = os.path.join(BASE_DIR, 'exports')

for _dir in [DATA_DIR, LOG_DIR, REPORT_DIR, EXPORT_DIR]:
    os.makedirs(_dir, exist_ok=True)


DB_PATH = os.path.join(DATA_DIR, 'wms_release.db')
LOG_FILE = os.path.join(LOG_DIR, 'wms_release.log')
AUDIT_LOG_FILE = os.path.join(LOG_DIR, 'audit_trail.log')


WAREHOUSES = {
    'WH001': {'name': '华东中心仓', 'region': '华东', 'priority': 1, 'gray_group': 1},
    'WH002': {'name': '华南中心仓', 'region': '华南', 'priority': 2, 'gray_group': 1},
    'WH003': {'name': '华北中心仓', 'region': '华北', 'priority': 3, 'gray_group': 2},
    'WH004': {'name': '西南区域仓', 'region': '西南', 'priority': 4, 'gray_group': 2},
    'WH005': {'name': '华中区域仓', 'region': '华中', 'priority': 5, 'gray_group': 3},
    'WH006': {'name': '东北区域仓', 'region': '东北', 'priority': 6, 'gray_group': 3},
    'WH007': {'name': '西北区域仓', 'region': '西北', 'priority': 7, 'gray_group': 4},
    'WH008': {'name': '海外保税仓', 'region': '海外', 'priority': 8, 'gray_group': 4},
}


APPROVERS = {
    'warehouse_director': [
        {'id': 'WD001', 'name': '张总监', 'email': 'zhang.director@wms.com', 'phone': '13800000001'},
    ],
    'supply_chain': [
        {'id': 'SC001', 'name': '李经理', 'email': 'li.manager@wms.com', 'phone': '13800000002'},
        {'id': 'SC002', 'name': '王经理', 'email': 'wang.manager@wms.com', 'phone': '13800000003'},
    ],
    'tech': [
        {'id': 'TECH001', 'name': '陈工', 'email': 'chen.engineer@wms.com', 'phone': '13800000004'},
        {'id': 'TECH002', 'name': '刘工', 'email': 'liu.engineer@wms.com', 'phone': '13800000005'},
    ],
    'emergency': [
        {'id': 'EM001', 'name': '赵副总裁', 'email': 'zhao.vp@wms.com', 'phone': '13800000006'},
    ]
}


PRECHECK_THRESHOLDS = {
    'inbound_acceptance_rate': 0.95,
    'location_algorithm_valid_rate': 0.98,
    'inventory_consistency_rate': 0.99,
    'device_health_rate': 0.97,
}


MONITOR_THRESHOLDS = {
    'putaway_error_rate': 0.02,
    'outbound_delay_rate': 0.03,
    'inventory_diff_rate': 0.005,
}


GRAY_STRATEGY = {
    'groups': [1, 2, 3, 4],
    'observation_minutes': 30,
    'max_fail_rate': 0.01,
}


MONITOR_INTERVAL_SECONDS = 300


RISK_LEVELS = {
    'normal': {
        'name': '常规发布',
        'description': '常规功能迭代，影响范围可控',
        'approval_flow': ['warehouse_director', 'supply_chain', 'tech'],
        'gray_required': True,
    },
    'emergency': {
        'name': '紧急发布',
        'description': '紧急故障修复或重大业务变更',
        'approval_flow': ['emergency', 'tech'],
        'gray_required': False,
    }
}


EMAIL_CONFIG = {
    'smtp_server': 'smtp.wms.com',
    'smtp_port': 465,
    'sender': 'wms-release@wms.com',
    'use_ssl': True,
}


RELEASE_STATUS = {
    'CREATED': '已创建',
    'PRECHECK_PENDING': '待前置检查',
    'PRECHECK_PASSED': '前置检查通过',
    'PRECHECK_FAILED': '前置检查失败',
    'APPROVAL_PENDING': '待审批',
    'APPROVAL_REJECTED': '审批拒绝',
    'APPROVED': '审批通过',
    'GRAY_PROGRESS': '灰度发布中',
    'FULL_RELEASE': '全量发布',
    'RELEASE_SUCCESS': '发布成功',
    'ROLLBACK_TRIGGERED': '触发回滚',
    'ROLLBACK_IN_PROGRESS': '回滚中',
    'ROLLBACK_SUCCESS': '回滚成功',
    'ROLLBACK_FAILED': '回滚失败',
    'CANCELLED': '已取消',
}


def get_current_time_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_timestamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S')
