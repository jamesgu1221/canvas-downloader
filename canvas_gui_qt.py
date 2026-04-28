import sys


def main() -> None:
    if "--canvas-dl-cli" in sys.argv:
        sys.argv.remove("--canvas-dl-cli")
        from canvas_dl.__main__ import main as cli_main

        sys.exit(cli_main())

    from canvas_dl.gui_qt.__main__ import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
