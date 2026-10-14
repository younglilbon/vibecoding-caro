"""Shared Tk interpreter for tests in environments with fragile Tcl teardown."""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def _tk_session_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_root(_tk_session_root):
    yield _tk_session_root
    for child in _tk_session_root.winfo_children():
        child.destroy()
    _tk_session_root.update_idletasks()
    import gc
    gc.collect()