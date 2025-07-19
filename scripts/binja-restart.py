#!/usr/bin/env python3
"""
Advanced Binary Ninja control script with enhanced monitoring and API integration capabilities.
"""
import subprocess
import time
import os
import tempfile
from pathlib import Path
from typing import Optional
from plumbum import cli

class BinaryNinjaAdvancedController(cli.Application):
    """Advanced Binary Ninja restart with enhanced monitoring capabilities."""
    
    VERSION = "2.0.0"
    
    DEFAULT_BINJA_PATH = "Binary Ninja"  # Use open -a to find it
    
    timeout = cli.SwitchAttr(
        ["-t", "--timeout"],
        int,
        default=60,
        help="Overall timeout in seconds"
    )
    
    binja_path = cli.SwitchAttr(
        ["-b", "--binja-path"],
        str,
        default=DEFAULT_BINJA_PATH,
        help="Path to Binary Ninja application"
    )
    
    window_wait = cli.SwitchAttr(
        ["-w", "--window-wait"],
        int,
        default=30,
        help="Timeout for window detection"
    )
    
    stabilization_time = cli.SwitchAttr(
        ["-s", "--stabilization-time"],
        int,
        default=5,
        help="Additional wait time after window appears"
    )
    
    force = cli.Flag(
        ["-f", "--force"],
        help="Force kill without graceful quit"
    )
    
    verbose = cli.Flag(
        ["-v", "--verbose"],
        help="Enable verbose output"
    )
    
    monitor_interval = cli.SwitchAttr(
        ["--monitor-interval"],
        float,
        default=0.5,
        help="Interval between monitoring checks"
    )
    
    create_startup_script = cli.Flag(
        ["--startup-script"],
        help="Create a startup script for Binary Ninja (experimental)"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.temp_script = None
        self.start_time = None
    
    def log(self, message: str, level: str = "INFO"):
        """Enhanced logging with levels."""
        if self.verbose or level in ["ERROR", "WARNING"]:
            timestamp = time.strftime('%H:%M:%S')
            print(f"[{timestamp}] {level}: {message}")
    
    def elapsed_time(self) -> float:
        """Get elapsed time since start."""
        if self.start_time:
            return time.time() - self.start_time
        return 0.0
    
    def kill_existing(self) -> bool:
        """Kill any existing Binary Ninja instances."""
        self.log("Checking for existing Binary Ninja instances...")
        
        # Check if running
        if not self._is_running():
            self.log("No existing instances found")
            return True
        
        if not self.force:
            self.log("Attempting graceful quit...")
            try:
                subprocess.run([
                    "osascript", "-e", 
                    'tell application "Binary Ninja" to quit'
                ], capture_output=True, timeout=5)
                
                # Try to handle save dialog if it appears
                time.sleep(0.5)
                subprocess.run([
                    "osascript", "-e",
                    '''tell application "System Events" to tell process "Binary Ninja"
                        repeat 3 times
                            if exists sheet 1 of window 1 then
                                if exists button "Don't Save" of sheet 1 of window 1 then
                                    click button "Don't Save" of sheet 1 of window 1
                                    delay 0.5
                                end if
                            end if
                        end repeat
                    end tell'''
                ], capture_output=True, timeout=2)
                
                # Wait for graceful quit
                for i in range(5):
                    time.sleep(1)
                    if not self._is_running():
                        self.log(f"Gracefully quit after {i+1}s")
                        return True
            except subprocess.TimeoutExpired:
                self.log("Graceful quit timed out", "WARNING")
        
        # Force kill
        self.log("Force killing Binary Ninja...")
        # Use pkill with full path pattern to only kill .app bundle processes
        subprocess.run([
            "pkill", "-f", "Binary Ninja\\.app/Contents/MacOS/binaryninja"
        ], capture_output=True)
        time.sleep(1)
        
        if self._is_running():
            self.log("Failed to kill Binary Ninja", "ERROR")
            return False
        
        self.log("Successfully killed Binary Ninja")
        return True
    
    def _is_running(self) -> bool:
        """Check if Binary Ninja is running from a valid .app bundle."""
        try:
            # Get all processes named binaryninja
            result = subprocess.run(
                ["pgrep", "-x", "binaryninja"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                return False
                
            # Check each PID to verify it's from an .app bundle
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid:
                    # Get the full path of the process
                    ps_result = subprocess.run(
                        ["ps", "-p", pid, "-o", "comm="],
                        capture_output=True,
                        text=True
                    )
                    if ps_result.returncode == 0:
                        path = ps_result.stdout.strip()
                        # Check if it's from a valid .app bundle
                        if ("/Binary Ninja.app/Contents/MacOS/binaryninja" in path or
                            path.endswith("/Contents/MacOS/binaryninja")):
                            return True
            
            return False
        except Exception:
            return False
    
    def _get_window_name(self) -> Optional[str]:
        """Get current Binary Ninja window name."""
        try:
            result = subprocess.run([
                "osascript", "-e",
                'tell application "System Events" to tell process "Binary Ninja" '
                'to get name of window 1'
            ], capture_output=True, text=True, timeout=2)
            
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        
        return None
    
    
    def create_binja_startup_script(self, file_path: str) -> str:
        """Create a temporary Python script for Binary Ninja startup."""
        script_content = f'''
# Binary Ninja Startup Script
import binaryninja
import time

print("[STARTUP] Opening file: {file_path}")
bv = binaryninja.open_view("{file_path}")

if bv:
    print(f"[STARTUP] Successfully opened: {{bv.file.filename}}")
    print(f"[STARTUP] View type: {{bv.view_type}}")
    print("[STARTUP] Waiting for initial analysis...")
    bv.update_analysis_and_wait()
    print("[STARTUP] Analysis complete")
    print(f"[STARTUP] Functions found: {{len(bv.functions)}}")
else:
    print("[STARTUP] Failed to open file")
'''
        
        fd, temp_path = tempfile.mkstemp(suffix=".py", prefix="binja_startup_")
        with open(temp_path, 'w') as f:
            f.write(script_content)
        
        self.temp_script = temp_path
        self.log(f"Created startup script: {temp_path}")
        return temp_path
    
    def launch_binja(self, file_path: Optional[str] = None) -> bool:
        """Launch Binary Ninja with optional file."""
        if file_path:
            self.log(f"Launching Binary Ninja with: {file_path}")
            
            if self.create_startup_script and False:  # Disabled for now
                # This would require Binary Ninja CLI support
                startup_script = self.create_binja_startup_script(file_path)
                cmd = ["open", "-a", self.binja_path, "--args", file_path, "-s", startup_script]
            else:
                cmd = ["open", "-a", self.binja_path, file_path]
        else:
            self.log("Launching Binary Ninja without file")
            cmd = ["open", "-a", self.binja_path]
        
        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            self.log(f"Failed to launch Binary Ninja: {e}", "ERROR")
            return False
    
    def monitor_startup(self, file_name: Optional[str] = None) -> bool:
        """Monitor Binary Ninja startup progress."""
        self.log(f"Monitoring startup (window timeout: {self.window_wait}s)")
        
        # Phase 1: Wait for window
        window_found = False
        window_start = time.time()
        
        while time.time() - window_start < self.window_wait:
            window_name = self._get_window_name()
            
            if window_name:
                self.log(f"Window detected: {window_name}")
                if file_name:
                    if file_name.lower() in window_name.lower():
                        window_found = True
                        print(f"✓ Window opened: {window_name}")
                        break
                else:
                    # No file specified, any window is good
                    window_found = True
                    print(f"✓ Window opened: {window_name}")
                    break
            
            if not self.verbose:
                print(".", end="", flush=True)
            time.sleep(self.monitor_interval)
        
        if not self.verbose:
            print()
        
        if not window_found:
            self.log("Window detection failed", "ERROR")
            return False
        
        # Phase 2: Additional wait time
        if self.stabilization_time > 0:
            self.log(f"Waiting {self.stabilization_time}s for stabilization...")
            time.sleep(self.stabilization_time)
            print("✓ Stabilization wait complete")
        
        return True
    
    def cleanup(self, retcode=0):
        """Clean up temporary resources."""
        if self.temp_script and os.path.exists(self.temp_script):
            os.unlink(self.temp_script)
            self.log("Cleaned up temporary script")
    
    def main(self, file_path: str = None):
        """Main entry point."""
        self.start_time = time.time()
        
        # Verify file exists if provided
        if file_path and not Path(file_path).exists():
            print(f"Error: File not found: {file_path}")
            return 1
        
        # Skip path verification when using app name
        if "/" in self.binja_path and not Path(self.binja_path).exists():
            print(f"Error: Binary Ninja not found at: {self.binja_path}")
            return 1
        
        file_name = Path(file_path).name if file_path else None
        
        print("=" * 50)
        print("Binary Ninja Advanced Controller")
        if file_path:
            print(f"File: {file_path}")
        else:
            print("No file specified - opening Binary Ninja only")
        print(f"Binary Ninja: {self.binja_path}")
        print("=" * 50)
        
        try:
            # Kill existing instances
            if not self.kill_existing():
                return 1
            
            # Launch Binary Ninja
            if not self.launch_binja(file_path):
                return 1
            
            # Monitor startup
            if self.monitor_startup(file_name):
                # Bring to front
                try:
                    subprocess.run([
                        "osascript", "-e",
                        'tell application "Binary Ninja" to activate'
                    ], capture_output=True)
                except Exception:
                    pass
                
                print(f"\n✓ Success! Total time: {self.elapsed_time():.1f}s")
                return 0
            else:
                print(f"\n✗ Failed after {self.elapsed_time():.1f}s")
                return 1
                
        finally:
            self.cleanup()

if __name__ == "__main__":
    BinaryNinjaAdvancedController.run()