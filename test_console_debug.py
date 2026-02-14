#!/usr/bin/env python3
"""
Debug script to understand console capture initialization issues
"""

import sys

try:
    import binaryninja as bn
except ModuleNotFoundError:
    print("SKIP: binaryninja module is not available in this Python environment.")
    sys.exit(0)

print("=== Binary Ninja Console Capture Debug ===")
print(f"Binary Ninja Version: {bn.core_version()}")
print(f"Python Version: {sys.version}")

# Check for ScriptingProvider
print("\n--- Checking ScriptingProvider ---")
try:
    providers = bn.ScriptingProvider.list
    print(f"Available ScriptingProviders: {len(providers)}")
    for i, provider in enumerate(providers):
        print(f"  [{i}] Name: {provider.name}")
        print(f"      Instance Type: {provider.instance_class}")
        print(f"      API Version: {provider.api_version}")
except Exception as e:
    print(f"Error accessing ScriptingProvider.list: {e}")

# Try to find Python provider
print("\n--- Looking for Python Provider ---")
try:
    python_provider = None
    for provider in bn.ScriptingProvider.list:
        if provider.name == "Python" or "python" in provider.name.lower():
            python_provider = provider
            print(f"Found Python provider: {provider.name}")
            break

    if not python_provider:
        print("No Python provider found!")
    else:
        # Try to create instance
        print("\n--- Creating Scripting Instance ---")
        try:
            instance = python_provider.create_instance()
            print(f"Successfully created instance: {instance}")
            print(f"Instance type: {type(instance)}")

            # Check available methods
            print("\nInstance methods:")
            for attr in dir(instance):
                if not attr.startswith("_"):
                    print(f"  - {attr}")

            # Try to register a dummy listener
            print("\n--- Testing Output Listener ---")
            try:

                class TestListener(bn.ScriptingOutputListener):
                    def __init__(self):
                        super().__init__()
                        self.messages = []

                    def notify_output(self, text):
                        self.messages.append(("output", text))
                        print(f"[Listener] Output: {text}")

                    def notify_error(self, text):
                        self.messages.append(("error", text))
                        print(f"[Listener] Error: {text}")

                listener = TestListener()
                instance.register_output_listener(listener)
                print("Successfully registered output listener")

                # Try to execute something
                print("\n--- Testing Script Execution ---")
                test_code = "print('Hello from console test!')"
                result = instance.execute_script_input(test_code)
                print(f"Execution result: {result}")
                print(f"Result type: {type(result)}")
                print(f"Captured messages: {listener.messages}")

                # Unregister
                instance.unregister_output_listener(listener)
                print("Successfully unregistered listener")

            except Exception as e:
                print(f"Error with output listener: {e}")
                import traceback

                traceback.print_exc()

        except Exception as e:
            print(f"Error creating scripting instance: {e}")
            import traceback

            traceback.print_exc()

except Exception as e:
    print(f"Error in main flow: {e}")
    import traceback

    traceback.print_exc()

print("\n=== Debug Complete ===")
