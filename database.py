# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 数据库存储模块
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager

from config import DB_PATH, get_current_time_str, MONITOR_INTERVAL_SECONDS
from logger import log


_lock = threading.Lock()


class Database:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"数据库操作异常: {str(e)}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        with _lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS releases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_no TEXT UNIQUE NOT NULL,
                        version TEXT NOT NULL,
                        risk_level TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        changelog TEXT,
                        submitter TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'CREATED',
                        precheck_result TEXT,
                        precheck_passed INTEGER DEFAULT 0,
                        rollback_version TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        finished_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS approvals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL,
                        release_no TEXT NOT NULL,
                        approver_role TEXT NOT NULL,
                        approver_id TEXT NOT NULL,
                        approver_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        comment TEXT,
                        approved_at TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS gray_release_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL,
                        release_no TEXT NOT NULL,
                        warehouse_id TEXT NOT NULL,
                        gray_group INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        deployed_at TEXT,
                        verified_at TEXT,
                        metrics TEXT,
                        FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS monitor_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL,
                        release_no TEXT NOT NULL,
                        warehouse_id TEXT NOT NULL,
                        check_time TEXT NOT NULL,
                        putaway_error_rate REAL DEFAULT 0,
                        outbound_delay_rate REAL DEFAULT 0,
                        inventory_diff_rate REAL DEFAULT 0,
                        total_orders INTEGER DEFAULT 0,
                        abnormal_orders INTEGER DEFAULT 0,
                        is_abnormal INTEGER DEFAULT 0,
                        alert_details TEXT
                    );

                    CREATE TABLE IF NOT EXISTS rollback_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL,
                        release_no TEXT NOT NULL,
                        trigger_type TEXT NOT NULL,
                        trigger_reason TEXT,
                        rollback_version TEXT NOT NULL,
                        affected_warehouses TEXT,
                        affected_orders INTEGER DEFAULT 0,
                        root_cause TEXT,
                        status TEXT NOT NULL DEFAULT 'ROLLBACK_IN_PROGRESS',
                        report_path TEXT,
                        operator TEXT,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS precheck_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER NOT NULL,
                        release_no TEXT NOT NULL,
                        check_item TEXT NOT NULL,
                        check_value REAL DEFAULT 0,
                        threshold REAL DEFAULT 0,
                        passed INTEGER DEFAULT 0,
                        details TEXT,
                        checked_at TEXT NOT NULL,
                        FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS rollback_drills (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        drill_no TEXT UNIQUE NOT NULL,
                        title TEXT NOT NULL,
                        plan TEXT,
                        target_release_no TEXT,
                        target_warehouses TEXT,
                        status TEXT NOT NULL DEFAULT 'CREATED',
                        execution_log TEXT,
                        report_path TEXT,
                        operator TEXT NOT NULL,
                        result TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS active_monitors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        release_id INTEGER UNIQUE NOT NULL,
                        release_no TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'RUNNING',
                        added_by TEXT,
                        added_at TEXT NOT NULL,
                        last_check_at TEXT,
                        next_check_at TEXT,
                        check_count INTEGER DEFAULT 0
                    );

                    CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);
                    CREATE INDEX IF NOT EXISTS idx_releases_created ON releases(created_at);
                    CREATE INDEX IF NOT EXISTS idx_approvals_release ON approvals(release_id);
                    CREATE INDEX IF NOT EXISTS idx_monitor_release ON monitor_records(release_id);
                    CREATE INDEX IF NOT EXISTS idx_monitor_time ON monitor_records(check_time);
                    CREATE INDEX IF NOT EXISTS idx_rollback_release ON rollback_records(release_id);
                """)
                log.info("数据库初始化完成")

    # ==================== Release 相关 ====================
    def create_release(self, release_no, version, risk_level, title, description,
                       changelog, submitter, rollback_version):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO releases (release_no, version, risk_level, title, description,
                    changelog, submitter, status, rollback_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?)
            """, (release_no, version, risk_level, title, description,
                  changelog, submitter, rollback_version, now, now))
            release_id = cursor.lastrowid
            log.info(f"创建发布单: {release_no}, ID={release_id}")
            log.audit("创建发布单", submitter, release_no,
                      details={"version": version, "risk_level": risk_level})
            return release_id

    def get_release(self, release_id=None, release_no=None):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if release_id:
                cursor.execute("SELECT * FROM releases WHERE id = ?", (release_id,))
            elif release_no:
                cursor.execute("SELECT * FROM releases WHERE release_no = ?", (release_no,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_release_status(self, release_id, status, **kwargs):
        now = get_current_time_str()
        fields = ["status = ?", "updated_at = ?"]
        params = [status, now]
        for k, v in kwargs.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            params.append(v)
        if status in ('RELEASE_SUCCESS', 'ROLLBACK_SUCCESS', 'ROLLBACK_FAILED',
                      'APPROVAL_REJECTED', 'CANCELLED', 'PRECHECK_FAILED'):
            fields.append("finished_at = ?")
            params.append(now)
        params.append(release_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE releases SET {', '.join(fields)} WHERE id = ?", params)
            log.info(f"更新发布单状态 ID={release_id} -> {status}")

    def list_releases(self, status=None, risk_level=None, start_time=None,
                      end_time=None, version=None, limit=100, offset=0):
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if risk_level:
            conditions.append("risk_level = ?")
            params.append(risk_level)
        if start_time:
            conditions.append("created_at >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("created_at <= ?")
            params.append(end_time)
        if version:
            conditions.append("version LIKE ?")
            params.append(f"%{version}%")
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM releases {where_sql}
                ORDER BY id DESC LIMIT ? OFFSET ?
            """, params + [limit, offset])
            return [dict(r) for r in cursor.fetchall()]

    # ==================== Approval 相关 ====================
    def create_approvals(self, release_id, release_no, approval_list):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            for item in approval_list:
                cursor.execute("""
                    INSERT INTO approvals (release_id, release_no, approver_role,
                        approver_id, approver_name, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                """, (release_id, release_no, item['role'], item['id'], item['name'], now))
            log.info(f"为发布单 {release_no} 创建 {len(approval_list)} 条审批记录")

    def get_approvals(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approvals WHERE release_id = ? ORDER BY id", (release_id,))
            return [dict(r) for r in cursor.fetchall()]

    def approve(self, approval_id, approver_id, comment=None, passed=True):
        now = get_current_time_str()
        status = 'APPROVED' if passed else 'REJECTED'
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE approvals SET status = ?, comment = ?, approved_at = ? WHERE id = ?
            """, (status, comment, now, approval_id))
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            row = cursor.fetchone()
            if row:
                row = dict(row)
                log.audit("审批" + ("通过" if passed else "拒绝"),
                          approver_id, row['release_no'],
                          details={"role": row['approver_role'], "comment": comment})
        return status

    def check_all_approved(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM approvals WHERE release_id = ?", (release_id,))
            rows = cursor.fetchall()
            if not rows:
                return True
            return all(dict(r)['status'] == 'APPROVED' for r in rows)

    # ==================== Gray Release 相关 ====================
    def create_gray_records(self, release_id, release_no, warehouse_list):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            for wh in warehouse_list:
                cursor.execute("""
                    INSERT INTO gray_release_records (release_id, release_no, warehouse_id,
                        gray_group, status, deployed_at)
                    VALUES (?, ?, ?, ?, 'PENDING', ?)
                """, (release_id, release_no, wh['id'], wh['gray_group'], now))
            log.info(f"为发布单 {release_no} 创建 {len(warehouse_list)} 条灰度记录")

    def get_gray_records(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM gray_release_records WHERE release_id = ? ORDER BY id",
                           (release_id,))
            return [dict(r) for r in cursor.fetchall()]

    def update_gray_status(self, gray_id, status, **kwargs):
        fields = ["status = ?"]
        params = [status]
        for k, v in kwargs.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            params.append(v)
        params.append(gray_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE gray_release_records SET {', '.join(fields)} WHERE id = ?", params)

    # ==================== Monitor 相关 ====================
    def add_monitor_record(self, release_id, release_no, warehouse_id, metrics):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO monitor_records (release_id, release_no, warehouse_id, check_time,
                    putaway_error_rate, outbound_delay_rate, inventory_diff_rate,
                    total_orders, abnormal_orders, is_abnormal, alert_details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (release_id, release_no, warehouse_id, now,
                  metrics.get('putaway_error_rate', 0),
                  metrics.get('outbound_delay_rate', 0),
                  metrics.get('inventory_diff_rate', 0),
                  metrics.get('total_orders', 0),
                  metrics.get('abnormal_orders', 0),
                  1 if metrics.get('is_abnormal') else 0,
                  json.dumps(metrics.get('alert_details', {}), ensure_ascii=False)))

    def get_monitor_records(self, release_id=None, warehouse_id=None,
                            start_time=None, end_time=None, limit=1000):
        conditions = []
        params = []
        if release_id:
            conditions.append("release_id = ?")
            params.append(release_id)
        if warehouse_id:
            conditions.append("warehouse_id = ?")
            params.append(warehouse_id)
        if start_time:
            conditions.append("check_time >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("check_time <= ?")
            params.append(end_time)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM monitor_records {where_sql}
                ORDER BY id DESC LIMIT ?
            """, params + [limit])
            return [dict(r) for r in cursor.fetchall()]

    # ==================== Rollback 相关 ====================
    def create_rollback_record(self, release_id, release_no, trigger_type,
                               trigger_reason, rollback_version, operator):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rollback_records (release_id, release_no, trigger_type,
                    trigger_reason, rollback_version, status, operator, started_at)
                VALUES (?, ?, ?, ?, ?, 'ROLLBACK_IN_PROGRESS', ?, ?)
            """, (release_id, release_no, trigger_type, trigger_reason,
                  rollback_version, operator, now))
            rollback_id = cursor.lastrowid
            log.audit("触发回滚", operator, release_no,
                      details={"trigger_type": trigger_type, "reason": trigger_reason})
            return rollback_id

    def update_rollback_record(self, rollback_id, **kwargs):
        fields = []
        params = []
        for k, v in kwargs.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            params.append(v)
        if 'status' in kwargs and kwargs['status'] in ('ROLLBACK_SUCCESS', 'ROLLBACK_FAILED'):
            fields.append("finished_at = ?")
            params.append(get_current_time_str())
        params.append(rollback_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE rollback_records SET {', '.join(fields)} WHERE id = ?", params)

    def get_rollback_records(self, release_id=None, start_time=None, end_time=None):
        conditions = []
        params = []
        if release_id:
            conditions.append("release_id = ?")
            params.append(release_id)
        if start_time:
            conditions.append("started_at >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("started_at <= ?")
            params.append(end_time)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM rollback_records {where_sql} ORDER BY id DESC
            """, params)
            return [dict(r) for r in cursor.fetchall()]

    # ==================== Precheck 相关 ====================
    def add_precheck_records(self, release_id, release_no, check_results):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            for item in check_results:
                cursor.execute("""
                    INSERT INTO precheck_records (release_id, release_no, check_item,
                        check_value, threshold, passed, details, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (release_id, release_no, item['item'], item['value'],
                      item['threshold'], 1 if item['passed'] else 0,
                      item.get('details', ''), now))

    def get_precheck_records(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM precheck_records WHERE release_id = ? ORDER BY id",
                           (release_id,))
            return [dict(r) for r in cursor.fetchall()]

    # ==================== Rollback Drill 相关 ====================
    def create_drill(self, drill_no, title, operator, plan=None,
                     target_release_no=None, target_warehouses=None):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rollback_drills (drill_no, title, plan, target_release_no,
                    target_warehouses, status, operator, created_at)
                VALUES (?, ?, ?, ?, ?, 'CREATED', ?, ?)
            """, (drill_no, title,
                  json.dumps(plan, ensure_ascii=False) if plan else None,
                  target_release_no,
                  json.dumps(target_warehouses, ensure_ascii=False) if target_warehouses else None,
                  operator, now))
            drill_id = cursor.lastrowid
            log.audit("创建回滚演练", operator, drill_no, details={"title": title})
            return drill_id

    def update_drill(self, drill_id, **kwargs):
        fields = []
        params = []
        for k, v in kwargs.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            params.append(v)
        if 'status' in kwargs and kwargs['status'] == 'FINISHED':
            fields.append("finished_at = ?")
            params.append(get_current_time_str())
        params.append(drill_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE rollback_drills SET {', '.join(fields)} WHERE id = ?", params)

    def get_drill(self, drill_id=None, drill_no=None):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if drill_id:
                cursor.execute("SELECT * FROM rollback_drills WHERE id = ?", (drill_id,))
            elif drill_no:
                cursor.execute("SELECT * FROM rollback_drills WHERE drill_no = ?", (drill_no,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_drills(self, status=None, start_time=None, end_time=None, limit=100):
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if start_time:
            conditions.append("created_at >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("created_at <= ?")
            params.append(end_time)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM rollback_drills {where_sql} ORDER BY id DESC LIMIT ?
            """, params + [limit])
            return [dict(r) for r in cursor.fetchall()]

    # ==================== Active Monitor 相关 ====================
    def add_active_monitor(self, release_id, release_no, added_by='system'):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO active_monitors (release_id, release_no, status,
                        added_by, added_at, check_count)
                    VALUES (?, ?, 'RUNNING', ?, ?, 0)
                """, (release_id, release_no, added_by, now))
                log.info(f"添加活跃监控: {release_no}, release_id={release_id}")
                return True
            except sqlite3.IntegrityError:
                cursor.execute("""
                    UPDATE active_monitors SET status = 'RUNNING', added_by = ?, added_at = ?
                    WHERE release_id = ?
                """, (added_by, now, release_id))
                log.info(f"活跃监控已存在，重置为运行: {release_no}")
                return True

    def remove_active_monitor(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_monitors WHERE release_id = ?", (release_id,))
            log.info(f"移除活跃监控: release_id={release_id}")
            return cursor.rowcount > 0

    def list_active_monitors(self, status='RUNNING'):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("""
                    SELECT am.*, r.version, r.risk_level, r.status as release_status
                    FROM active_monitors am
                    LEFT JOIN releases r ON am.release_id = r.id
                    WHERE am.status = ?
                    ORDER BY am.id DESC
                """, (status,))
            else:
                cursor.execute("""
                    SELECT am.*, r.version, r.risk_level, r.status as release_status
                    FROM active_monitors am
                    LEFT JOIN releases r ON am.release_id = r.id
                    ORDER BY am.id DESC
                """)
            return [dict(r) for r in cursor.fetchall()]

    def get_active_monitor(self, release_id):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_monitors WHERE release_id = ?", (release_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_monitor_check(self, release_id):
        from datetime import datetime, timedelta
        now = get_current_time_str()
        next_check = (datetime.now() + timedelta(seconds=MONITOR_INTERVAL_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE active_monitors SET last_check_at = ?, next_check_at = ?,
                    check_count = check_count + 1
                WHERE release_id = ?
            """, (now, next_check, release_id))

    def get_monitors_to_check(self):
        now = get_current_time_str()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM active_monitors
                WHERE status = 'RUNNING'
                    AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY id
            """, (now,))
            return [dict(r) for r in cursor.fetchall()]

    # ==================== 统计查询 ====================
    def get_weekly_stats(self, start_date, end_date):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_releases,
                    SUM(CASE WHEN status = 'RELEASE_SUCCESS' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'ROLLBACK_SUCCESS' THEN 1 ELSE 0 END) as rollback_count,
                    SUM(CASE WHEN status = 'APPROVAL_REJECTED' THEN 1 ELSE 0 END) as rejected_count,
                    SUM(CASE WHEN status = 'PRECHECK_FAILED' THEN 1 ELSE 0 END) as precheck_failed_count,
                    SUM(CASE WHEN risk_level = 'normal' THEN 1 ELSE 0 END) as normal_count,
                    SUM(CASE WHEN risk_level = 'emergency' THEN 1 ELSE 0 END) as emergency_count
                FROM releases
                WHERE created_at BETWEEN ? AND ?
            """, (start_date, end_date))
            row = cursor.fetchone()
            stats = dict(row) if row else {}

            cursor.execute("""
                SELECT status, COUNT(*) as cnt
                FROM releases
                WHERE created_at BETWEEN ? AND ?
                GROUP BY status
            """, (start_date, end_date))
            status_stats = {dict(r)['status']: dict(r)['cnt'] for r in cursor.fetchall()}
            stats['status_breakdown'] = status_stats

            cursor.execute("""
                SELECT DATE(created_at) as day, COUNT(*) as cnt
                FROM releases
                WHERE created_at BETWEEN ? AND ?
                GROUP BY DATE(created_at)
                ORDER BY day
            """, (start_date, end_date))
            daily_stats = [dict(r) for r in cursor.fetchall()]
            stats['daily_trend'] = daily_stats

            return stats


db = Database()
