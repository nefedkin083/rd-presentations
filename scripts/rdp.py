#!/usr/bin/env python3
"""RD presentations CLI.

  rdp.py build    deck.yaml --out plan/          собрать план (без Google)
  rdp.py finalize plan/ [--media-map map.json]   подставить ссылки Drive → batch_NN.json
  rdp.py direct   plan/ --token token.json [--share email …]   всё сразу своим OAuth
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdp.build import build_plan  # noqa: E402
from rdp import apply  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rdp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("manifest")
    b.add_argument("--out", default="plan")
    f = sub.add_parser("finalize")
    f.add_argument("plan")
    f.add_argument("--media-map")
    d = sub.add_parser("direct")
    d.add_argument("plan")
    d.add_argument("--token", default=os.environ.get("RDP_GOOGLE_TOKEN"))
    d.add_argument("--share", nargs="*", default=[])
    a = ap.parse_args(argv)

    if a.cmd == "build":
        plan = build_plan(a.manifest, a.out)
        print(json.dumps(plan, ensure_ascii=False))
        print(f"plan: {os.path.abspath(a.out)}  (media → {os.path.join(a.out, 'media.json')})")
    elif a.cmd == "finalize":
        for p in apply.finalize(a.plan, a.media_map):
            print(p)
    elif a.cmd == "direct":
        if not a.token:
            sys.exit("нужен --token (или RDP_GOOGLE_TOKEN): JSON с refresh_token, client_id, client_secret")
        print(apply.direct(a.plan, a.token, a.share))


if __name__ == "__main__":
    main()
