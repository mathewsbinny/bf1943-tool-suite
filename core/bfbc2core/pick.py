"""A small level picker for the suite's 3D tools, shown when they are started without a level (a double-click,
or `python studio.py` alone): the levels of your BF1943 copy (game1943.find_game), double-click or OK to open one.

    lv = pick_level('BF1943 Navigation Studio')     # the level name, or None when cancelled
"""
import os

from . import game1943


def levels():
    """[(level, state)]: each level of the game copy, and whether NavKit has built its navigation (work folder)."""
    out = []
    for lv in game1943.levels():
        built = os.path.isfile(os.path.join(game1943.level_work(lv), 'navkit', f'{lv}.hpd'))
        kind = 'multiplayer map' if lv in game1943.MP_LEVELS else 'other level'
        out.append((lv, kind + (', navigation built' if built else '')))
    return out


def pick_level(title):
    import tkinter as tk
    from tkinter import messagebox, ttk
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # sharp on scaled displays
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.title(title)
    root.minsize(460, 420)
    try:
        ttk.Style().theme_use('vista')
    except tk.TclError:
        pass
    try:
        items = levels()
    except Exception as e:
        messagebox.showerror(title, f'No BF1943 game copy found: {e}')
        root.destroy()
        return None
    chosen = []
    ttk.Label(root, text='Choose a level to open:', padding=(10, 10, 10, 4)).pack(anchor='w')
    tree = ttk.Treeview(root, columns=('state',), selectmode='browse', height=16)
    tree.heading('#0', text='Level')
    tree.heading('state', text='')
    tree.column('#0', width=140)
    tree.column('state', width=300)
    for lv, state in items:
        tree.insert('', 'end', iid=lv, text=lv, values=(state,))
    tree.pack(fill='both', expand=True, padx=10)

    def ok(*_):
        sel = tree.selection()
        if sel:
            chosen.append(sel[0])
            root.destroy()
    tree.bind('<Double-1>', ok)
    tree.bind('<Return>', ok)
    b = ttk.Frame(root, padding=10)
    b.pack(fill='x')
    ttk.Button(b, text='Open', command=ok).pack(side='right')
    ttk.Button(b, text='Cancel', command=root.destroy).pack(side='right', padx=6)
    root.mainloop()
    return chosen[0] if chosen else None
