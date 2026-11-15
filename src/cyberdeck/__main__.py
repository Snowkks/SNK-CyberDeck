"""Entry point for `python -m cyberdeck`."""

from cyberdeck.app import CyberDeckApp


def main() -> None:
    CyberDeckApp().run()


if __name__ == "__main__":
    main()
