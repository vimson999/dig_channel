#!/usr/bin/env python3
"""Run book-commerce PostgreSQL, migration and worker tests against disposable local services."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / 'venv' / 'bin' / 'python'


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', action='store_true', help='also run the 100,000-observation benchmark')
    benchmark = parser.parse_args().benchmark
    pg_port, redis_port = free_port(), free_port()
    if pg_port == redis_port:
        redis_port = free_port()
    with tempfile.TemporaryDirectory(prefix='bc-isolated-') as directory:
        pg_data = directory + '/postgres'
        pg_log = directory + '/postgres.log'
        redis_log = directory + '/redis.log'
        subprocess.run(['initdb', '-D', pg_data, '-A', 'trust', '-U', 'bc_test', '--no-locale', '-E', 'UTF8'], check=True, stdout=subprocess.DEVNULL)
        redis = None
        postgres_started = False
        try:
            subprocess.run(['pg_ctl', '-D', pg_data, '-l', pg_log, '-o', f'-p {pg_port} -h 127.0.0.1 -k {directory}', 'start'], check=True)
            postgres_started = True
            subprocess.run(['createdb', '-h', '127.0.0.1', '-p', str(pg_port), '-U', 'bc_test', 'bc_test_isolated'], check=True)
            with open(redis_log, 'w') as log:
                redis = subprocess.Popen(['redis-server', '--port', str(redis_port), '--bind', '127.0.0.1', '--save', '', '--appendonly', 'no', '--dir', directory],
                                         env=dict(os.environ, LC_ALL='C', LANG='C'), stdout=log, stderr=subprocess.STDOUT)
                for _ in range(50):
                    if redis.poll() is not None:
                        raise RuntimeError('临时 Redis 未能启动')
                    with socket.socket() as probe:
                        probe.settimeout(.2)
                        if probe.connect_ex(('127.0.0.1', redis_port)) == 0:
                            break
                    time.sleep(.1)
                else:
                    raise RuntimeError('临时 Redis 启动超时')
                broker = f'redis://127.0.0.1:{redis_port}/0'
                env = dict(os.environ, BOOK_COMMERCE_TEST_DATABASE_URL=f'postgresql://bc_test@127.0.0.1:{pg_port}/bc_test_isolated',
                           BOOK_COMMERCE_TEST_REDIS_URL=broker, CELERY_BROKER_URL=broker, CELERY_RESULT_BACKEND=broker, REDIS_URL=broker)
                args = [str(PYTHON), '-m', 'pytest', 'tests/unit/test_book_commerce_migration.py',
                        'tests/unit/test_book_commerce_postgres.py', 'tests/unit/test_book_commerce_worker_e2e.py', '-q']
                status = subprocess.run(args, cwd=ROOT, env=env).returncode
                if status or not benchmark:
                    return status
                subprocess.run(['createdb', '-h', '127.0.0.1', '-p', str(pg_port), '-U', 'bc_test', 'bc_test_benchmark'], check=True)
                env['BOOK_COMMERCE_TEST_DATABASE_URL'] = f'postgresql://bc_test@127.0.0.1:{pg_port}/bc_test_benchmark'
                return subprocess.run([str(PYTHON), 'scripts/dev/benchmark-book-commerce.py'], cwd=ROOT, env=env).returncode
        finally:
            if redis is not None and redis.poll() is None:
                redis.terminate()
                redis.wait(timeout=5)
            if postgres_started:
                subprocess.run(['pg_ctl', '-D', pg_data, '-m', 'immediate', 'stop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    sys.exit(main())
