"""Tk tests for login form validation and its callback boundary."""

import tkinter as tk

from Code.Client.login_view import LoginView


def test_login_view_passes_credentials_without_network_work(tk_root: tk.Tk) -> None:
    root = tk_root
    submitted: list[tuple[str, str, int]] = []
    view = LoginView(root, lambda name, host, port: submitted.append((name, host, port)))
    view.pack()
    view.name_var.set("  Alice  ")
    view.host_var.set("localhost")
    view.port_var.set("43210")

    view.submit()

    assert submitted == [("Alice", "localhost", 43210)]
    assert str(view.connect_button["state"]) == "disabled"


def test_login_view_rejects_invalid_port_without_callback(tk_root: tk.Tk) -> None:
    root = tk_root
    submitted: list[tuple[str, str, int]] = []
    view = LoginView(root, lambda name, host, port: submitted.append((name, host, port)))
    view.port_var.set("not-a-port")

    view.submit()

    assert submitted == []
    assert view.status_var.get() == "Port must be a number."
    assert str(view.connect_button["state"]) == "normal"