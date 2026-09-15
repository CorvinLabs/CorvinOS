"""TaskEngine command-line interface and server."""

import sys

if __name__ == "__main__":
    # Router for different subcommands
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        # Remove 'server' from argv so argparse sees the rest
        sys.argv.pop(1)
        from .server import main
        main()
    else:
        print("Usage: python -m operator.task_analysis server [options]")
        print("       python -m operator.task_analysis --help")
        sys.exit(1)
