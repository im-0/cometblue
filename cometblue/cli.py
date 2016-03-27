# -*- coding: utf-8 -*-
from __future__ import absolute_import

import datetime
import functools
import itertools
import json
import logging
import os
import sys

import click
import shellescape
import six
import tabulate

import cometblue.device
import cometblue.discovery


_SHELL_VAR_PREFIX = 'COMETBLUE_'
_WEEK_DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')

_log = None


class _ContextObj(object):
    pass


def _configure_logger(level=logging.ERROR):
    root_logger = logging.getLogger()
    list(map(root_logger.removeHandler, root_logger.handlers[:]))
    list(map(root_logger.removeFilter, root_logger.filters[:]))
    logging.basicConfig(
        format=' %(levelname).1s|%(asctime)s|%(process)d:%(thread)d| '
               '%(message)s',
        stream=sys.stderr,
        level=level)
    global _log
    _log = logging.getLogger()


def _get_log_level(level_str):
    return {
        'D': logging.DEBUG,
        'I': logging.INFO,
        'W': logging.WARNING,
        'E': logging.ERROR,
        'C': logging.CRITICAL,
    }[level_str.upper()[0]]


def _json_default_serializer(obj):
    # Only supports datetime objects.
    return obj.isoformat()


class _JSONFormatter(object):
    def __init__(self):
        self._stream = sys.stdout

    def _print_any(self, value):
        json.dump(value, self._stream, default=_json_default_serializer)
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

    def print_days(self, value):
        table = zip(
                itertools.count(1),
                map(lambda s: s[0].upper() + s[1:], _WEEK_DAYS),
                *zip(*[[('' if period['start'] is None
                         else '%s - %s' % (period['start'].isoformat(),
                                           period['end'].isoformat()))
                        for period in day]
                       for day in value]))
        self._print_simple(
                tabulate.tabulate(
                        table,
                        headers=('N', 'Day', 'Period #1', 'Period #2',
                                 'Period #3', 'Period #4'),
                        tablefmt='psql'))

    def print_holidays(self, value):
        table = zip(
                itertools.count(1),
                *zip(*[(('', '', '') if holiday['start'] is None
                        else (holiday['start'].isoformat(' '),
                              holiday['end'].isoformat(' '),
                              '%.01f' % holiday['temp']))
                       for holiday in value]))
        self._print_simple(
                tabulate.tabulate(
                        table,
                        headers=('N', 'Start', 'End', 'Temperature'),
                        tablefmt='psql'))

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

    def print_days(self, value):
        for day_n, day in zip(itertools.count(), value):
            for period_n, period in zip(itertools.count(), day):
                for var_name in 'start', 'end':
                    var_val = ('' if period[var_name] is None
                               else period[var_name].isoformat())
                    self._stream.write(
                            _SHELL_VAR_PREFIX + 'DAY_%u_PERIOD_%u_%s=%s\n' % (
                                day_n, period_n, var_name.upper(),
                                shellescape.quote(var_val)))
        self._stream.flush()

    def print_holidays(self, value):
        for holiday_n, holiday in zip(itertools.count(), value):
            for var_name in 'start', 'end':
                var_val = ('' if holiday[var_name] is None
                           else holiday[var_name].isoformat())
                self._stream.write(
                        _SHELL_VAR_PREFIX + 'HOLIDAY_%u_%s=%s\n' % (
                            holiday_n, var_name.upper(),
                            shellescape.quote(var_val)))
            self._stream.write(
                    _SHELL_VAR_PREFIX + 'HOLIDAY_%u_TEMP=%s\n' % (
                        holiday_n, shellescape.quote(
                                '' if holiday['temp'] is None
                                else '%f' % holiday['temp'])))
        self._stream.flush()


    def __getattr__(self, item):
        if item.startswith('print_'):
            return functools.partial(self._print_simple, item[len('print_'):])


def _parse_time(time_str):
    if time_str is None:
        return None
    return datetime.datetime.strptime(time_str, '%H:%M:%S').time()


def _parse_datetime(datetime_str):
    if datetime_str is None:
        return None
    try:
        return datetime.datetime.strptime(
                datetime_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return datetime.datetime.strptime(
                datetime_str, '%Y-%m-%dT%H:%M:%S')


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
            ctx.obj.adapter, timeout)
    devices = [dict(name=name, address=address)
               for address, name in six.iteritems(devices)]
    ctx.obj.formatter.print_discovered_devices(devices)


@click.command(
        'days',
        help='Get configured periods per days of the week (requires PIN)')
@click.pass_context
def _device_get_days(ctx):
    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        days = device.get_days()

    ctx.obj.formatter.print_days(days)


@click.command(
        'holidays',
        help='Get configured holidays (requires PIN)')
@click.pass_context
def _device_get_holidays(ctx):
    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        holidays = device.get_holidays()

    ctx.obj.formatter.print_holidays(holidays)


