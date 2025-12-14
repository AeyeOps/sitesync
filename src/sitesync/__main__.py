"""Entry point for `python -m sitesync`."""

from sitesync.cli.app import app


def main() -> None:
    """Invoke the CLI application."""

    app()


if __name__ == "__main__":
    main()
