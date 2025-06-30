"""
Simple Python Console Output Capture for MCP Server
Fallback implementation that doesn't require ScriptingOutputListener
"""

import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional, Any
import binaryninja as bn


class SimpleConsoleCapture:
    """Simple console capture - returns empty results as Binary Ninja doesn't expose console capture in Python API"""
    
    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.output_buffer = deque(maxlen=max_entries)
        self.lock = threading.RLock()
        self.is_registered = False
        
    def start(self):
        """Start capturing console output"""
        if not self.is_registered:
            self.is_registered = True
            # Add a message to indicate console capture is not available
            self.add_output('warning', '[MCP] Console capture is not available in this Binary Ninja version')
            bn.log_info("[MCP] Simple console capture started (no actual capture available)")
                
    def stop(self):
        """Stop capturing console output"""
        if self.is_registered:
            self.is_registered = False
            bn.log_info("[MCP] Simple console capture stopped")
            
    def add_output(self, output_type: str, text: str):
        """Add console output to the buffer"""
        with self.lock:
            self.output_buffer.append({
                'id': len(self.output_buffer),
                'timestamp': datetime.now().isoformat(),
                'type': output_type,
                'text': text
            })
            
    def get_output(self, count: int = 100, type_filter: Optional[str] = None,
                   search_text: Optional[str] = None, start_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve console output with optional filtering"""
        with self.lock:
            entries = list(self.output_buffer)
            
            if type_filter:
                entries = [entry for entry in entries if entry['type'] == type_filter]
                
            if search_text:
                search_lower = search_text.lower()
                entries = [entry for entry in entries if search_lower in entry['text'].lower()]
                
            if start_id is not None:
                entries = [entry for entry in entries if entry['id'] > start_id]
                
            return entries[-count:]
            
    def get_console_stats(self) -> Dict[str, Any]:
        """Get statistics about captured console output"""
        with self.lock:
            if not self.output_buffer:
                return {
                    'total_entries': 0,
                    'types': {},
                    'oldest_timestamp': None,
                    'newest_timestamp': None
                }
                
            type_counts = {}
            total_chars = 0
            
            for entry in self.output_buffer:
                entry_type = entry['type']
                type_counts[entry_type] = type_counts.get(entry_type, 0) + 1
                total_chars += len(entry['text'])
                
            return {
                'total_entries': len(self.output_buffer),
                'types': type_counts,
                'total_characters': total_chars,
                'oldest_timestamp': self.output_buffer[0]['timestamp'],
                'newest_timestamp': self.output_buffer[-1]['timestamp']
            }
            
    def clear_output(self):
        """Clear all captured console output"""
        with self.lock:
            self.output_buffer.clear()
            bn.log_info("[MCP] Console buffer cleared")
            
    def get_latest_errors(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent error output"""
        return self.get_output(count=count, type_filter="error")
        
    def execute_command(self, command: str) -> Dict[str, Any]:
        """Execute a Python command in the console"""
        # Since we can't actually capture console output, we'll just indicate this
        return {
            'success': False,
            'error': 'Console command execution is not available in this Binary Ninja version',
            'output': []
        }