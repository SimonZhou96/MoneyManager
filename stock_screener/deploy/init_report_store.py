"""Initialize report mappings and generate index-scoped application credentials.

Run with PYTHONPATH=. from stock_screener. Credentials are generated into a
private runtime file, never printed. The application does not use the admin key.
"""
import argparse
import os
from pathlib import Path
import shlex

from elasticsearch import Elasticsearch

from task_reports import ElasticsearchReportRepository, REPORT_INDEX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--ca', type=Path, required=True)
    parser.add_argument('--admin-password-file', type=Path, required=True)
    parser.add_argument('--credentials-file', type=Path, required=True)
    args = parser.parse_args()
    # Refuse to replace credentials: rerunning must not invalidate another API.
    if args.credentials_file.exists():
        parser.error('credentials file already exists; use the existing credentials')
    client = Elasticsearch(args.url, ca_certs=str(args.ca.resolve()), verify_certs=True,
                           basic_auth=('elastic', args.admin_password_file.read_text().strip()),
                           request_timeout=30, max_retries=0)
    try:
        ElasticsearchReportRepository(client).initialize()
        response = client.security.create_api_key(name='stock-screener-report-index', role_descriptors={
            'reports': {'cluster': [], 'indices': [
                {'names': [REPORT_INDEX], 'privileges': ['read', 'index', 'view_index_metadata']}
            ]}
        })
        values = {'ELASTICSEARCH_URL': args.url, 'ELASTICSEARCH_CA_CERT': str(args.ca.resolve()),
                  'ELASTICSEARCH_API_KEY': response['encoded']}
        try:
            fd = os.open(args.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                for key, value in values.items():
                    output.write(f'export {key}={shlex.quote(value)}\n')
        except BaseException:
            client.security.invalidate_api_key(ids=[response['id']])
            raise
        print('Report index initialized; index-scoped credentials saved (mode 0600).')
    finally:
        client.close()


if __name__ == '__main__':
    main()
