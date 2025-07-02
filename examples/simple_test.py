print("Hello from Binary Ninja!")
print(f"2 + 2 = {2 + 2}")

# Access Binary Ninja context
if 'bv' in globals() and bv:
    print(f"Binary: {bv.file.filename if bv.file else 'No file'}")
else:
    print("No binary loaded")