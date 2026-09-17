"""Run only from a trusted terminal with DATABASE_URL. Never print pending posts in public CI logs."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import engine
from fan_community import messages
from sqlalchemy import select, update

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['pending', 'approve', 'reject'])
parser.add_argument('--id')
args = parser.parse_args()
with engine.begin() as conn:
    if args.action == 'pending':
        rows=conn.execute(select(messages.c.id,messages.c.nickname,messages.c.body,messages.c.status,messages.c.report_reason)
                         .where(messages.c.status.in_(['pending','reported'])).order_by(messages.c.created_at).limit(100)).mappings()
        print(json.dumps([dict(r) for r in rows],ensure_ascii=False,indent=2))
    else:
        if not args.id: parser.error('--id is required')
        result=conn.execute(update(messages).where(messages.c.id==args.id,messages.c.status.in_(['pending','reported']))
                            .values(status='approved' if args.action=='approve' else 'rejected',reviewed_at=int(time.time())))
        if result.rowcount != 1: raise SystemExit('No pending/reported message matched; nothing changed')
        print('Review saved. Public API reflects the decision immediately.')
