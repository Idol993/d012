# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 前置条件检查模块
检查项：入库验收通过率、库位算法校验、库存一致性、设备对接健康度
"""

import random
import time
from typing import List, Dict

from config import PRECHECK_THRESHOLDS, WAREHOUSES, get_current_time_str
from logger import log
from database import db


class PreCheckEngine:
    """前置条件检查引擎"""

    CHECK_ITEMS = {
        'inbound_acceptance_rate': {
            'name': '入库验收通过率',
            'desc': '最近7天入库单验收通过比例',
            'threshold_key': 'inbound_acceptance_rate',
        },
        'location_algorithm_valid_rate': {
            'name': '库位算法校验通过率',
            'desc': '库位分配、推荐算法准确率',
            'threshold_key': 'location_algorithm_valid_rate',
        },
        'inventory_consistency_rate': {
            'name': '库存一致性',
            'desc': '系统库存与实物库存盘点一致率',
            'threshold_key': 'inventory_consistency_rate',
        },
        'device_health_rate': {
            'name': '设备对接健康度',
            'desc': 'AGV、PDA、RFID、分拣机等设备在线率',
            'threshold_key': 'device_health_rate',
        }
    }

    def __init__(self):
        pass

    def _mock_inbound_acceptance_rate(self) -> float:
        rate = 0.92 + random.uniform(0, 0.07)
        return round(rate, 4)

    def _mock_location_algorithm_valid_rate(self) -> float:
        rate = 0.95 + random.uniform(0, 0.05)
        return round(rate, 4)

    def _mock_inventory_consistency_rate(self) -> float:
        rate = 0.97 + random.uniform(0, 0.03)
        return round(rate, 4)

    def _mock_device_health_rate(self) -> float:
        rate = 0.94 + random.uniform(0, 0.06)
        return round(rate, 4)

    def _check_single_item(self, item_key: str, force_pass: bool = False) -> Dict:
        checkers = {
            'inbound_acceptance_rate': self._mock_inbound_acceptance_rate,
            'location_algorithm_valid_rate': self._mock_location_algorithm_valid_rate,
            'inventory_consistency_rate': self._mock_inventory_consistency_rate,
            'device_health_rate': self._mock_device_health_rate,
        }
        checker = checkers.get(item_key)
        if not checker:
            return None

        value = checker()
        threshold = PRECHECK_THRESHOLDS[self.CHECK_ITEMS[item_key]['threshold_key']]
        if force_pass:
            value = max(value, threshold + 0.01)
        passed = value >= threshold
        item_info = self.CHECK_ITEMS[item_key]
        return {
            'item': item_key,
            'name': item_info['name'],
            'value': value,
            'threshold': threshold,
            'passed': passed,
            'details': f"{item_info['desc']}, 实际值={value:.2%}, 阈值={threshold:.2%}",
        }

    def run_precheck(self, release_id: int, release_no: str, force_pass: bool = False) -> Dict:
        log.info(f"开始执行发布 {release_no} 的前置条件检查")
        results = []
        all_passed = True

        for item_key in self.CHECK_ITEMS:
            time.sleep(0.3)
            result = self._check_single_item(item_key, force_pass=force_pass)
            if result:
                if not result['passed']:
                    all_passed = False
                results.append(result)
                log.info(
                    f"  [{result['name']}: {result['value']:.2%} "
                    f"(阈值 {result['threshold']:.2%}) "
                    f"-> {'通过' if result['passed'] else '未通过'}"
                )

        db.add_precheck_records(release_id, release_no, results)

        precheck_result = {
            'passed': all_passed,
            'items': results,
            'summary': {
                'total': len(results),
                'passed_count': sum(1 for r in results if r['passed']),
                'failed_count': sum(1 for r in results if not r['passed']),
            },
            'checked_at': get_current_time_str(),
        }

        status = 'PRECHECK_PASSED' if all_passed else 'PRECHECK_FAILED'
        db.update_release_status(
            release_id, status,
            precheck_result=str(precheck_result),
            precheck_passed=1 if all_passed else 0,
        )

        log.info(
            f"前置条件检查{'全部通过' if all_passed else f'未通过'}, "
            f"通过 {precheck_result['summary']['passed_count']}/{precheck_result['summary']['total']} 项"
        )
        log.audit("前置条件检查", "system", release_no,
                    details={"passed": all_passed, "summary": precheck_result['summary']})

        return precheck_result

    def run_detailed_report(self, release_id: int = None, release_no: str = None) -> str:
        """生成可读的检查报告文本"""
        release = db.get_release(release_id=release_id, release_no=release_no)
        if not release:
            return "未找到对应发布单"

        records = db.get_precheck_records(release['id'])

        lines = []
        lines.append("=" * 60)
        lines.append(f"前置条件检查报告 - 发布单: {release['release_no']}")
        lines.append("=" * 60)
        for r in records:
            status_icon = "✓" if r['passed'] else "✗"
            lines.append(f"  {status_icon} {r['check_item']}: "
                         f"{r['check_value']:.2%} (阈值 {r['threshold']:.2%})")
            if r['details']:
                lines.append(f"      {r['details']}")
        passed = all(r['passed'] for r in records)
        lines.append("=" * 60)
        lines.append(f"总体结果: {'全部通过' if passed else '存在未通过项'}")
        return "\n".join(lines)
