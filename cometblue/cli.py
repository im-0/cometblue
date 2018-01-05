#!/usr/bin/python3
# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 expandtab
from __future__ import absolute_import

import datetime
import functools
import itertools
import json
import logging
import os
import sys
import gatt

import click
import shellescape
import six
import tabulate

import cometblue.device
import cometblue.discovery

import threading
from collections import deque


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
        text += 'Current temperature:\t%.01f °C\n' % value['current_temp']
        text += 'Temperature for manual mode:\t%.01f °C\n' % value['manual_temp']
        text += 'Target temperature low:\t%.01f °C\n' % value['target_temp_l']
        text += 'Target temperature high:\t%.01f °C\n' % value['target_temp_h']
        text += 'Offset temperature:\t%.01f °C\n' % value['offset_temp']
        text += 'Window open sensitivity:\t%u (1 = low, 4 = high, 8 = mid)\n' % value['window_open_detection']
        text += 'Window open minutes:\t%u\n' % value['window_open_minutes']
        self._stream.write(text)
        self._stream.flush()

    def print_status(self, value):
        text = ''
        text += 'Temperature satisfied:\t%r\n' % value['satisfied']
        text += 'Child-lock:\t%r\n' % value['childlock']
        text += 'Manual mode is:\t%r\n' % value['manual_mode']
        text += 'Adapting:\t%r\n' % value['adapting']
        text += 'Not ready:\t%r\n' % value['not_ready']
        text += 'Motor moving:\t%r\n' % value['motor_moving']
        text += 'Install procedure running:\t%r\n' % value['installing']
        text += 'Antifrost active:\t%r\n' % value['antifrost_activated']
        text += 'Low battery alert:\t%r\n' % value['low_battery']
        text += 'State dword:\t0x%08X\n' % value['state_as_dword']
        text += 'Unknown state:\t0x%08X\n' % value['unused_bits']
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


class Command(object):
    def __init__(self, routine, *args):
        self._routine = routine
        self._args = args

    def __call__(self):
        return self._routine(*self._args)


def _queue_command(ctx, *cmdargs):
    ctx.obj.commands.append(Command(*cmdargs))
def _inject_command(ctx, *cmdargs):
    ctx.obj.commands.appendleft(Command(*cmdargs))
def _queue_cleanup(ctx, *cmdargs):
    ctx.obj.cleanup.append(Command(*cmdargs))


@click.command(
        'discover',
        help='Discover "Comet Blue" Bluetooth LE devices (might take a while)',
        short_help='Scan for devices (might take a while)')
@click.option(
        '--timeout', '-t',
        type=int,
        show_default=True,
        default=10,
        help='Device discovery timeout in seconds')
@click.pass_context
def _discover(ctx, timeout):
    def _discover_command(manager, timeout, formatter):
        devices = cometblue.discovery.discover(manager, timeout)
        devices = [dict(name=name, address=address)
               for address, name in six.iteritems(devices)]
            formatter.print_discovered_devices(devices)
        _log.info('Starting discovery on adapter "%s" with %u seconds timeout...',
                      manager.adapter_name, timeout)
        return 0
    _queue_command(ctx, _discover_command, ctx.obj.manager, timeout, ctx.obj.formatter)


@click.command(
        'days',
        help='Get configured periods per days of the week (requires PIN)')
@click.pass_context
def _device_get_days(ctx):
    def _device_get_days_command(device):
        days = device.get_days()

        ctx.obj.formatter.print_days(days)
        return 0
    _queue_command(ctx, _device_get_days_command, ctx.obj.device)


@click.command(
        'holidays',
        help='Get configured holidays (requires PIN)')
@click.pass_context
def _device_get_holidays(ctx):
    def _device_get_holidays_command(device):
        holidays = device.get_holidays()

        ctx.obj.formatter.print_holidays(holidays)
        return 0
    _queue_command(ctx, _device_get_holidays_command, ctx.obj.device)


