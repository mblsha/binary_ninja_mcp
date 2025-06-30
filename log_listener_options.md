# Log Listener Implementation Options for Binary Ninja MCP

## Option 1: Simple In-Memory Buffer

**Approach**: Store all logs in a Python list with thread-safe access.

```python
import threading
from collections import deque
from datetime import datetime
import binaryninja as bn

class SimpleLogCapture(bn.LogListener):
    def __init__(self, max_entries=10000):
        super().__init__()
        self.logs = deque(maxlen=max_entries)
        self.lock = threading.RLock()
        bn.log.register_log_listener(self)
        
    def log_message(self, session, level, msg, logger_name="", tid=0):
        with self.lock:
            self.logs.append({
                'timestamp': datetime.now().isoformat(),
                'level': level.name,
                'message': msg,
                'logger': logger_name,
                'thread_id': tid,
                'session': session
            })
    
    def get_logs(self, count=100, level_filter=None):
        with self.lock:
            logs = list(self.logs)
            if level_filter:
                logs = [l for l in logs if l['level'] == level_filter]
            return logs[-count:]
```

**Pros**: 
- Simple implementation
- Fast access
- Low overhead

**Cons**: 
- Limited history (memory constrained)
- Lost on restart
- No persistence

## Option 2: Rotating File-Based Logger

**Approach**: Write logs to rotating files with in-memory cache for recent entries.

```python
import json
import os
from pathlib import Path
import threading
from collections import deque

class FileBasedLogCapture(bn.LogListener):
    def __init__(self, log_dir="mcp_logs", max_file_size=10*1024*1024):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.current_file = None
        self.file_lock = threading.Lock()
        self.memory_cache = deque(maxlen=1000)  # Recent logs
        self.max_file_size = max_file_size
        self._rotate_if_needed()
        bn.log.register_log_listener(self)
        
    def log_message(self, session, level, msg, logger_name="", tid=0):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'level': level.name,
            'message': msg,
            'logger': logger_name,
            'thread_id': tid,
            'session': session
        }
        
        # Add to memory cache
        self.memory_cache.append(entry)
        
        # Write to file
        with self.file_lock:
            self._rotate_if_needed()
            with open(self.current_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
    
    def _rotate_if_needed(self):
        if not self.current_file or self.current_file.stat().st_size > self.max_file_size:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.current_file = self.log_dir / f"bninja_{timestamp}.log"
    
    def get_logs(self, count=100, include_history=False):
        if not include_history:
            return list(self.memory_cache)[-count:]
        
        # Read from files
        logs = []
        for log_file in sorted(self.log_dir.glob("*.log"), reverse=True):
            with open(log_file) as f:
                for line in f:
                    logs.append(json.loads(line))
                    if len(logs) >= count:
                        return logs
        return logs
```

**Pros**: 
- Persistent storage
- Can handle large volumes
- Survives restarts
- Can implement log rotation

**Cons**: 
- More complex
- File I/O overhead
- Need to manage disk space

## Option 3: Hybrid Memory + SQLite

**Approach**: Use SQLite for persistent storage with in-memory cache for performance.

```python
import sqlite3
import threading
from contextlib import contextmanager

class SQLiteLogCapture(bn.LogListener):
    def __init__(self, db_path="mcp_logs.db", cache_size=1000):
        super().__init__()
        self.db_path = db_path
        self.cache = deque(maxlen=cache_size)
        self.lock = threading.Lock()
        self._init_db()
        bn.log.register_log_listener(self)
        
    def _init_db(self):
        with self._db() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    level TEXT,
                    message TEXT,
                    logger TEXT,
                    thread_id INTEGER,
                    session INTEGER,
                    INDEX idx_timestamp (timestamp),
                    INDEX idx_level (level)
                )
            ''')
            
            # Clean old logs (keep last 100k)
            conn.execute('''
                DELETE FROM logs WHERE id IN (
                    SELECT id FROM logs 
                    ORDER BY id DESC 
                    LIMIT -1 OFFSET 100000
                )
            ''')
    
    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def log_message(self, session, level, msg, logger_name="", tid=0):
        entry = (
            datetime.now().isoformat(),
            level.name,
            msg,
            logger_name,
            tid,
            session
        )
        
        # Add to cache
        self.cache.append({
            'timestamp': entry[0],
            'level': entry[1],
            'message': entry[2],
            'logger': entry[3],
            'thread_id': entry[4],
            'session': entry[5]
        })
        
        # Async write to DB (could be done in background thread)
        with self.lock:
            with self._db() as conn:
                conn.execute(
                    'INSERT INTO logs (timestamp, level, message, logger, thread_id, session) VALUES (?, ?, ?, ?, ?, ?)',
                    entry
                )
    
    def get_logs(self, count=100, level_filter=None, search_text=None):
        # Try cache first for recent logs
        if count <= len(self.cache) and not level_filter and not search_text:
            return list(self.cache)[-count:]
        
        # Query database
        query = 'SELECT * FROM logs WHERE 1=1'
        params = []
        
        if level_filter:
            query += ' AND level = ?'
            params.append(level_filter)
            
        if search_text:
            query += ' AND message LIKE ?'
            params.append(f'%{search_text}%')
            
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(count)
        
        with self._db() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()][::-1]
```