@click.group(
        'get',
        help='Get value')
def _device_get():
    pass


@click.command(
        'day',
        help='Set periods per days of the week (requires PIN)')
@click.argument(
        'day',
        required=True)
@click.argument(
        'period',
        nargs=-1)
@click.pass_context
def _device_set_day(ctx, day, period):
    try:
        day_index = int(day) - 1
    except ValueError:
        day_index = None
        for day_n, day_name in zip(itertools.count(), _WEEK_DAYS):
            if day.lower().startswith(day_name):
                day_index = day_n
                break
        if day_index is None:
            raise RuntimeError('Unknown day: "%s"' % day)

    periods = []
    for one_period in period:
        str_start, str_end = tuple(map(lambda s: s.strip(),
                                       one_period.split('-')))

        if str_start:
            start = _parse_time(str_start)
        else:
            start = datetime.time()

        if str_end:
            end = _parse_time(str_end)
        else:
            end = datetime.time(23, 59, 59)

        periods.append(dict(start=start, end=end))

    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        device.set_day(day_index, periods)


@click.command(
        'holiday',
        help='Set period and temperature for holiday (requires PIN)')
@click.argument(
        'holiday',
        required=True)
@click.argument(
        'start',
        required=False,
        default=None)
@click.argument(
        'end',
        required=False,
        default=None)
@click.argument(
        'temperature',
        type=float,
        required=False,
        default=None)
@click.pass_context
def _device_set_holiday(ctx, holiday, start, end, temperature):
    holiday_index = int(holiday) - 1

    if any(map(lambda v: v is None, (start, end, temperature))):
        start = None
        end = None
        temperature = None

    holiday_data = {
        'start': _parse_datetime(start),
        'end': _parse_datetime(end),
        'temp': temperature,
    }

    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        device.set_holiday(holiday_index, holiday_data)


@click.group(
        'set',
        help='Set value (always requires PIN)')
def _device_set():
    pass


@click.command(
        'backup',
        help='Backup all supported configuration values in JSON format to file '
             'or stdout')
@click.argument(
        'file_name',
        default=None,
        required=False)
@click.pass_context
def _device_backup(ctx, file_name):
    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        backup = device.backup()

    if file_name is None:
        json.dump(backup, sys.stdout, default=_json_default_serializer)
        sys.stdout.flush()
    else:
        with open(file_name, 'w') as backup_file:
            json.dump(backup, backup_file, default=_json_default_serializer)


@click.command(
        'restore',
        help='Restore configuration values from backup in JSON format (from '
             'file or stdin)')
@click.argument(
        'file_name',
        default=None,
        required=False)
@click.pass_context
def _device_restore(ctx, file_name):
    if file_name is None:
        backup = json.load(sys.stdin)
    else:
        with open(file_name, 'r') as backup_file:
            backup = json.load(backup_file)

    if 'days' in backup:
        backup['days'] = [
            [dict(start=_parse_time(period['start']),
                  end=_parse_time(period['end']))
             for period in day]
            for day in backup['days']
        ]

    if 'holidays' in backup:
        backup['holidays'] = [
            dict(start=_parse_datetime(holiday['start']),
                 end=_parse_datetime(holiday['end']),
                 temp=holiday['temp'])
            for holiday in backup['holidays']
        ]

    with cometblue.device.CometBlue(
            ctx.obj.device_address,
            adapter=ctx.obj.adapter,
            pin=ctx.obj.pin) as device:
        device.restore(backup)


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
        '--formatter', '-f',
        type=click.Choice(('json', 'human-readable', 'shell-var')),
        show_default=True,
        default='human-readable',
        help='Output formatter')
@click.option(
        '--log-level', '-L',
        show_default=True,
        default='error')
@click.pass_context
def _main(ctx, adapter, formatter, log_level):
    _configure_logger(_get_log_level(log_level))

    ctx.obj.adapter = adapter

    if formatter == 'json':
        ctx.obj.formatter = _JSONFormatter()
    elif formatter == 'human-readable':
        ctx.obj.formatter = _HumanReadableFormatter()
    else:
        ctx.obj.formatter = _ShellVarFormatter()

    return os.EX_OK


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
                parsed_dt = _parse_datetime(dt)

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


def main():
    _configure_logger()

    _add_values()

    _main.add_command(_discover)
    _main.add_command(_device)

    _device.add_command(_device_get)
    _device.add_command(_device_set)
    _device.add_command(_device_backup)
    _device.add_command(_device_restore)

    _device_get.add_command(_device_get_days)
    _device_get.add_command(_device_get_holidays)

    _device_set.add_command(_device_set_day)
    _device_set.add_command(_device_set_holiday)

    return _main(obj=_ContextObj())


if __name__ == '__main__':
    exit(main())