@click.group(
        'get',
        help='Get value',
        chain=True)
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
    def _device_set_day_command(device, day, period):
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

        device.set_day(day_index, periods)

        return 0
    _queue_command(ctx, _device_set_day_command, ctx.obj.device, day, period)


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
    def _device_set_holiday_command(device, holiday, start, end, temperature):
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

        device.set_holiday(holiday_index, holiday_data)
        return 0
    _queue_command(ctx, __device_set_holiday_command, ctx.obj.device, holiday, start, end, temperature)


@click.group(
        'set',
        help='Set value (always requires PIN)',
        chain=True)
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
    def _device_backup_command(device, file_name):
        backup = device.backup()

        if file_name is None:
            json.dump(backup, sys.stdout, default=_json_default_serializer)
            sys.stdout.flush()
        else:
            with open(file_name, 'w') as backup_file:
                json.dump(backup, backup_file, default=_json_default_serializer)
        return 0
    _queue_command(ctx, _device_backup_command, ctx.obj.device, file_name)


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
    def _device_restore_command(device, file_name):
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

        device.restore(backup)
        return 0

    _queue_command(ctx, _device_restore_command, ctx.obj.device, file_name)


@click.group(
        'device',
        short_help='Get or set values'
        #, chain=True
        )
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
    '''
    Get or set values.
    '''
    def _setup_pin(pin, pin_file):
        if pin_file is not None:
            with open(pin_file, 'r') as pin_file:
                return int(pin_file.read())
        elif pin is not None:
            return int(pin)
        else:
            return None

    pin = _setup_pin(pin, pin_file)
    device = cometblue.device.CometBlue(address, ctx.obj.manager, pin)
    ctx.obj.device = device

    def _device_connect_command(device):
        device.connect()
        return 0 if device.is_connected() else 1
    def _device_disconnect_command(device):
        device.disconnect()
        return 0

    _queue_command(ctx, _device_connect_command, device)
    _queue_cleanup(ctx, _device_disconnect_command, device)

    def _wait_for_device_ready_command(device):
        _log.info('Waiting for device handler to become ready...')

        if device is None:
            return

        device.attempt_to_get_ready()
        if not device.ready():
            raise RuntimeError("Waited for device to become ready for too long, aborting")
        _log.debug("Device reports ready")

        return 0

    _queue_command(ctx, _wait_for_device_ready_command, device)


@click.group(
        context_settings={'help_option_names': ['-h', '--help']},
        help='Command line tool for "Comet Blue" radiator thermostat')
@click.option(
        '--adapter', '-a',
        show_default=True,
        default='hci0',
        help='Bluetooth adapter interface')
@click.option(
        '--poweron', '-p',
        show_default=True,
        default=False,
        is_flag=True,
        help='Power ON/OFF adapter if needed')
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
def _main(ctx, adapter, poweron, formatter, log_level):
    _configure_logger(_get_log_level(log_level))
    manager = cometblue.device.CometBlueManager(adapter_name = str(adapter))
    if formatter == 'json':
        ctx.obj.formatter = _JSONFormatter()
    elif formatter == 'human-readable':
        ctx.obj.formatter = _HumanReadableFormatter()
    else:
        ctx.obj.formatter = _ShellVarFormatter()

    def _main_command(ctx, manager, poweron):
        def _powerdown_adapter_command(manager):
            _log.debug('Shutting down bluetooth adapter %s' % (manager.adapter_name))
            manager.is_adapter_powered = False

        poweron_mgmt = poweron and not manager.is_adapter_powered
        if poweron_mgmt:
            _log.debug('Powering on bluetooth adapter %s' % (manager.adapter_name))
            manager.is_adapter_powered = True
            _queue_cleanup(ctx, _powerdown_adapter_command, manager)

        return 0

    ctx.obj.manager = manager
    _queue_command(ctx, _main_command, ctx, manager, poweron)


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
    def status(real_setter):

        @click.option('+c/-c', '--childlock/--no-childlock', 'childlock', is_flag=True, default=None, help='Enable/disable childlock')
        @click.option('+m/-m', '--manual-mode', '--auto-mode', 'manual_mode', is_flag=True, default=None, help='Enable/disable manual mode')
        @click.option('+a', '--adapt', 'adapting', is_flag=True, default=None, help='Re-adapt (make sure device is mounted)')
        @click.pass_context
        def set_status(ctx, childlock, manual_mode, adapting):
            keys = ['childlock', 'manual_mode', 'adapting']
            vals = [childlock, manual_mode, adapting]

            status = {}
            for i in range(len(keys)):
                if vals[i] is None:
                    continue
                status[keys[i]] = vals[i]

            if not status:
                raise RuntimeError(
                        'No status flags to update, try "status -h"')

            if ctx.obj.device._device.is_connected():
                current = ctx.obj.device.get_status()
                current = dict((k, v) for k, v in current.items() if k in keys)
                for k, v in status.items():
                    current[k] = v
                status = current

            real_setter(ctx, status)

        return set_status

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
                help='Window open sensitivity (1 = low, 4 = high, 8 = mid)')
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


