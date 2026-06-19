# -*- coding: utf-8 -*-
"""
智能仓储 WMS 系统 - 全链路日志管理模块
"""

import os
import json
import uuid
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOG_FILE, AUDIT_LOG_FILE, get_current_time_str


class TraceContext:
    _local = threading.local()

    @classmethod
    def get_trace_id(cls):
        if not hasattr(cls._local, 'trace_id'):
            cls._local.trace_id = str(uuid.uuid4())[:16]
        return cls._local.trace_id

    @classmethod
    def set_trace_id(cls, trace_id):
        cls._local.trace_id = trace_id

    @classmethod
    def new_trace_id(cls):
        cls._local.trace_id = str(uuid.uuid4())[:16]
        return cls._local.trace_id


class WMSLogger:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._setup_logger()
        self._setup_audit_logger()

    def _setup_logger(self):
        self.logger = logging.getLogger('wms_release')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        log_dir = os.path.dirname(LOG_FILE)
        os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=50 * 1024 * 1024,
            backupCount=20,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | trace_id=%(trace_id)s | %(module)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def _setup_audit_logger(self):
        self.audit_logger = logging.getLogger('wms_audit')
        self.audit_logger.setLevel(logging.INFO)
        self.audit_logger.propagate = False

        audit_dir = os.path.dirname(AUDIT_LOG_FILE)
        os.makedirs(audit_dir, exist_ok=True)

        audit_handler = RotatingFileHandler(
            AUDIT_LOG_FILE,
            maxBytes=100 * 1024 * 1024,
            backupCount=50,
            encoding='utf-8'
        )
        audit_handler.setLevel(logging.INFO)

        audit_formatter = logging.Formatter(
            '%(asctime)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        audit_handler.setFormatter(audit_formatter)
        self.audit_logger.addHandler(audit_handler)

    STANDARD_KEYS = {'exc_info', 'stack_info', 'stacklevel', 'extra'}

    def _split_kwargs(self, kwargs):
        standard = {}
        custom = {}
        for k, v in kwargs.items():
            if k in self.STANDARD_KEYS:
                standard[k] = v
            else:
                custom[k] = v
        extra = {'trace_id': TraceContext.get_trace_id()}
        extra.update(custom)
        standard['extra'] = extra
        return standard

    def debug(self, message, **kwargs):
        self.logger.debug(message, **self._split_kwargs(kwargs))

    def info(self, message, **kwargs):
        self.logger.info(message, **self._split_kwargs(kwargs))

    def warning(self, message, **kwargs):
        self.logger.warning(message, **self._split_kwargs(kwargs))

    def error(self, message, **kwargs):
        self.logger.error(message, **self._split_kwargs(kwargs))

    def critical(self, message, **kwargs):
        self.logger.critical(message, **self._split_kwargs(kwargs))

    def audit(self, action, operator, target, details=None, status='success', **kwargs):
        audit_record = {
            'timestamp': get_current_time_str(),
            'trace_id': TraceContext.get_trace_id(),
            'action': action,
            'operator': operator,
            'target': target,
            'status': status,
            'details': details or {},
            'extra': kwargs
        }
        self.audit_logger.info(json.dumps(audit_record, ensure_ascii=False))
        self.info(
            f"[审计] {action} | 操作人={operator} | 目标={target} | 状态={status}",
            **kwargs
        )

    def new_trace(self):
        return TraceContext.new_trace_id()

    def set_trace(self, trace_id):
        TraceContext.set_trace_id(trace_id)
        return trace_id


log = WMSLogger()
