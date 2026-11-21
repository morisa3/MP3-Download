"""Dialog lifecycle for Flet 0.21.2."""

def open_dialog(page, dialog):
    page.dialog = dialog
    dialog.open = True
    page.update()


def close_dialog(page, dialog):
    dialog.open = False
    page.update()
