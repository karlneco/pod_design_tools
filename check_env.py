#!/usr/bin/env python
"""
Diagnostic script to check Python environment for testing.
Run this in PyCharm terminal to verify your setup.
"""
import sys
import os

print("=" * 60)
print("Python Environment Diagnostic")
print("=" * 60)
print()

print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")
print(f"Python path: {sys.path[0]}")
print()

print("Virtual Environment:")
venv_path = os.environ.get('VIRTUAL_ENV', 'Not activated')
print(f"  VIRTUAL_ENV: {venv_path}")
print()

print("Checking test dependencies:")
packages = [
    'pytest',
    'pytest-flask',
    'pytest-cov',
    'pytest-mock',
    'respx',
    'httpx',
]

for package in packages:
    try:
        mod = __import__(package.replace('-', '_'))
        version = getattr(mod, '__version__', 'unknown')
        print(f"  ✓ {package}: {version}")
    except ImportError:
        print(f"  ✗ {package}: NOT INSTALLED")

print()
print("PyCharm Detection:")
print(f"  PYCHARM_HOSTED: {os.environ.get('PYCHARM_HOSTED', 'No')}")
print(f"  IDE: {os.environ.get('IDE', 'No')}")
print()

# Check if we're in the right directory
print("Working Directory:")
print(f"  {os.getcwd()}")
print()

# Check if .venv exists
venv_exists = os.path.exists('.venv')
print(f".venv directory exists: {venv_exists}")

if venv_path != 'Not activated':
    expected_venv = os.path.join(os.getcwd(), '.venv')
    if venv_path == expected_venv:
        print("✓ Using correct virtual environment")
    else:
        print(f"⚠ WARNING: Virtual environment mismatch!")
        print(f"  Expected: {expected_venv}")
        print(f"  Actual:   {venv_path}")
else:
    print("⚠ WARNING: No virtual environment activated")

print()
print("=" * 60)
print("Recommendation:")
if venv_path == 'Not activated':
    print("  Activate your virtual environment:")
    print("  source .venv/bin/activate")
elif 'site-packages' not in sys.path[0]:
    print("  PyCharm may not be using the correct interpreter")
    print("  Go to: Settings → Project → Python Interpreter")
    print("  Select: Python 3.12.2 (.venv)")
else:
    print("  Environment looks good!")
    print("  If tests still fail, try: pip install --upgrade respx")
print("=" * 60)
