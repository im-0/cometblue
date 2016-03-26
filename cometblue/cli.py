from __future__ import absolute_import

import itertools
import json
import logging
import sys

import click
import shellescape
import six

import cometblue.discovery


_SHELL_VAR_PREFIX = 'COMETBLUE_'

_log = None


class _ContextObj(object):
    pass


def _configure_logger():
    root_logger = logging.getLogger()
    list(map(root_logger.removeHandler, root_logger.handlers[:]))
    list(map(root_logger.removeFilter, root_logger.filters[:]))
    logging.basicConfig(
        format=' %(levelname).1s|%(asctime)s|%(process)d:%(thread)d| '
               '%(message)s',
        stream=sys.stderr,
        level=logging.INFO)
    global _log
    _log = logging.getLogger()


class _JSONFormatter(object):
    def __init__(self):
        self._stream = sys.stdout

    def print_any(self, value):
        json.dump(value, self._stream)
        self._stream.flush()

    def __getattr__(self, item):
        if item.startswith('print_'):
            return self.print_any


class _HumanReadableFormatter(object):
    def __init__(self):
        self._stream = sys.stdout

    def print_discovered_devices(self, devices):
        for device in devices:
            self._stream.write('%(name)s (%(address)s)\n' % device)
        self._stream.flush()


class _ShellVarFormatter(object):
    def __init__(self):
        self._stream = sys.stdout

    def print_discovered_devices(self, devices):
        self._stream.write(_SHELL_VAR_PREFIX + 'DEVICES=%u\n' % len(devices))
        for device_n, device in zip(itertools.count(), devices):
            self._stream.write(
                    _SHELL_VAR_PREFIX + 'DEVICE_%u_NAME=%s\n' % (
                        device_n, shellescape.quote(device['name'])))
            self._stream.write(
                    _SHELL_VAR_PREFIX + 'DEVICE_%u_ADDRESS=%s\n' % (
                        device_n, shellescape.quote(device['address'])))
        self._stream.flush()


@click.command(
        'discover',
        help='Discover "Comet Blue" Bluetooth LE devices')
@click.option(
        '--timeout', '-t',
        type=int,
        show_default=True,
        default=10,
        help='Device discovery timeout in seconds')
@click.pass_context
def _discover(ctx, timeout):
    devices = cometblue.discovery.discover(ctx.obj.adapter, timeout)
    devices = [dict(name=name, address=address)
               for address, name in six.iteritems(devices)]
    ctx.obj.formatter.print_discovered_devices(devices)


@click.group(
        context_settings={'help_option_names': ['-h', '--help']},
        help='Command line tool for "Comet Blue" radiator thermostat')
@click.option(
        '--adapter', '-a',
        show_default=True,
        default='hci0',
        help='Bluetooth adapter interface')
@click.option(
        '--formatter', '-f',
        type=click.Choice(('json', 'human-readable', 'shell-var')),
        show_default=True,
        default='human-readable',
        help='Output formatter')
@click.pass_context
def main(ctx, adapter, formatter):
    ctx.obj.adapter = adapter

    if formatter == 'json':
        ctx.obj.formatter = _JSONFormatter()
    elif formatter == 'human-readable':
        ctx.obj.formatter = _HumanReadableFormatter()
    else:
        ctx.obj.formatter = _ShellVarFormatter()


if __name__ == '__main__':
    _configure_logger()

    main.add_command(_discover)

    main(obj=_ContextObj())
