# -*- coding: utf-8 -*-
from __future__ import absolute_import

import datetime
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

    def print_datetime(self, value):
        self._print_any(value.isoformat())

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

    def print_datetime(self, value):
        self._print_simple(value.isoformat(' '))

    def print_battery(self, value):
        if value is None:
            self._print_simple('No information')
        else:
            self._print_simple('%u%%' % value)

    def print_temperatures(self, value):
        text = ''
        text += 'Current temperature: %.01f °C\n' % value['current_temp']
        text += 'Temperature for manual mode: %.01f °C\n' % value['manual_temp']
        text += 'Target temperature low: %.01f °C\n' % value['target_temp_l']
        text += 'Target temperature high: %.01f °C\n' % value['target_temp_h']
        text += 'Offset temperature: %.01f °C\n' % value['offset_temp']
        text += 'Window open detection: %u\n' % value['window_open_detection']
        text += 'Window open minutes: %u\n' % value['window_open_minutes']
        self._stream.write(text)
        self._stream.flush()

    def print_lcd_timer(self, value):
        self._print_simple('%02u:%02u' % (value['preload'], value['current']))

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

    def print_datetime(self, value):
        self._print_simple('datetime', value.isoformat())

    def print_battery(self, value):
        if value is None:
            self._print_simple('battery', '')
        else:
            self._print_simple('battery', '%u' % value)

    def print_temperatures(self, value):
        for var_name in ('current_temp',
                         'manual_temp',
                         'target_temp_l',
                         'target_temp_h',
                         'offset_temp'):
            val_str = '%f' % value[var_name]
            self._stream.write(
                    _SHELL_VAR_PREFIX + '%s=%s\n' % (
                        var_name.upper(), shellescape.quote(val_str)))
        for var_name in ('window_open_detection',
                         'window_open_minutes'):
            val_str = '%u' % value[var_name]
            self._stream.write(
                    _SHELL_VAR_PREFIX + '%s=%s\n' % (
                        var_name.upper(), shellescape.quote(val_str)))
        self._stream.flush()

    def print_lcd_timer(self, value):
        self._print_simple('lcd_timer_preload', '%u' % value['preload'])
        self._print_simple('lcd_timer_current', '%u' % value['current'])

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
        'set',
        help='Set value (always requires PIN)')
def _device_set():
    pass


@click.group(
        'device',
        help='Get or set values')
@click.option(
        '--pin', '-p',
        default=None,
        help='PIN for connecting to device (factory default PIN is 0)')
@click.option(
        '--pin-file', '-P',
        default=None,
        help='Read PIN for connecting to device from file')
@click.argument(
        'address',
        required=True)
@click.pass_context
def _device(ctx, address, pin, pin_file):
    ctx.obj.device_address = address

    if pin_file is not None:
        with open(pin_file, 'r') as pin_file:
            ctx.obj.pin = int(pin_file.read())
    elif pin is not None:
        ctx.obj.pin = int(pin)
    else:
        ctx.obj.pin = None


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


class _SetterFunctions(object):
    @staticmethod
    def pin(real_setter):
        @click.argument(
                'pin',
                required=True)
        @click.pass_context
        def set_pin(ctx, pin):
            real_setter(ctx, int(pin))

        return set_pin

    @staticmethod
    def datetime(real_setter):
        @click.argument(
                'dt',
                default=None,
                required=False)
        @click.pass_context
        def set_datetime(ctx, dt):
            if dt is None:
                parsed_dt = datetime.datetime.now()
            else:
                try:
                    parsed_dt = datetime.datetime.strptime(
                            dt, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    parsed_dt = datetime.datetime.strptime(
                            dt, '%Y-%m-%dT%H:%M:%S')

            real_setter(ctx, parsed_dt)

        return set_datetime

    @staticmethod
    def temperatures(real_setter):
        @click.option(
                '--temp-manual', '-m',
                type=float,
                default=None,
                help='Temperature for manual mode')
        @click.option(
                '--temp-target-low', '-t',
                type=float,
                default=None,
                help='Target temperature low')
        @click.option(
                '--temp-target-high', '-T',
                type=float,
                default=None,
                help='Target temperature high')
        @click.option(
                '--temp-offset', '-o',
                type=float,
                default=None,
                help='Offset temperature')
        @click.option(
                '--window-open-detect', '-w',
                type=int,
                default=None,
                help='Window open detection')
        @click.option(
                '--window-open-minutes', '-W',
                type=int,
                default=None,
                help='Window open minutes')
        @click.pass_context
        def set_temperatures(ctx, temp_manual, temp_target_low,
                             temp_target_high, temp_offset, window_open_detect,
                             window_open_minutes):
            temps = {
                'manual_temp': temp_manual,
                'target_temp_l': temp_target_low,
                'target_temp_h': temp_target_high,
                'offset_temp': temp_offset,
                'window_open_detection': window_open_detect,
                'window_open_minutes': window_open_minutes,
            }
            if all(map(lambda v: v is None, six.itervalues(temps))):
                raise RuntimeError(
                        'No new values to set, try "temperatures -h"')
            real_setter(ctx, temps)

        return set_temperatures

    @staticmethod
    def lcd_timer(real_setter):
        @click.argument(
                'value',
                required=True)
        @click.pass_context
        def set_lcd_timer(ctx, value):
            lcd_timer = {
                'preload': int(value),
            }
            real_setter(ctx, lcd_timer)

        return set_lcd_timer


def _add_values():
    for val_name, val_conf in six.iteritems(
            cometblue.device.CometBlue.SUPPORTED_VALUES):
        if 'decode' in val_conf:
            def get_fn_with_name(get_fn_name, print_fn_name):
                def real_get_fn(ctx):
                    with cometblue.device.CometBlue(
                            ctx.obj.device_address,
                            adapter=ctx.obj.adapter,
                            channel_type=ctx.obj.channel_type,
                            security_level=ctx.obj.security_level,
                            pin=ctx.obj.pin) as device:
                        value = getattr(device, get_fn_name)()

                    print_fn = getattr(ctx.obj.formatter, print_fn_name)
                    print_fn(value)

                return real_get_fn

            get_fn = get_fn_with_name('get_' + val_name, 'print_' + val_name)
            get_fn = click.pass_context(get_fn)

            help_text = 'Get %s' % val_conf['description']
            if val_conf.get('read_requires_pin', False):
                help_text += ' (requires PIN)'
            get_fn = click.command(
                    val_name,
                    help=help_text)(get_fn)

            _device_get.add_command(get_fn)

        if 'encode' in val_conf:
            def set_fn_with_name(set_fn_name):
                def real_set_fn(ctx, value):
                    with cometblue.device.CometBlue(
                            ctx.obj.device_address,
                            adapter=ctx.obj.adapter,
                            channel_type=ctx.obj.channel_type,
                            security_level=ctx.obj.security_level,
                            pin=ctx.obj.pin) as device:
                        getattr(device, set_fn_name)(value)

                return real_set_fn

            set_fn = getattr(_SetterFunctions, val_name)(
                    set_fn_with_name('set_' + val_name))
            set_fn = click.command(
                    val_name,
                    help='Set %s '
                         '(requires PIN)' % val_conf['description'])(set_fn)

            _device_set.add_command(set_fn)


if __name__ == '__main__':
    _configure_logger()

    _add_values()

    main.add_command(_discover)
    main.add_command(_device)

    _device.add_command(_device_get)
    _device.add_command(_device_set)

    main(obj=_ContextObj())
