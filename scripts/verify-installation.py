"""Exercise the single-command installer and reconfiguration on disposable volumes."""
import argparse
import hashlib
import json
import os
import pty
import secrets
import select
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main(kit):
    kit = kit.resolve()
    project = 'eventhub-install-check-' + uuid4().hex[:8]
    report = {'project': project, 'checks': {}}
    stage = 'start'
    with tempfile.TemporaryDirectory(prefix='installer-check-', dir=ROOT / '.local') as temp:
        directory = Path(temp) / 'installation'
        def run(*args):
            result = subprocess.run([str(x) for x in args], capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError('STEP_FAILED: ' + stage)
            return result.stdout
        def compose(*args):
            return run('docker', 'compose', '-p', project, '--env-file', directory / 'settings.env',
                       '-f', directory / 'compose.json', *args)
        def admin(*args):
            return run(sys.executable, kit / 'admin.py', *args, '--directory', directory)
        def ready(port):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:' + str(port) + '/health/ready', timeout=10) as response:
                assert response.status == 200
        def sql(statement):
            return compose('exec', '-T', 'postgres', 'psql', '-XAt', '-U', 'eventhub', '-d', 'eventhub', '-c', statement)
        def hashes():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (directory / '.secrets').iterdir()}
        try:
            stage = 'automatic installation'
            print('Проверка установки одной командой…', flush=True)
            run(kit / 'install.sh', '--directory', directory, '--project', project,
                '--http-port', '8096', '--smtp-port', '2536', '--skip-admin')
            ready(8096)
            state = json.loads((directory / 'installation.json').read_text())
            assert state['network_management'] == 'external' and state['ready']
            network = json.loads(run('docker', 'network', 'inspect', project + '_default'))[0]
            assert network['Internal'] is False
            first_subnet = network['IPAM']['Config'][0]['Subnet']
            report['checks']['automatic_installation'] = True
            report['checks']['automatic_subnet'] = first_subnet
            config = json.loads((directory / 'compose.json').read_text())
            assert all(x.get('restart') == 'unless-stopped' for x in config['services'].values() if 'healthcheck' in x)
            assert not any(x.get('dns') == ['127.0.0.1'] for x in config['services'].values())
            stage = 'administrator prompt'
            print('Проверка скрытого ввода пароля администратора…', flush=True)
            master, slave = pty.openpty()
            command = ['docker', 'compose', '-p', project, '--env-file', str(directory / 'settings.env'),
                       '-f', str(directory / 'compose.json'), 'exec', 'api', 'python', '-m', 'app.cli',
                       'create-admin', '--username', 'install-check-admin', '--display-name', 'Проверка установки']
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            password = secrets.token_urlsafe(24).encode()
            data = b''
            answered = 0
            deadline = time.monotonic() + 60
            try:
                while process.poll() is None and time.monotonic() < deadline:
                    readable, _, _ = select.select([master], [], [], 0.2)
                    if readable:
                        try:
                            chunk = os.read(master, 4096)
                        except OSError:
                            break
                        data += chunk
                        if b':' in chunk and answered < 2:
                            os.write(master, password + b'\n')
                            answered += 1
                if process.poll() is None:
                    process.wait(timeout=10)
                assert process.returncode == 0 and answered == 2
                assert password not in data
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
            report['checks']['interactive_admin_no_password_echo'] = True
            before = sql('SELECT id, username, password_hash FROM users ORDER BY id;')
            old_hashes = hashes()
            stage = 'repeated install protection'
            again = subprocess.run([str(kit / 'install.sh'), '--directory', str(directory), '--project', project, '--skip-admin'], capture_output=True, check=False)
            assert again.returncode != 0 and hashes() == old_hashes
            ready(8096)
            report['checks']['repeat_install_preserves_data'] = True
            stage = 'network reconfiguration'
            print('Проверка смены подсети и портов с сохранением данных…', flush=True)
            sys.path.insert(0, str(kit))
            import host
            new_subnet = host.choose_subnet()
            assert new_subnet != first_subnet
            admin('configure', '--subnet', new_subnet, '--http-port', '8097', '--smtp-port', '2537')
            ready(8097)
            assert sql('SELECT id, username, password_hash FROM users ORDER BY id;') == before
            assert hashes() == old_hashes
            network = json.loads(run('docker', 'network', 'inspect', project + '_default'))[0]
            assert network['IPAM']['Config'][0]['Subnet'] == new_subnet
            report['checks']['subnet_and_ports_changed_data_preserved'] = True
            stage = 'restart'
            admin('stop')
            admin('start')
            ready(8097)
            report['checks']['restart_without_firewall_policy'] = True
            report['checks']['local_secrets_unchanged'] = True
        finally:
            if (directory / 'installation.json').exists():
                result = subprocess.run(['docker', 'compose', '-p', project, '--env-file', str(directory / 'settings.env'),
                                         '-f', str(directory / 'compose.json'), 'down', '--volumes', '--timeout', '30'], capture_output=True, check=False)
                if result.returncode:
                    raise RuntimeError('TEST_CLEANUP_FAILED')
    target = ROOT / '.local/publication/installer-verification.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Установка, смена сети, сохранность данных и повторный запуск проверены.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--kit', required=True, type=Path)
    try:
        main(parser.parse_args().kit)
    except Exception as error:  # noqa: BLE001 - keep credentials out of diagnostics
        # No captured child output or user data in diagnostics.
        print('Проверка установки не завершена: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
