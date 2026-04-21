"""Entry point: start the Flask orchestrator."""
from app import config
from app.orchestrator import create_app


def main():
    app = create_app()
    print(f"BFO-Agent starting on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print(f"BFO path:     {config.BFO_PATH}")
    print(f"Working path: {config.WORKING_PATH}")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False)


if __name__ == "__main__":
    main()
