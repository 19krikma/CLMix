import tkinter as tk
from tkinter import messagebox

class MainWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("My Desktop Application")
        self.root.geometry("600x400")

        self.create_widgets()

    def create_widgets(self):
        title = tk.Label(
            self.root,
            text="Welcome to My Application",
            font=("Arial", 16)
        )
        title.pack(pady=20)

        button = tk.Button(
            self.root,
            text="Click Me",
            command=self.show_message
        )
        button.pack(pady=10)

    def show_message(self):
        messagebox.showinfo("Information", "Button clicked!")

    def run(self):
        self.root.mainloop()
