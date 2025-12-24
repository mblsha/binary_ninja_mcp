import binaryninja as bn
from .core.config import Config
from .server.http_server import MCPServer


class BinaryNinjaMCP:
    def __init__(self):
        self.config = Config()
        self.server = MCPServer(self.config)

    def start_server(self, bv):
        try:
            self.server.binary_ops.current_view = bv
            started = self.server.start()
            if started:
                bn.log_info(
                    f"MCP server started successfully on http://{self.config.server.host}:{self.config.server.port}"
                )
        except Exception as e:
            bn.log_error(f"Failed to start MCP server: {str(e)}")

    def stop_server(self, bv):
        try:
            self.server.binary_ops.current_view = None
            stopped = self.server.stop()
            if stopped:
                bn.log_info("Binary Ninja MCP plugin stopped successfully")
        except Exception as e:
            bn.log_error(f"Failed to stop server: {str(e)}")


plugin = BinaryNinjaMCP()

# Auto-start the server if configured
if plugin.config.server.auto_start:
    try:
        # Start server without a specific binary view
        started = plugin.server.start()
        if started:
            bn.log_info(
                f"MCP server auto-started on http://{plugin.config.server.host}:{plugin.config.server.port}"
            )
    except Exception as e:
        bn.log_error(f"Failed to auto-start MCP server: {str(e)}")
else:
    bn.log_info(
        "MCP server auto-start disabled. Use 'MCP Server > Start MCP Server' to start manually."
    )

bn.PluginCommand.register(
    "MCP Server\\Start MCP Server",
    "Start the Binary Ninja MCP server",
    plugin.start_server,
)

bn.PluginCommand.register(
    "MCP Server\\Stop MCP Server",
    "Stop the Binary Ninja MCP server",
    plugin.stop_server,
)


# Register callback to update binary view when files are opened
def on_binary_opened(bv):
    """Automatically update the MCP server with the newly opened binary view"""
    if plugin.server and hasattr(plugin.server, "binary_ops"):
        plugin.server.binary_ops.current_view = bv
        bn.log_info(f"MCP server updated with binary view: {bv.file.filename}")


# Register the callback for when binaries are opened
bn.BinaryViewType.add_binaryview_initial_analysis_completion_event(on_binary_opened)

bn.log_info("Binary Ninja MCP plugin loaded successfully")
