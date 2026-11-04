"""Start the API with this checkout's bundled news engine, from any directory."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--root-path", default="", help="Public URL prefix when serving behind a proxy.")
    parser.add_argument("--check-imports", action="store_true",
                        help="Verify the bundled engine without starting services or making requests.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    engine = root / "news_engine"
    sys.path[:0] = [str(root / "api"), str(engine)]

    import headline_model
    import news_agent
    for package in (headline_model, news_agent):
        if not Path(package.__file__).resolve().is_relative_to(engine):
            raise RuntimeError("The API must load the news engine bundled with this checkout.")
    if args.check_imports:
        print("Bundled news engine verified.")
        return

    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, root_path=args.root_path)


if __name__ == "__main__":
    main()
