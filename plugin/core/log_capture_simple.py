"""
Simple Binary Ninja Log Capture for MCP Server
Fallback implementation that doesn't rely on LogListener
"""

import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional, Any
import binaryninja as bn


class SimpleLogCapture:
    """Simple log capture that intercepts log function calls"""

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.logs = deque(maxlen=max_entries)
        self.lock = threading.RLock()
        self.is_registered = False
        self.original_functions = {}

    def start(self):
        """Start capturing logs by patching log functions"""
        if not self.is_registered:
            try:
                # Store original functions
                self.original_functions = {
                    "log_info": bn.log_info,
                    "log_debug": bn.log_debug,
                    "log_warn": bn.log_warn,
                    "log_error": bn.log_error,
                    "log_alert": bn.log_alert,
                }

                # Replace with our interceptors
                bn.log_info = self._make_interceptor("InfoLog", self.original_functions["log_info"])
                bn.log_debug = self._make_interceptor(
                    "DebugLog", self.original_functions["log_debug"]
                )
                bn.log_warn = self._make_interceptor(
                    "WarningLog", self.original_functions["log_warn"]
                )
                bn.log_error = self._make_interceptor(
                    "ErrorLog", self.original_functions["log_error"]
                )
                bn.log_alert = self._make_interceptor(
                    "AlertLog", self.original_functions["log_alert"]
                )

                self.is_registered = True
                self.original_functions["log_info"]("[MCP] Simple log capture started")

            except Exception as e:
                print(f"[MCP] Failed to start simple log capture: {str(e)}")

    def stop(self):
        """Stop capturing logs"""
        if self.is_registered:
            self.is_registered = False

            # Restore original functions
            if self.original_functions:
                bn.log_info = self.original_functions.get("log_info", bn.log_info)
                bn.log_debug = self.original_functions.get("log_debug", bn.log_debug)
                bn.log_warn = self.original_functions.get("log_warn", bn.log_warn)
                bn.log_error = self.original_functions.get("log_error", bn.log_error)
                bn.log_alert = self.original_functions.get("log_alert", bn.log_alert)

            self.original_functions["log_info"]("[MCP] Simple log capture stopped")

    def _make_interceptor(self, level: str, original_func):
        """Create an interceptor function for a log level"""

        def interceptor(message: str, logger: str = ""):
            # Add to our buffer
            if "[MCP]" not in message:  # Skip our own logs
                self.add_log(0, level, message, logger, threading.current_thread().ident)
            # Call original function
            return original_func(message, logger)

        return interceptor

    def add_log(self, session: int, level: str, message: str, logger_name: str = "", tid: int = 0):
        """Add a log entry to the buffer"""
        with self.lock:
            self.logs.append(
                {
                    "id": len(self.logs),
                    "timestamp": datetime.now().isoformat(),
                    "level": level,
                    "message": message,
                    "logger": logger_name,
                    "thread_id": tid,
                    "session": session,
                }
            )

    def get_logs(
        self,
        count: int = 100,
        level_filter: Optional[str] = None,
        search_text: Optional[str] = None,
        start_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve logs with optional filtering"""
        with self.lock:
            logs = list(self.logs)

            if level_filter:
                logs = [log for log in logs if log["level"] == level_filter]

            if search_text:
                search_lower = search_text.lower()
                logs = [log for log in logs if search_lower in log["message"].lower()]

            if start_id is not None:
                logs = [log for log in logs if log["id"] > start_id]

            return logs[-count:]

    def get_log_stats(self) -> Dict[str, Any]:
        """Get statistics about captured logs"""
        with self.lock:
            if not self.logs:
                return {
                    "total_logs": 0,
                    "levels": {},
                    "loggers": {},
                    "oldest_timestamp": None,
                    "newest_timestamp": None,
                }

            level_counts = {}
            logger_counts = {}

            for log in self.logs:
                level = log["level"]
                level_counts[level] = level_counts.get(level, 0) + 1
                logger = log["logger"] or "default"
                logger_counts[logger] = logger_counts.get(logger, 0) + 1

            return {
                "total_logs": len(self.logs),
                "levels": level_counts,
                "loggers": logger_counts,
                "oldest_timestamp": self.logs[0]["timestamp"],
                "newest_timestamp": self.logs[-1]["timestamp"],
            }

    def clear_logs(self):
        """Clear all captured logs"""
        with self.lock:
            self.logs.clear()
            if self.original_functions and "log_info" in self.original_functions:
                self.original_functions["log_info"]("[MCP] Log buffer cleared")

    def get_latest_errors(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent error logs"""
        return self.get_logs(count=count, level_filter="ErrorLog")

    def get_latest_warnings(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent warning logs"""
        return self.get_logs(count=count, level_filter="WarningLog")
