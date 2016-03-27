from __future__ import absolute_import

import functools
import itertools
import json
import logging
import sys

import click
import shellescape
import six

import cometblue.device
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

    def _print_any(self, value):
        json.dump(value, self._stream)
        self._stream.flush()

    def __getattr__(self, item):
        if item.startswith('print_'):
            return self._print_any


class _HumanReadableFormatter(object):
    def __init__(self):
        self._stream = sys.stdout

    def print_discovered_devices(self, devices):
        for device in devices:
            self._stream.write('%(name)s (%(address)s)\n' % device)
        self._stream.flush()

    def _print_simple(self, value):
        self._stream.write(value + '\n')
        self._stream.flush()

    def __getattr__(self, item):
        if item.startswith('print_'):
            return self._print_simple


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

    def _print_simple(self, name, value):
        self._stream.write(
                _SHELL_VAR_PREFIX + '%s=%s\n' % (
                    name.upper(), shellescape.quote(value)))
        self._stream.flush()

    def __getattr__(self, item):
        if item.startswith('print_'):
            return functools.partial(self._print_simple, item[len('print_'):])


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
    devices = cometblue.discovery.discover(
            ctx.obj.adapter, timeout,
            channel_type=ctx.obj.channel_type,
            security_level=ctx.obj.security_level)
    devices = [dict(name=name, address=address)
               for address, name in six.iteritems(devices)]
    ctx.obj.formatter.print_discovered_devices(devices)


@click.group(
        'get',
        help='Get value')
def _device_get():
    pass


@click.group(
        'device',
        help='Get or set values')
@click.argument(
        'address',
        required=True)
@click.pass_context
def _device(ctx, address):
    ctx.obj.device_address = address


@click.group(
        context_settings={'help_option_names': ['-h', '--help']},
        help='Command line tool for "Comet Blue" radiator thermostat')
@click.option(
        '--adapter', '-a',
        show_default=True,
        default='hci0',
        help='Bluetooth adapter interface')
@click.option(
        '--channel-type', '-c',
        type=click.Choice(('public', 'random')),
        show_default=True,
        default='public')
@click.option(
        '--security-level', '-s',
        type=click.Choice(('low', 'medium', 'high')),
        show_default=True,
        default='low')
@click.option(
        '--formatter', '-f',
        type=click.Choice(('json', 'human-readable', 'shell-var')),
        show_default=True,
        default='human-readable',
        help='Output formatter')
@click.pass_context
def main(ctx, adapter, channel_type, security_level, formatter):
    ctx.obj.adapter = adapter
    ctx.obj.channel_type = channel_type
    ctx.obj.security_level = security_level

    if formatter == 'json':
        ctx.obj.formatter = _JSONFormatter()
    elif formatter == 'human-readable':
        ctx.obj.formatter = _HumanReadableFormatter()
    else:
        ctx.obj.formatter = _ShellVarFormatter()


def _add_values():
    for val_name, val_conf in six.iteritems(
            cometblue.device.CometBlue.SUPPORTED_VALUES):
        def get_fn_with_name(get_fn_name, print_fn_name):
            def real_get_fn(ctx):
                with cometblue.device.CometBlue(
                        ctx.obj.device_address,
                        adapter=ctx.obj.adapter,
                        channel_type=ctx.obj.channel_type,
                        security_level=ctx.obj.security_level) as device:
                    value = getattr(device, get_fn_name)()

                print_fn = getattr(ctx.obj.formatter, print_fn_name)
                print_fn(value)

            return real_get_fn

        get_fn = get_fn_with_name('get_' + val_name, 'print_' + val_name)
        get_fn = click.pass_context(get_fn)
        get_fn = click.command(
                val_name,
                help='Get %s' % val_conf['description'])(get_fn)

        _device_get.add_command(get_fn)


if __name__ == '__main__':
    _configure_logger()

    _add_values()

    main.add_command(_discover)
    main.add_command(_device)

    _device.add_command(_device_get)

    main(obj=_ContextObj())
