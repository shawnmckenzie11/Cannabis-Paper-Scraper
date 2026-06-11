# build_citation_graph.py
"""CLI entry point to build or query the citation graph.

Usage:
    python build_citation_graph.py build [--limit N] [--offset N] [--paper-id ID]
    python build_citation_graph.py stats
    python build_citation_graph.py refs <paper_id>
    python build_citation_graph.py cited-by <paper_id>
    python build_citation_graph.py clear [--paper-id ID]
"""
import sys
import json
import logging
from db_manager import DatabaseManager
from citation_graph import CitationGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    db = DatabaseManager()
    cg = CitationGraph(db)
    cmd = sys.argv[1]

    if cmd == "build":
        kwargs = {}
        for arg in sys.argv[2:]:
            if arg.startswith("--limit="):
                kwargs["limit"] = int(arg.split("=", 1)[1])
            elif arg.startswith("--offset="):
                kwargs["offset"] = int(arg.split("=", 1)[1])
            elif arg.startswith("--paper-id="):
                kwargs["paper_id"] = int(arg.split("=", 1)[1])
            elif arg.startswith("--no-refs"):
                kwargs["include_references"] = False
            elif arg.startswith("--no-citations"):
                kwargs["include_citations"] = False

        if "paper_id" in kwargs:
            pid = kwargs.pop("paper_id")
            logger.info(f"Building graph for single paper {pid}...")
            counts = cg.build_for_paper(pid, **kwargs)
        else:
            logger.info("Building graph for all papers...")
            counts = cg.build_for_all(**kwargs)

        print(json.dumps(counts, indent=2))

    elif cmd == "stats":
        stats = cg.get_graph_stats()
        print(json.dumps(stats, indent=2))

    elif cmd == "refs" and len(sys.argv) >= 3:
        paper_id = int(sys.argv[2])
        include_ext = "--include-external" in sys.argv
        refs = cg.get_references(paper_id, include_external=include_ext)
        print(json.dumps(refs, indent=2))

    elif cmd == "cited-by" and len(sys.argv) >= 3:
        paper_id = int(sys.argv[2])
        include_ext = "--include-external" in sys.argv
        cites = cg.get_cited_by(paper_id, include_external=include_ext)
        print(json.dumps(cites, indent=2))

    elif cmd == "clear":
        paper_id = None
        for arg in sys.argv[2:]:
            if arg.startswith("--paper-id="):
                paper_id = int(arg.split("=", 1)[1])
        if paper_id:
            cg.clear_paper_edges(paper_id)
            logger.info(f"Cleared edges for paper {paper_id}")
        else:
            cg.clear_all_edges()
            logger.info("Cleared all citation edges")

    elif cmd == "connected" and len(sys.argv) >= 3:
        paper_id = int(sys.argv[2])
        depth = 2
        for arg in sys.argv[3:]:
            if arg.startswith("--depth="):
                depth = int(arg.split("=", 1)[1])
        connected = cg.get_connected_papers(paper_id, max_depth=depth)
        print(f"Reachable papers from {paper_id} (depth <= {depth}):")
        for pid, info in sorted(connected.items(),
                                key=lambda x: (x[1]["depth"], x[0])):
            print(f"  depth={info['depth']}: paper {pid}")
        print(f"\nTotal: {len(connected)} papers (including self)")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
