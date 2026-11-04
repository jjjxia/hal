#!/usr/bin/env python3
"""Run close_chain.py unchanged with a larger Python recursion limit and thread stack (E5 amendment m): Python's ast
visitor recurses once per nesting level of an expression, and the flat program of a large netlist can exceed the
default limit (RecursionError). Same arguments as close_chain.py; nothing else changes."""
import os, runpy, sys, threading

sys.setrecursionlimit(2_000_000)
threading.stack_size(1 << 30)
err = []


def run():
    try:
        sys.argv = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "close_chain.py")] + sys.argv[1:]
        runpy.run_path(sys.argv[0], run_name="__main__")
    except SystemExit as e:
        err.append(e.code)
    except BaseException as e:
        import traceback; traceback.print_exc(); err.append(1)


t = threading.Thread(target=run); t.start(); t.join()
sys.exit(err[0] if err else 0)
