"""
Binary Ninja Log Capture for MCP Server
Captures Binary Ninja log messages using the LogListener API
"""

import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional, Any
import binaryninja as bn


class MCPLogCapture:
    """Captures Binary Ninja log messages with in-memory storage"""
    
    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.logs = deque(maxlen=max_entries)
        self.lock = threading.RLock()
        self.listener = None
        self.is_registered = False
        
    def start(self):
        """Start capturing logs"""
        if not self.is_registered:
            self.listener = LogListenerImpl(self)
            bn.log.register_log_listener(self.listener)
            self.is_registered = True
            bn.log_info("[MCP] Log capture started")
            
    def stop(self):
        """Stop capturing logs"""
        if self.is_registered and self.listener:
            bn.log.unregister_log_listener(self.listener)
            self.is_registered = False
            self.listener = None
            bn.log_info("[MCP] Log capture stopped")
            
    def add_log(self, session: int, level: str, message: str, logger_name: str = "", tid: int = 0):
        """Add a log entry to the buffer"""
        with self.lock:
            self.logs.append({
                'id': len(self.logs),
                'timestamp': datetime.now().isoformat(),
                'level': level,
                'message': message,
                'logger': logger_name,
                'thread_id': tid,
                'session': session
            })
            
    def get_logs(self, count: int = 100, level_filter: Optional[str] = None, 
                 search_text: Optional[str] = None, start_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieve logs with optional filtering
        
        Args:
            count: Maximum number of logs to return
            level_filter: Filter by log level (e.g., "ErrorLog", "WarningLog")
            search_text: Search for text in messages
            start_id: Return logs with ID greater than this value (for pagination)
            
        Returns:
            List of log entries
        """
        with self.lock:
            logs = list(self.logs)
            
            # Apply filters
            if level_filter:
                logs = [log for log in logs if log['level'] == level_filter]
                
            if search_text:
                search_lower = search_text.lower()
                logs = [log for log in logs if search_lower in log['message'].lower()]
                
            if start_id is not None:
                logs = [log for log in logs if log['id'] > start_id]
                
            # Return most recent logs up to count
            return logs[-count:]
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get statistics about captured logs"""
        with self.lock:
            if not self.logs:
                return {
                    'total_logs': 0,
                    'levels': {},
                    'loggers': {},
                    'oldest_timestamp': None,
                    'newest_timestamp': None
                }
                
            level_counts = {}
            logger_counts = {}
            
            for log in self.logs:
                # Count by level
                level = log['level']
                level_counts[level] = level_counts.get(level, 0) + 1
                
                # Count by logger
                logger = log['logger'] or 'default'
                logger_counts[logger] = logger_counts.get(logger, 0) + 1
                
            return {
                'total_logs': len(self.logs),
                'levels': level_counts,
                'loggers': logger_counts,
                'oldest_timestamp': self.logs[0]['timestamp'],
                'newest_timestamp': self.logs[-1]['timestamp']
            }
            
    def clear_logs(self):
        """Clear all captured logs"""
        with self.lock:
            self.logs.clear()
            bn.log_info("[MCP] Log buffer cleared")
            
    def get_latest_errors(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent error logs"""
        return self.get_logs(count=count, level_filter="ErrorLog")
        
    def get_latest_warnings(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent warning logs"""
        return self.get_logs(count=count, level_filter="WarningLog")


class LogListenerImpl(bn.LogListener):
    """Implementation of Binary Ninja's LogListener interface"""
    
    def __init__(self, capture: MCPLogCapture):
        super().__init__()
        self.capture = capture
        
    def log_message(self, session: int, level: bn.LogLevel, msg: str, logger_name: str = "", tid: int = 0):
        """Called by Binary Ninja when a log message is generated"""
        # Convert LogLevel enum to string
        level_str = level.name if hasattr(level, 'name') else str(level)
        
        # Don't capture our own log messages to avoid recursion
        if "[MCP]" not in msg:
            self.capture.add_log(session, level_str, msg, logger_name, tid)
            
    def close_log(self):
        """Called when the log is being closed"""
        self.capture.add_log(0, "InfoLog", "[MCP] Log listener closed", "mcp", 0)
        
    def get_log_level(self) -> bn.LogLevel:
        """Return the minimum log level we want to capture"""
        # Capture all log levels
        return bn.LogLevel.DebugLog


# Global instance
_log_capture = None


def get_log_capture() -> MCPLogCapture:
    """Get the global log capture instance"""
    global _log_capture
    if _log_capture is None:
        _log_capture = MCPLogCapture()
    return _log_capture