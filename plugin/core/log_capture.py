"""
Binary Ninja Log Capture for MCP Server
Captures Binary Ninja log messages using file redirection
"""

import threading
import os
import tempfile
import time
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
        self.is_registered = False
        self.log_file_path = None
        self.monitor_thread = None
        self.stop_monitoring = threading.Event()
        
    def start(self):
        """Start capturing logs"""
        if not self.is_registered:
            try:
                # Create a temporary log file
                fd, self.log_file_path = tempfile.mkstemp(suffix='.log', prefix='binja_mcp_')
                os.close(fd)  # Close the file descriptor, we'll open it separately
                
                # Redirect Binary Ninja logs to our file
                bn.log_to_file(bn.LogLevel.DebugLog, self.log_file_path, append=True)
                
                # Start monitoring thread
                self.stop_monitoring.clear()
                self.monitor_thread = threading.Thread(target=self._monitor_log_file)
                self.monitor_thread.daemon = True
                self.monitor_thread.start()
                
                self.is_registered = True
                bn.log_info("[MCP] Log capture started")
                
            except Exception as e:
                bn.log_error(f"[MCP] Failed to start log capture: {str(e)}")
                if self.log_file_path and os.path.exists(self.log_file_path):
                    os.unlink(self.log_file_path)
                self.log_file_path = None
            
    def stop(self):
        """Stop capturing logs"""
        if self.is_registered:
            self.is_registered = False
            
            # Stop monitoring thread
            if self.monitor_thread:
                self.stop_monitoring.set()
                self.monitor_thread.join(timeout=1.0)
                self.monitor_thread = None
            
            # Clean up log file
            if self.log_file_path and os.path.exists(self.log_file_path):
                try:
                    os.unlink(self.log_file_path)
                except:
                    pass
                self.log_file_path = None
                
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
    
    def _monitor_log_file(self):
        """Monitor the log file for new entries"""
        if not self.log_file_path:
            return
            
        # Wait a bit for file to be created
        time.sleep(0.1)
        
        try:
            # Open file in read mode
            with open(self.log_file_path, 'r') as f:
                # Move to end of file
                f.seek(0, 2)
                
                while not self.stop_monitoring.is_set():
                    # Read new lines
                    line = f.readline()
                    if line:
                        # Parse log line
                        self._parse_log_line(line.strip())
                    else:
                        # No new data, wait a bit
                        time.sleep(0.1)
                        
        except Exception as e:
            bn.log_error(f"[MCP] Error monitoring log file: {str(e)}")
            
    def _parse_log_line(self, line: str):
        """Parse a log line and add it to the buffer"""
        if not line or "[MCP]" in line:  # Skip our own logs
            return
            
        # Binary Ninja log format is typically: [LEVEL] message
        # or sometimes includes thread/session info
        
        level = "InfoLog"  # Default
        message = line
        
        # Try to extract log level
        if line.startswith('['):
            end = line.find(']')
            if end > 0:
                level_str = line[1:end].upper()
                message = line[end+1:].strip()
                
                # Map to Binary Ninja log levels
                if 'DEBUG' in level_str:
                    level = "DebugLog"
                elif 'INFO' in level_str:
                    level = "InfoLog"
                elif 'WARN' in level_str:
                    level = "WarningLog"
                elif 'ERROR' in level_str:
                    level = "ErrorLog"
                elif 'ALERT' in level_str:
                    level = "AlertLog"
        
        # Add to buffer
        self.add_log(0, level, message, "", threading.current_thread().ident)


# Global instance
_log_capture = None


def get_log_capture() -> MCPLogCapture:
    """Get the global log capture instance"""
    global _log_capture
    if _log_capture is None:
        _log_capture = MCPLogCapture()
    return _log_capture