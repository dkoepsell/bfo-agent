"""Entry point: start the Flask orchestrator."""
from app import config
from app.orchestrator import create_app


def main():
    app = create_app()
    print(f"BFO-Agent starting on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print(f"BFO path:     {config.BFO_PATH}")
    print(f"Working path: {config.WORKING_PATH}")
    # threaded=True so the web UI (and /jobs/<id>/progress polling) stays
    # responsive while the server-side feed thread holds the module _lock and
    # drives the reasoner back-to-back; the single-threaded default served one
    # request at a time and starved every UI poll during a hot feed.
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False,
            threaded=True)


if __name__ == "__main__":
    main()
