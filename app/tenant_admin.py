"""Local bootstrap, run INSIDE trusted SmartBiz container.

python -m app.tenant_admin create-org --name Acme --email owner@example.com
"""
import argparse
import getpass
from . import db
from .tenant_backend import store as tenant_store


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    org = sub.add_parser('create-org')
    org.add_argument('--name',required=True)
    org.add_argument('--email',required=True)
    args = parser.parse_args()
    pw = getpass.getpass('新管理员密码（至少12位）：')
    check = getpass.getpass('再次输入密码：')
    if pw != check:
        raise SystemExit('两次密码不一致')
    db.initialize(); tenant_store.migrate()
    try:
        created = tenant_store.create_org(args.name, args.email, pw)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print('created organization_id:',created['organization_id'])
    print('created user email:',created['email'])

if __name__ == '__main__':
    main()
