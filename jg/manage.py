"""Local operator-only administration; never expose this through HTTP."""
import argparse
import json
import os
from pathlib import Path
from .core import Store,inspect_xlsm

def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('file')
    for name in ('member','remove'):
        p=sub.add_parser(name);p.add_argument('--subject',required=True);p.add_argument('--project',required=True)
        if name=='member':
            p.add_argument('--role',choices=['admin','supervisor','estimator','viewer'],required=True)
            p.add_argument('--commercial',action='store_true')
    args=parser.parse_args()
    if args.command=='inspect':print(json.dumps(inspect_xlsm(Path(args.file).read_bytes()),ensure_ascii=False,indent=2));return
    db=Path(os.getenv('JG_DATABASE','data/jg-development.db'));db.parent.mkdir(parents=True,exist_ok=True)
    store=Store(db)
    if args.command=='member':store.add_member(args.subject,args.project,args.role,args.commercial)
    else:
        with store.connect() as c:
            c.execute('DELETE FROM members WHERE subject=? AND project=?',(args.subject,args.project))
            store.log(c,'local-operator',args.project,'remove_member',{'subject':args.subject})
    print('Membresía actualizada en la base local.')

if __name__=='__main__':main()
