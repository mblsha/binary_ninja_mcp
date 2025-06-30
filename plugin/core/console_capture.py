"""
Binary Ninja Python Console Output Capture for MCP Server
Captures Python console output using the ScriptingOutputListener API
"""

import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional, Any
import binaryninja as bn


class MCPConsoleCapture:
    """Captures Python console output with in-memory storage"""
    
    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.output_buffer = deque(maxlen=max_entries)
        self.lock = threading.RLock()
        self.listener = None
        self.is_registered = False
        self.scripting_instance = None
        
    def start(self):
        """Start capturing console output"""
        if not self.is_registered:
            try:
                # Get the Python scripting provider
                providers = bn.ScriptingProvider.list
                python_provider = None
                
                for provider in providers:
                    if provider.name == "Python" or "python" in provider.name.lower():
                        python_provider = provider
                        break
                        
                if python_provider:
                    # Create a scripting instance
                    self.scripting_instance = python_provider.create_instance()
                    
                    # Create and register our listener
                    self.listener = ConsoleOutputListenerImpl(self)
                    self.scripting_instance.register_output_listener(self.listener)
                    self.is_registered = True
                    
                    # Log that we've started (this will be captured too)
                    print("[MCP] Console capture started")
                else:
                    bn.log_error("[MCP] Could not find Python scripting provider")
                    
            except Exception as e:
                bn.log_error(f"[MCP] Failed to start console capture: {str(e)}")
                
    def stop(self):
        """Stop capturing console output"""
        if self.is_registered and self.listener and self.scripting_instance:
            try:
                self.scripting_instance.unregister_output_listener(self.listener)
                self.is_registered = False
                self.listener = None
                print("[MCP] Console capture stopped")
            except Exception as e:
                bn.log_error(f"[MCP] Failed to stop console capture: {str(e)}")
                
    def add_output(self, output_type: str, text: str):
        """Add console output to the buffer"""
        with self.lock:
            self.output_buffer.append({
                'id': len(self.output_buffer),
                'timestamp': datetime.now().isoformat(),
                'type': output_type,  # 'output', 'error', 'warning', 'input'
                'text': text
            })
            
    def get_output(self, count: int = 100, type_filter: Optional[str] = None,
                   search_text: Optional[str] = None, start_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieve console output with optional filtering
        
        Args:
            count: Maximum number of entries to return
            type_filter: Filter by output type ('output', 'error', 'warning', 'input')
            search_text: Search for text in output
            start_id: Return entries with ID greater than this value (for pagination)
            
        Returns:
            List of console output entries
        """
        with self.lock:
            entries = list(self.output_buffer)
            
            # Apply filters
            if type_filter:
                entries = [entry for entry in entries if entry['type'] == type_filter]
                
            if search_text:
                search_lower = search_text.lower()
                entries = [entry for entry in entries if search_lower in entry['text'].lower()]
                
            if start_id is not None:
                entries = [entry for entry in entries if entry['id'] > start_id]
                
            # Return most recent entries up to count
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
                # Count by type
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
        """
        Execute a Python command in the console and capture output
        
        Args:
            command: Python code to execute
            
        Returns:
            Dict with execution result and any output
        """
        if not self.scripting_instance:
            return {
                'success': False,
                'error': 'Console capture not initialized'
            }
            
        # Mark where we are in the buffer before execution
        with self.lock:
            start_id = len(self.output_buffer) - 1 if self.output_buffer else -1
            
        # Add the input command to our buffer
        self.add_output('input', command)
        
        try:
            # Execute the command
            result = self.scripting_instance.execute_script_input(command)
            
            # Get any output generated during execution
            with self.lock:
                new_output = []
                for entry in self.output_buffer:
                    if entry['id'] > start_id and entry['type'] != 'input':
                        new_output.append(entry)
                        
            return {
                'success': result == bn.ScriptingProviderExecuteResult.SuccessfulScriptExecution,
                'result': result.name if hasattr(result, 'name') else str(result),
                'output': new_output
            }
            
        except Exception as e:
            self.add_output('error', str(e))
            return {
                'success': False,
                'error': str(e),
                'output': []
            }


class ConsoleOutputListenerImpl(bn.ScriptingOutputListener):
    """Implementation of Binary Ninja's ScriptingOutputListener interface"""
    
    def __init__(self, capture: MCPConsoleCapture):
        super().__init__()
        self.capture = capture
        
    def notify_output(self, text: str):
        """Called when normal output is written to the console"""
        if text and "[MCP]" not in text:  # Avoid capturing our own messages
            self.capture.add_output('output', text)
            
    def notify_error(self, text: str):
        """Called when error output is written to the console"""
        if text:
            self.capture.add_output('error', text)
            
    def notify_warning(self, text: str):
        """Called when warning output is written to the console"""
        if text:
            self.capture.add_output('warning', text)
            
    def notify_input_ready_state_changed(self, ready: bool):
        """Called when the console input state changes"""
        # We can use this to track when the console is ready for input
        pass


# Global instance
_console_capture = None


def get_console_capture() -> MCPConsoleCapture:
    """Get the global console capture instance"""
    global _console_capture
    if _console_capture is None:
        _console_capture = MCPConsoleCapture()
    return _console_capture