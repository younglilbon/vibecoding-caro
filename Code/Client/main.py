"""Launch the Tkinter GameCaroOnline client."""

import tkinter as tk

from Code.Client.app import App


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()