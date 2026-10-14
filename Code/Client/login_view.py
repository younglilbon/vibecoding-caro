"""Tkinter login screen; networking is delegated to the application callback."""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from Code.Shared.constants import DEFAULT_HOST, DEFAULT_PORT


class LoginView(ttk.Frame):
    """Collect a player name and server endpoint without performing socket I/O."""

    def __init__(
        self,
        master: tk.Misc,
        on_login: Callable[[str, str, int], None],
    ) -> None:
        super().__init__(master, style="App.TFrame", padding=(42, 36))
        self.on_login = on_login
        self.name_var = tk.StringVar(self, value="")
        self.host_var = tk.StringVar(self, value=DEFAULT_HOST)
        self.port_var = tk.StringVar(self, value=str(DEFAULT_PORT))
        self.status_var = tk.StringVar(self, value="Disconnected")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text="CARO ONLINE", style="Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Label(self, text="Welcome back", style="Title.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            self,
            text="Sign in with a player name to enter the lobby.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 28))

        ttk.Label(self, text="PLAYER NAME", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, 6)
        )
        self.name_entry = ttk.Entry(self, textvariable=self.name_var, width=34)
        self.name_entry.grid(row=4, column=0, sticky="ew", ipady=7, pady=(0, 20))
        self.name_entry.bind("<Return>", self._on_return)

        endpoint = ttk.Frame(self, style="App.TFrame")
        endpoint.grid(row=5, column=0, sticky="ew")
        endpoint.columnconfigure(0, weight=3)
        endpoint.columnconfigure(1, weight=1)
        ttk.Label(endpoint, text="SERVER", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        ttk.Label(endpoint, text="PORT", style="Field.TLabel").grid(
            row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 6)
        )
        ttk.Entry(endpoint, textvariable=self.host_var).grid(
            row=1, column=0, sticky="ew", ipady=6
        )
        ttk.Entry(endpoint, textvariable=self.port_var, width=8).grid(
            row=1, column=1, sticky="ew", padx=(12, 0), ipady=6
        )

        self.connect_button = ttk.Button(
            self,
            text="Connect to server",
            style="Accent.TButton",
            command=self.submit,
        )
        self.connect_button.grid(row=6, column=0, sticky="ew", pady=(26, 14), ipady=5)
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=7, column=0, sticky="w")

    def submit(self) -> None:
        """Validate the form and hand connection parameters to the app."""
        name = self.name_var.get().strip()
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.set_status("Port must be a number.", error=True)
            return

        if not name:
            self.set_status("Enter a player name.", error=True)
            self.name_entry.focus_set()
            return
        if not host:
            self.set_status("Enter a server address.", error=True)
            return
        if not 1 <= port <= 65535:
            self.set_status("Port must be between 1 and 65535.", error=True)
            return

        self.set_busy(True)
        self.set_status("Connecting...")
        self.on_login(name, host, port)

    def set_busy(self, busy: bool) -> None:
        self.connect_button.configure(state="disabled" if busy else "normal")
        self.name_entry.configure(state="disabled" if busy else "normal")

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_var.set(text)
        self.status_label.configure(style="Error.TLabel" if error else "Status.TLabel")

    def _on_return(self, _event: tk.Event[tk.Misc]) -> str:
        self.submit()
        return "break"