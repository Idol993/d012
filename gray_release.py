# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 灰度发布模块
按照仓库灰度策略逐步推送新版本
"""

import time
from typing import List, Dict, Callable, Optional

from config import WAREHOUSES, GRAY_STRATEGY, RISK_LEVELS, get_current_time_str
from logger import log
from database import db


class GrayReleaseEngine:
    """灰度发布引擎"""

    def __init__(self):
        pass

    def prepare_gray_plan(self, release_id: int, release_no: str,
                          risk_level: str, version: str = None) -> Dict:
        """
        根据风险级别准备灰度发布计划
        """
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"未知风险级别: {risk_level}")

        gray_required = RISK_LEVELS[risk_level]['gray_required']

        warehouse_list = []
        for wh_id, wh_info in WAREHOUSES.items():
            warehouse_list.append({
                'id': wh_id,
                'name': wh_info['name'],
                'region': wh_info['region'],
                'priority': wh_info['priority'],
                'gray_group': wh_info['gray_group'],
            })

        warehouse_list.sort(key=lambda x: (x['gray_group'], x['priority']))

        db.create_gray_records(release_id, release_no, warehouse_list)

        groups = {}
        for wh in warehouse_list:
            g = wh['gray_group']
            if g not in groups:
                groups[g] = []
            groups[g].append(wh)

        if not gray_required:
            for gr in db.get_gray_records(release_id):
                db.update_gray_status(
                    gr['id'], 'FULL_DEPLOYED',
                    deployed_at=get_current_time_str(),
                    metrics={'deploy_type': 'emergency', 'version': version},
                )
            log.info(f"紧急发布 {release_no}，跳过灰度，全量部署到 {len(warehouse_list)} 个仓库")
            return {
                'gray_required': False,
                'groups': [],
                'warehouses': warehouse_list,
                'total_warehouses': len(warehouse_list),
                'message': '紧急发布，跳过灰度，直接全量发布',
            }

        plan = {
            'gray_required': True,
            'observation_minutes': GRAY_STRATEGY['observation_minutes'],
            'max_fail_rate': GRAY_STRATEGY['max_fail_rate'],
            'groups': [
                {
                    'group_id': g,
                    'warehouses': groups[g],
                    'warehouse_count': len(groups[g]),
                }
                for g in sorted(groups.keys())
            ],
            'total_warehouses': len(warehouse_list),
            'total_groups': len(groups),
        }

        log.info(
            f"灰度发布计划已生成: 共 {plan['total_groups']} 组, "
            f"{plan['total_warehouses']} 个仓库, "
            f"每组观察期 {plan['observation_minutes']} 分钟"
        )
        for g in plan['groups']:
            log.info(
                f"  灰度组 {g['group_id']}: "
                + ", ".join([f"{w['id']}({w['name']})" for w in g['warehouses']])
            )

        return plan

    def deploy_to_group(self, release_id: int, release_no: str,
                        group_id: int, version: str) -> Dict:
        """
        部署到指定灰度组
        """
        records = db.get_gray_records(release_id)
        target_records = [r for r in records if r['gray_group'] == group_id]

        if not target_records:
            return {'success': False, 'message': f'未找到灰度组 {group_id} 的记录'}

        deployed = []
        for r in target_records:
            wh_info = WAREHOUSES.get(r['warehouse_id'], {})
            log.info(
                f"[部署] 正在将版本 {version} 部署到仓库 "
                f"{r['warehouse_id']}({wh_info.get('name', '')})"
            )
            time.sleep(0.5)
            db.update_gray_status(
                r['id'], 'DEPLOYED',
                deployed_at=get_current_time_str(),
                metrics={'deploy_version': version},
            )
            deployed.append(r['warehouse_id'])
            log.info(f"[部署] 仓库 {r['warehouse_id']} 部署成功")

        return {
            'success': True,
            'group_id': group_id,
            'deployed_warehouses': deployed,
            'message': f'灰度组 {group_id} 部署完成，共 {len(deployed)} 个仓库',
        }

    def verify_group(self, release_id: int, release_no: str,
                     group_id: int, monitor_metrics: Dict = None) -> Dict:
        """
        验证指定灰度组的发布结果
        """
        records = db.get_gray_records(release_id)
        target_records = [r for r in records if r['gray_group'] == group_id]

        all_ok = True
        verified_warehouses = []

        for r in target_records:
            wh_info = WAREHOUSES.get(r['warehouse_id'], {})
            metrics = monitor_metrics or {
                'putaway_error_rate': 0.005,
                'outbound_delay_rate': 0.01,
                'inventory_diff_rate': 0.002,
                'status': 'healthy',
            }

            is_healthy = metrics.get('status') == 'healthy'
            if not is_healthy:
                all_ok = False

            db.update_gray_status(
                r['id'],
                'VERIFIED' if is_healthy else 'FAILED',
                verified_at=get_current_time_str(),
                metrics=metrics,
            )
            verified_warehouses.append({
                'warehouse_id': r['warehouse_id'],
                'warehouse_name': wh_info.get('name', ''),
                'healthy': is_healthy,
                'metrics': metrics,
            })

            log.info(
                f"[验证] 仓库 {r['warehouse_id']}({wh_info.get('name', '')}) "
                f"验证结果: {'健康' if is_healthy else '异常'}, "
                f"上架错误率={metrics.get('putaway_error_rate', 0):.2%}, "
                f"出库延迟率={metrics.get('outbound_delay_rate', 0):.2%}, "
                f"库存差异率={metrics.get('inventory_diff_rate', 0):.2%}"
            )

        return {
            'success': all_ok,
            'group_id': group_id,
            'verified_warehouses': verified_warehouses,
            'all_healthy': all_ok,
            'message': f'灰度组 {group_id} 验证{"全部通过" if all_ok else "存在异常"}',
        }

    def execute_full_gray_release(self, release_id: int, release_no: str,
                                  version: str,
                                  on_group_deployed: Optional[Callable] = None,
                                  on_group_verified: Optional[Callable] = None,
                                  on_abnormal: Optional[Callable] = None) -> Dict:
        """
        执行完整的灰度发布流程
        """
        release = db.get_release(release_id=release_id)
        if not release:
            return {'success': False, 'message': '发布单不存在'}

        plan = self.prepare_gray_plan(release_id, release_no, release['risk_level'], version)

        if not plan['gray_required']:
            log.info(f"紧急发布 {release_no}，跳过灰度，直接全量发布")
            db.update_release_status(release_id, 'FULL_RELEASE')
            all_warehouses = list(WAREHOUSES.keys())
            return {
                'success': True,
                'gray_skipped': True,
                'deployed_warehouses': all_warehouses,
                'message': '紧急发布完成',
            }

        db.update_release_status(release_id, 'GRAY_PROGRESS')

        results = []
        all_success = True
        all_deployed = []

        for group in plan['groups']:
            group_id = group['group_id']
            log.info(f"{'=' * 50}")
            log.info(f"开始部署灰度组 {group_id}: "
                     + ", ".join([w['name'] for w in group['warehouses']]))

            deploy_result = self.deploy_to_group(
                release_id, release_no, group_id, version
            )
            if not deploy_result['success']:
                all_success = False
                if on_abnormal:
                    on_abnormal(release_id, release_no, 'DEPLOY_FAILED', deploy_result)
                break

            all_deployed.extend(deploy_result['deployed_warehouses'])
            if on_group_deployed:
                on_group_deployed(release_id, release_no, group_id, deploy_result)

            log.info(f"灰度组 {group_id} 部署完成，进入观察期...")
            time.sleep(0.2)

            verify_result = self.verify_group(release_id, release_no, group_id)
            results.append(verify_result)

            if on_group_verified:
                on_group_verified(release_id, release_no, group_id, verify_result)

            if not verify_result['all_healthy']:
                all_success = False
                log.warning(f"灰度组 {group_id} 验证失败，终止灰度发布")
                if on_abnormal:
                    on_abnormal(release_id, release_no, 'VERIFY_FAILED', verify_result)
                break

            log.info(f"灰度组 {group_id} 验证通过，继续下一组")

        if all_success:
            db.update_release_status(release_id, 'FULL_RELEASE')
            log.info(f"发布单 {release_no} 全部灰度组部署验证通过，进入全量发布")
            db.update_release_status(release_id, 'RELEASE_SUCCESS')
            final_status = 'RELEASE_SUCCESS'
        else:
            final_status = 'GRAY_FAILED'

        return {
            'success': all_success,
            'results': results,
            'deployed_warehouses': all_deployed,
            'final_status': final_status,
            'message': '灰度发布完成' if all_success else '灰度发布过程中出现异常',
        }
