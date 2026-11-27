from services.log_store import install_crash_logging

# Before anything else is imported, so a failure while the app is still
# starting up reaches the log too.
install_crash_logging()

from ui.main_window import MainWindow  # noqa: E402


def main():
    app = MainWindow()
    app.run()


if __name__ == "__main__":
    main()