**Pros**: 
- Persistent and queryable
- Good performance with caching
- Advanced filtering capabilities
- Structured data storage

**Cons**: 
- Most complex implementation
- SQLite dependency
- Potential locking issues

## Option 4: Ring Buffer with Streaming

**Approach**: Fixed-size ring buffer with WebSocket/SSE streaming capability.

```python
import asyncio
from typing import List, Optional, Callable

class StreamingLogCapture(bn.LogListener):
    def __init__(self, buffer_size=5000):
        super().__init__()
        self.buffer = [None] * buffer_size
        self.write_pos = 0
        self.wrapped = False
        self.lock = threading.Lock()
        self.subscribers: List[Callable] = []
        bn.log.register_log_listener(self)
        
    def log_message(self, session, level, msg, logger_name="", tid=0):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'level': level.name,
            'message': msg,
            'logger': logger_name,
            'thread_id': tid,
            'session': session
        }
        
        with self.lock:
            self.buffer[self.write_pos] = entry
            self.write_pos = (self.write_pos + 1) % len(self.buffer)
            if self.write_pos == 0:
                self.wrapped = True
            
            # Notify subscribers
            for subscriber in self.subscribers:
                try:
                    subscriber(entry)
                except Exception:
                    pass
    
    def get_logs(self, count=100):
        with self.lock:
            if not self.wrapped and self.write_pos < count:
                # Haven't filled buffer yet
                return [e for e in self.buffer[:self.write_pos] if e is not None]
            
            # Calculate start position
            total_logs = len(self.buffer) if self.wrapped else self.write_pos
            start_offset = max(0, total_logs - count)
            
            if self.wrapped:
                start_pos = (self.write_pos + start_offset) % len(self.buffer)
                if start_pos < self.write_pos:
                    return self.buffer[start_pos:self.write_pos]
                else:
                    return self.buffer[start_pos:] + self.buffer[:self.write_pos]
            else:
                return self.buffer[start_offset:self.write_pos]
    
    def subscribe(self, callback):
        with self.lock:
            self.subscribers.append(callback)
    
    def unsubscribe(self, callback):
        with self.lock:
            self.subscribers.remove(callback)
```

**Pros**: 
- Fixed memory usage
- Real-time streaming capability
- Efficient for recent logs
- Good for monitoring

**Cons**: 
- Limited history
- Old logs are lost
- More complex retrieval logic

## Option 5: Python Console Capture

**Approach**: Capture Python stdout/stderr separately from Binary Ninja logs.

```python
import sys
import io
import contextlib

class PythonConsoleCapture:
    def __init__(self):
        self.output_buffer = deque(maxlen=10000)
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.capturing = False
        
    def start_capture(self):
        if not self.capturing:
            sys.stdout = self.OutputInterceptor(self.original_stdout, self.output_buffer, 'stdout')
            sys.stderr = self.OutputInterceptor(self.original_stderr, self.output_buffer, 'stderr')
            self.capturing = True
    
    def stop_capture(self):
        if self.capturing:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.capturing = False
    
    class OutputInterceptor(io.TextIOBase):
        def __init__(self, original, buffer, stream_type):
            self.original = original
            self.buffer = buffer
            self.stream_type = stream_type
            
        def write(self, text):
            if text and text != '\n':
                self.buffer.append({
                    'timestamp': datetime.now().isoformat(),
                    'stream': self.stream_type,
                    'text': text
                })
            return self.original.write(text)
        
        def flush(self):
            return self.original.flush()
    
    def get_output(self, count=100):
        return list(self.output_buffer)[-count:]
```

**Pros**: 
- Captures Python print statements
- Separate from Binary Ninja logs
- Can distinguish stdout/stderr

**Cons**: 
- Only captures Python output
- May interfere with other output redirection
- Need to handle thread safety

## Recommendation

For the MCP server, I recommend **Option 3 (Hybrid Memory + SQLite)** with **Option 5 (Python Console Capture)** as a complementary feature. This provides:

1. Persistent storage for debugging
2. Fast access to recent logs via cache
3. Advanced querying capabilities
4. Separate Python console output tracking
5. Reasonable complexity vs functionality trade-off

The implementation would add these endpoints to the MCP server:
- `/logs` - Get recent logs with filtering
- `/logs/search` - Search logs by text
- `/logs/clear` - Clear log history
- `/console/python` - Get Python console output
- `/console/clear` - Clear Python console buffer