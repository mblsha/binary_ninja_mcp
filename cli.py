#!/usr/bin/env python3
"""
Binary Ninja MCP CLI - Command-line interface for Binary Ninja MCP server
Uses the same HTTP API as the MCP bridge but provides a terminal interface
"""

import json
import sys
from typing import Optional, List
import requests
from plumbum import cli, colors


class BinaryNinjaCLI(cli.Application):
    """Binary Ninja MCP command-line interface"""
    
    PROGNAME = "binja-mcp"
    VERSION = "0.2.7"
    DESCRIPTION = "Command-line interface for Binary Ninja MCP server"
    
    server_url = cli.SwitchAttr(
        ["--server", "-s"],
        str,
        default="http://localhost:9009",
        help="MCP server URL"
    )
    
    json_output = cli.Flag(
        ["--json", "-j"],
        help="Output raw JSON response"
    )
    
    verbose = cli.Flag(
        ["--verbose", "-v"],
        help="Verbose output"
    )
    
    def _request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
        """Make HTTP request to the server"""
        url = f"{self.server_url}/{endpoint}"
        
        if self.verbose:
            print(f"[{method}] {url}", file=sys.stderr)
            if params:
                print(f"Params: {params}", file=sys.stderr)
            if data:
                print(f"Data: {data}", file=sys.stderr)
        
        try:
            if method == "GET":
                response = requests.get(url, params=params, timeout=5)
            else:
                response = requests.post(url, json=data, timeout=5)
            
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.ConnectionError:
            print(colors.red | f"Error: Cannot connect to server at {self.server_url}", file=sys.stderr)
            print("Make sure the MCP server is running in Binary Ninja", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            print(colors.red | f"HTTP Error: {e}", file=sys.stderr)
            if hasattr(e.response, 'text'):
                print(e.response.text, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(colors.red | f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    
    def _output(self, data: dict):
        """Output data in JSON or formatted text"""
        if self.json_output:
            print(json.dumps(data, indent=2))
        else:
            # Custom formatting based on data type
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            elif "success" in data and data.get("success"):
                print(colors.green | "Success!")
                if "message" in data:
                    print(data["message"])
            else:
                # Pretty print the data
                print(json.dumps(data, indent=2))
    
    def main(self):
        """Show help if no subcommand is provided"""
        if len(sys.argv) == 1:
            self.help()
            return 1
        return 0


@BinaryNinjaCLI.subcommand("status")
class Status(cli.Application):
    """Check if binary is loaded and server status"""
    
    def main(self):
        data = self.parent._request("GET", "status")
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            if data.get("loaded"):
                print(colors.green | "✓ Binary loaded")
                print(f"  File: {data.get('filename', 'Unknown')}")
            else:
                print(colors.yellow | "⚠ No binary loaded")


@BinaryNinjaCLI.subcommand("functions")
class Functions(cli.Application):
    """List functions in the binary"""
    
    offset = cli.SwitchAttr(
        ["--offset", "-o"],
        int,
        default=0,
        help="Starting offset for pagination"
    )
    
    limit = cli.SwitchAttr(
        ["--limit", "-l"],
        int,
        default=100,
        help="Maximum number of functions to return"
    )
    
    search = cli.SwitchAttr(
        ["--search", "-s"],
        str,
        help="Search for functions by name"
    )
    
    def main(self):
        if self.search:
            data = self.parent._request("GET", "searchFunctions", {
                "query": self.search,
                "offset": self.offset,
                "limit": self.limit
            })
            
            if self.parent.json_output:
                self.parent._output(data)
            else:
                matches = data.get("matches", [])
                if matches:
                    print(f"Found {len(matches)} matching functions:")
                    for func in matches:
                        print(f"  • {func}")
                else:
                    print("No matching functions found")
        else:
            data = self.parent._request("GET", "functions", {
                "offset": self.offset,
                "limit": self.limit
            })
            
            if self.parent.json_output:
                self.parent._output(data)
            else:
                functions = data.get("functions", [])
                if functions:
                    print(f"Functions ({self.offset}-{self.offset + len(functions)}):")
                    for func in functions:
                        print(f"  • {func}")
                else:
                    print("No functions found")


@BinaryNinjaCLI.subcommand("decompile")
class Decompile(cli.Application):
    """Decompile a function"""
    
    def main(self, function_name: str):
        data = self.parent._request("GET", "decompile", {"name": function_name})
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Decompiled code for {function_name}:")
                print(data.get("decompiled", "No decompilation available"))


@BinaryNinjaCLI.subcommand("assembly")
class Assembly(cli.Application):
    """Get assembly code for a function"""
    
    def main(self, function_name: str):
        data = self.parent._request("GET", "assembly", {"name": function_name})
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Assembly for {function_name}:")
                print(data.get("assembly", "No assembly available"))


@BinaryNinjaCLI.subcommand("rename")
class Rename(cli.Application):
    """Rename functions or data"""


@Rename.subcommand("function")
class RenameFunction(cli.Application):
    """Rename a function"""
    
    def main(self, old_name: str, new_name: str):
        data = self.parent.parent._request("POST", "renameFunction", data={
            "oldName": old_name,
            "newName": new_name
        })
        self.parent.parent._output(data)


@Rename.subcommand("data")
class RenameData(cli.Application):
    """Rename data at address"""
    
    def main(self, address: str, new_name: str):
        data = self.parent.parent._request("POST", "renameData", data={
            "address": address,
            "newName": new_name
        })
        self.parent.parent._output(data)


@BinaryNinjaCLI.subcommand("comment")
class Comment(cli.Application):
    """Manage comments"""
    
    delete = cli.Flag(
        ["--delete", "-d"],
        help="Delete comment instead of setting"
    )
    
    function = cli.Flag(
        ["--function", "-f"],
        help="Comment on function instead of address"
    )
    
    def main(self, target: str, comment: str = None):
        if self.function:
            endpoint = "comment/function"
            params = {"name": target}
        else:
            endpoint = "comment"
            params = {"address": target}
        
        if self.delete:
            params["_method"] = "DELETE"
            data = self.parent._request("POST", endpoint, data=params)
        elif comment is None:
            # Get comment
            data = self.parent._request("GET", endpoint, params)
        else:
            # Set comment
            params["comment"] = comment
            data = self.parent._request("POST", endpoint, data=params)
        
        self.parent._output(data)


@BinaryNinjaCLI.subcommand("refs")
class References(cli.Application):
    """Find code references to a function"""
    
    def main(self, function_name: str):
        data = self.parent._request("GET", "codeReferences", {"function": function_name})
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            refs = json.loads(data) if isinstance(data, str) else data
            if "error" in refs:
                print(colors.red | f"Error: {refs['error']}")
            else:
                code_refs = refs.get("code_references", [])
                if code_refs:
                    print(f"Functions that call {function_name}:")
                    for ref in code_refs:
                        print(f"  • {ref}")
                else:
                    print(f"No references found to {function_name}")


@BinaryNinjaCLI.subcommand("logs")
class Logs(cli.Application):
    """View Binary Ninja logs"""
    
    count = cli.SwitchAttr(
        ["--count", "-c"],
        int,
        default=20,
        help="Number of log entries to show"
    )
    
    level = cli.SwitchAttr(
        ["--level", "-l"],
        str,
        help="Filter by log level (DebugLog, InfoLog, WarningLog, ErrorLog)"
    )
    
    search = cli.SwitchAttr(
        ["--search", "-s"],
        str,
        help="Search in log messages"
    )
    
    errors = cli.Flag(
        ["--errors", "-e"],
        help="Show only errors"
    )
    
    warnings = cli.Flag(
        ["--warnings", "-w"],
        help="Show only warnings"
    )
    
    stats = cli.Flag(
        ["--stats"],
        help="Show log statistics"
    )
    
    clear = cli.Flag(
        ["--clear"],
        help="Clear all logs"
    )
    
    def main(self):
        if self.clear:
            data = self.parent._request("POST", "logs/clear")
            self.parent._output(data)
            return
        
        if self.stats:
            data = self.parent._request("GET", "logs/stats")
            if self.parent.json_output:
                self.parent._output(data)
            else:
                print("Log Statistics:")
                print(f"  Total logs: {data.get('total_logs', 0)}")
                print("  By level:")
                for level, count in data.get('levels', {}).items():
                    print(f"    {level}: {count}")
            return
        
        # Get logs
        params = {"count": self.count}
        endpoint = "logs"
        
        if self.errors:
            endpoint = "logs/errors"
        elif self.warnings:
            endpoint = "logs/warnings"
        else:
            if self.level:
                params["level"] = self.level
            if self.search:
                params["search"] = self.search
        
        data = self.parent._request("GET", endpoint, params)
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            logs = data.get("logs", data.get("errors", data.get("warnings", [])))
            if logs:
                for log in logs:
                    level = log.get("level", "INFO")
                    timestamp = log.get("timestamp", "")[:19]  # Trim microseconds
                    message = log.get("message", "")
                    
                    # Color based on level
                    if "Error" in level:
                        level_color = colors.red
                    elif "Warn" in level:
                        level_color = colors.yellow
                    elif "Debug" in level:
                        level_color = colors.blue
                    else:
                        level_color = colors.white
                    
                    print(f"{colors.dim | timestamp} {level_color | f'[{level:>8}]'} {message}")
            else:
                print("No logs found")


@BinaryNinjaCLI.subcommand("type")
class Type(cli.Application):
    """Get or define types"""
    
    define = cli.Flag(
        ["--define", "-d"],
        help="Define types from C code"
    )
    
    def main(self, type_name_or_code: str):
        if self.define:
            # Define types
            data = self.parent._request("GET", "defineTypes", {"cCode": type_name_or_code})
        else:
            # Get user-defined type
            data = self.parent._request("GET", "getUserDefinedType", {"name": type_name_or_code})
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            result = json.loads(data) if isinstance(data, str) else data
            if "error" in result:
                print(colors.red | f"Error: {result['error']}")
            elif "type_definition" in result:
                print(f"Type: {result.get('type_name', type_name_or_code)}")
                print(result.get('type_definition', 'No definition'))
            else:
                print(result)


@BinaryNinjaCLI.subcommand("imports")
class Imports(cli.Application):
    """List imported symbols"""
    
    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)
    
    def main(self):
        data = self.parent._request("GET", "imports", {
            "offset": self.offset,
            "limit": self.limit
        })
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            imports = data.get("imports", [])
            if imports:
                print(f"Imports ({self.offset}-{self.offset + len(imports)}):")
                for imp in imports:
                    print(f"  • {imp}")
            else:
                print("No imports found")


@BinaryNinjaCLI.subcommand("exports")
class Exports(cli.Application):
    """List exported symbols"""
    
    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)
    
    def main(self):
        data = self.parent._request("GET", "exports", {
            "offset": self.offset,
            "limit": self.limit
        })
        
        if self.parent.json_output:
            self.parent._output(data)
        else:
            exports = data.get("exports", [])
            if exports:
                print(f"Exports ({self.offset}-{self.offset + len(exports)}):")
                for exp in exports:
                    print(f"  • {exp}")
            else:
                print("No exports found")


if __name__ == "__main__":
    BinaryNinjaCLI.run()