def _enroll_subcommands():
    for val_name, val_conf in six.iteritems(
            cometblue.device.CometBlue.SUPPORTED_VALUES):
        if 'decode' in val_conf:
            def get_fn_with_name(get_fn_name, print_fn_name):
                def real_get_fn(ctx):
                    def _get_command(device):
                        value = getattr(device, get_fn_name)()

                        print_fn = getattr(ctx.obj.formatter, print_fn_name)
                        print_fn(value)
                        return 0
                    _queue_command(ctx, _get_command, ctx.obj.device)

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
                    def _set_command(device):
                        getattr(device, set_fn_name)(value)
                    _queue_command(ctx, _set_command, ctx.obj.device)

                return real_set_fn

            set_fn = getattr(_SetterFunctions, val_name)(
                    set_fn_with_name('set_' + val_name))
            set_fn = click.command(
                    val_name,
                    help='Set %s '
                         '(requires PIN)' % val_conf['description'])(set_fn)

            _device_set.add_command(set_fn)


def _init_command_parsing():
    _enroll_subcommands()

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

    context = _ContextObj()
    return context


# Bug in gatt-python, see https://github.com/getsenic/gatt-python/issues/5
# efectively, this means that glib main loop has to run somewhere...
class ManagerThread(threading.Thread):
    def __init__(self, manager, kill_event):
        super().__init__()
        self._manager = manager
        self._kill_event = kill_event

    def run(self):
        _log.debug("Manager thread running")
        while True:
            try:
                self._manager.run()
                break
            except KeyboardInterrupt as ex:
                self._kill_event.set()
                _log.debug("Manager thread received kill, dropping out to notify main thread")
                continue

        _log.debug("Manager thread done")

    def join(self):
        self._manager.stop()
        _log.debug("Manager thread done (join)")
        super().join()

class CliThread(threading.Thread):
    def __init__(self, commands, kill_event):
        super().__init__()
        self._commands = commands
        self._kill_event = kill_event

    def run(self):
        try:
            # command parsing done, start manager thread
            while self._commands and not self._kill_event.is_set():
                command = self._commands.popleft()
                rv = command()
                if rv != 0:
                    break
        except Exception as ex:
            _log.error('Command processing returned exception: ' + str(ex))
        self._kill_event.set()

def cli_main(argv):
    _configure_logger()
    context = _init_command_parsing()
    context.commands = deque()
    context.cleanup = deque()

    # click is from now on used only for command parsing
    rv = 0
    try:
        rv = _main(obj=context, args=argv)
    except SystemExit:
        pass
    except (RuntimeError, Exception) as err:
        _log.error(str(err))
        return -1

    somebody_killed = threading.Event()
    manager_thread = ManagerThread(context.manager, somebody_killed)
    cli_thread = CliThread(context.commands, somebody_killed)

    context.device.aborter = lambda: somebody_killed.is_set()

    manager_thread.start()
    cli_thread.start()

    # wait for either thread to be done
    somebody_killed.wait()
    cli_thread.join()

    while context.cleanup:
        command = context.cleanup.popleft()
        rv = command()

    manager_thread.join()

def main():
    return cli_main(sys.argv[1:])

if __name__ == '__main__':
    exit(main())
