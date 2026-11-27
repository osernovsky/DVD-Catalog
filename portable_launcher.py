"""Оконная точка входа портативной сборки Windows."""

import ctypes
import sys

from catalog.launch_paths import default_database


def main():
    try:
        from catalog.gui import run_gui

        return run_gui(default_database())
    except Exception as error:
        message = (
            f"Не удалось запустить DVD Catalog.\n{error}\n\n"
            f"Ожидаемый файл базы: {default_database()}\n"
            "Проверьте наличие файла catalog.db рядом с DVD-Catalog.exe и доступ к папке программы."
        )
        ctypes.windll.user32.MessageBoxW(None, message, "DVD Catalog", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(main())
