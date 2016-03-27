from __future__ import absolute_import

import datetime
import functools
import itertools
import logging
import struct
import uuid as uuid_module

import gattlib
import six


_PIN_STRUCT = '<I'
_DATETIME_STRUCT = '<BBBBB'
_FLAGS_STRUCT = '<BBB'
_TEMPERATURES_STRUCT = '<bbbbbbb'
_LCD_TIMER_STRUCT = '<BB'
_DAY_STRUCT = '<BBBBBBBB'
_HOLIDAY_STRUCT = '<BBBBBBBBb'

_log = logging.getLogger(__name__)


def _encode_pin(pin):
    return struct.pack(_PIN_STRUCT, pin)


def _decode_datetime(value):
    mi, ho, da, mo, ye = struct.unpack(_DATETIME_STRUCT, value)
    return datetime.datetime(
            year=ye + 2000,
            month=mo,
            day=da,
            hour=ho,
            minute=mi)


def _encode_datetime(dt):
    if dt.year < 2000:
        raise RuntimeError('Invalid year')
    return struct.pack(
            _DATETIME_STRUCT,
            dt.minute,
            dt.hour,
            dt.day,
            dt.month,
            dt.year - 2000)


def _decode_flags(value):
    f1, f2, f3 = struct.unpack(_FLAGS_STRUCT, value)
    return '%s %s %s' % tuple(map(bin, (f1, f2, f3)))


def _decode_temperatures(value):
    cur_temp, manual_temp, target_low, target_high, offset_temp, \
            window_open_detect, window_open_minutes = struct.unpack(
                    _TEMPERATURES_STRUCT, value)
    return {
        'current_temp': cur_temp / 2.0,
        'manual_temp': manual_temp / 2.0,
        'target_temp_l': target_low / 2.0,
        'target_temp_h': target_high / 2.0,
        'offset_temp': offset_temp / 2.0,
        'window_open_detection': window_open_detect,
        'window_open_minutes': window_open_minutes,
    }


def _temp_float_to_int(temps_dict, var_name):
    var_val = temps_dict.get(var_name)
    if var_val is None:
        return -128  # do not change setting
    return int(var_val * 2.0)


def _temp_int_to_int(temps_dict, var_name):
    var_val = temps_dict.get(var_name)
    if var_val is None:
        return -128  # do not change setting
    return var_val


def _encode_temperatures(temps):
    return struct.pack(
            _TEMPERATURES_STRUCT,
            -128,  # current_temp
            _temp_float_to_int(temps, 'manual_temp'),
            _temp_float_to_int(temps, 'target_temp_l'),
            _temp_float_to_int(temps, 'target_temp_h'),
            _temp_float_to_int(temps, 'offset_temp'),
            _temp_int_to_int(temps, 'window_open_detection'),
            _temp_int_to_int(temps, 'window_open_minutes'))


def _decode_battery(value):
    value = ord(value)
    if value == 255:
        return None
    return value


def _decode_lcd_timer(value):
    preload, current = struct.unpack(_LCD_TIMER_STRUCT, value)
    return {
        'preload': preload,
        'current': current,
    }


def _encode_lcd_timer(lcd_timer):
    return struct.pack(
            _LCD_TIMER_STRUCT,
            lcd_timer['preload'],
            0)


def _day_period_cmp(p1, p2):
    if p1['start'] is None:
        return 1
    if p2['start'] is None:
        return -1
    return cmp(p1['start'], p2['start'])


def _decode_day(value):
    max_raw_time = ((23 * 60) + 59) / 10

    raw_time_values = list(struct.unpack(_DAY_STRUCT, value))
    day = []
    while raw_time_values:
        raw_start = raw_time_values.pop(0)
        raw_end = raw_time_values.pop(0)

        if raw_end > max_raw_time:
            start = None
            end = None
        else:
            if raw_start > max_raw_time:
                start = datetime.time()
            else:
                raw_start *= 10
                start = datetime.time(hour=raw_start / 60,
                                      minute=raw_start % 60)

            if raw_end > max_raw_time:
                end = datetime.time(23, 59, 59)
            else:
                raw_end *= 10
                end = datetime.time(hour=raw_end / 60,
                                    minute=raw_end % 60)

        if start == end:
            day.append({
                'start': None,
                'end': None,
            })
        else:
            day.append({
                'start': start,
                'end': end,
            })

    day.sort(_day_period_cmp)

    return day


def _encode_day(periods):
    if len(periods) > 4:
        raise RuntimeError('Too many periods')
    periods = list(periods)
    periods.extend([dict(start=None, end=None)] * (4 - len(periods)))

    values = []
    for period in periods:
        if period['start'] is None:
            start = 255
            end = 255
        else:
            start = (period['start'].hour * 60 + period['start'].minute) / 10
            end = (period['end'].hour * 60 + period['end'].minute) / 10

        if start == 0:
            start = 255
        if end == 0:
            end = 255

        values.append(start)
        values.append(end)

    return struct.pack(_DAY_STRUCT, *values)


def _decode_holiday(value):
    ho_start, da_start, mo_start, ye_start, \
            ho_end, da_end, mo_end, ye_end, \
            temp = struct.unpack(_HOLIDAY_STRUCT, value)

    if (ho_start > 23) or (ho_end > 23) \
            or (da_start > 31) or (da_end > 31) \
            or (da_start < 1) or (da_end < 1) \
            or (mo_start > 12) or (mo_end > 12) \
            or (mo_start < 1) or (mo_end < 1) \
            or (ye_start > 99) or (ye_end > 99) \
            or (temp == -128):
        start = None
        end = None
        temp = None
    else:
        start = datetime.datetime(
                year=ye_start + 2000,
                month=mo_start,
                day=da_start,
                hour=ho_start)
        end = datetime.datetime(
                year=ye_end + 2000,
                month=mo_end,
                day=da_end,
                hour=ho_end)
        temp = temp / 2.0

    return {
        'start': start,
        'end': end,
        'temp': temp,
    }


def _encode_holiday(holiday):
    if any(map(lambda v: v is None, six.itervalues(holiday))):
        return struct.pack(_HOLIDAY_STRUCT,
                           128, 128, 128, 128, 128, 128, 128, 128, -128)

    if (holiday['start'].year < 2000) or (holiday['end'].year < 2000):
        raise RuntimeError('Invalid year')

    return struct.pack(
            _HOLIDAY_STRUCT,
            holiday['start'].hour,
            holiday['start'].day,
            holiday['start'].month,
            holiday['start'].year - 2000,
            holiday['end'].hour,
            holiday['end'].day,
            holiday['end'].month,
            holiday['end'].year - 2000,
            _temp_float_to_int(holiday, 'temp'))


def _increase_uuid(uuid_str, n):
    uuid_obj = uuid_module.UUID(uuid_str)
    uuid_fields = list(uuid_obj.fields)
    uuid_fields[0] += n
    return str(uuid_module.UUID(fields=uuid_fields))


class CometBlue(object):
    SUPPORTED_VALUES = {
        'device_name': {
            'description': 'device name',
            'uuid': '00002a00-0000-1000-8000-00805f9b34fb',
            'decode': str,
        },

        'model_number': {
            'description': 'model number',
            'uuid': '00002a24-0000-1000-8000-00805f9b34fb',
            'decode': str,
        },

        'firmware_revision': {
            'description': 'firmware revision',
            'uuid': '00002a26-0000-1000-8000-00805f9b34fb',
            'decode': str,
        },

        'software_revision': {
            'description': 'software revision',
            'uuid': '00002a28-0000-1000-8000-00805f9b34fb',
            'decode': str,
        },

        'manufacturer_name': {
            'description': 'manufacturer name',
            'uuid': '00002a29-0000-1000-8000-00805f9b34fb',
            'decode': str,
        },

        'datetime': {
            'description': 'time and date',
            'uuid': '47e9ee01-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_datetime,
            'encode': _encode_datetime,
        },

        'flags': {
            'description': 'flags',
            'uuid': '47e9ee2a-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_flags,
        },

        'temperatures': {
            'description': 'temperatures',
            'uuid': '47e9ee2b-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_temperatures,
            'encode': _encode_temperatures,
        },

        'battery': {
            'description': 'battery charge',
            'uuid': '47e9ee2c-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_battery,
        },

        'firmware_revision2': {
            'description': 'firmware revision #2',
            'uuid': '47e9ee2d-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': str,
        },

        'lcd_timer': {
            'description': 'LCD timer',
            'uuid': '47e9ee2e-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_lcd_timer,
            'encode': _encode_lcd_timer,
        },

        'pin': {
            'description': 'PIN',
            'uuid': '47e9ee30-47e9-11e4-8939-164230d1df67',
            'encode': _encode_pin,
        },
    }

    SUPPORTED_TABLE_VALUES = {
        'day': {
            'uuid': '47e9ee10-47e9-11e4-8939-164230d1df67',
            'num': 7,
            'read_requires_pin': True,
            'decode': _decode_day,
            'encode': _encode_day,
        },

        'holiday': {
            'uuid': '47e9ee20-47e9-11e4-8939-164230d1df67',
            'num': 8,
            'read_requires_pin': True,
            'decode': _decode_holiday,
            'encode': _encode_holiday,
        },
    }

    def _read_value(self, uuid, decode, pin_required):
        if not self._device.is_connected():
            raise RuntimeError('Not connected')
        if pin_required and (self._pin is None):
            raise RuntimeError('PIN required')

        _log.debug('Reading value "%s" from "%s"...',
                   uuid, self._device_address)
        value = self._device.read_by_uuid(uuid)
        _log.debug('Read value "%s" from "%s": %r',
                   uuid, self._device_address, value)
        if len(value) != 1:
            raise RuntimeError('Got more than one value')
        return decode(value[0])

    def _read_value_n(self, uuid, decode, pin_required, max_n, n):
        if (n < 0) or (n >= max_n):
            raise RuntimeError('Invalid table row number')
        return self._read_value(_increase_uuid(uuid, n), decode, pin_required)

    def _write_value(self, uuid, encode, value):
        if not self._device.is_connected():
            raise RuntimeError('Not connected')
        if self._pin is None:
            raise RuntimeError('PIN required')

        _log.debug('Writing value "%s" to "%s": %r...',
                   uuid, self._device_address, value)
        self._device.write_by_handle(self._chars[uuid], encode(value))
        _log.debug('Wrote value "%s" to "%s": %r',
                   uuid, self._device_address, value)

    def _write_value_n(self, uuid, encode, max_n, n, value):
        if (n < 0) or (n >= max_n):
            raise RuntimeError('Invalid table row number')
        return self._write_value(_increase_uuid(uuid, n), encode, value)

    def __init__(self, address, adapter='hci0', channel_type='public',
                 security_level='low', pin=None):
        self._device_address = address
        self._device = gattlib.GATTRequester(str(address), False, str(adapter))
        self._channel_type = channel_type
        self._security_level = security_level
        self._chars = None
        self._pin = pin

        for val_name, val_conf in six.iteritems(self.SUPPORTED_VALUES):
            if 'decode' in val_conf:
                setattr(
                        self,
                        'get_' + val_name,
                        functools.partial(
                                self._read_value,
                                str(val_conf['uuid']),
                                val_conf['decode'],
                                val_conf.get('read_requires_pin', False)))
            if 'encode' in val_conf:
                setattr(
                        self,
                        'set_' + val_name,
                        functools.partial(
                                self._write_value,
                                str(val_conf['uuid']),
                                val_conf['encode']))

        for val_name, val_conf in six.iteritems(self.SUPPORTED_TABLE_VALUES):
            if 'decode' in val_conf:
                setattr(
                        self,
                        'get_' + val_name,
                        functools.partial(
                                self._read_value_n,
                                str(val_conf['uuid']),
                                val_conf['decode'],
                                val_conf.get('read_requires_pin', False),
                                val_conf['num']))
            if 'encode' in val_conf:
                setattr(
                        self,
                        'set_' + val_name,
                        functools.partial(
                                self._write_value_n,
                                str(val_conf['uuid']),
                                val_conf['encode'],
                                val_conf['num']))

    def __enter__(self):
        _log.info('Connecting to device "%s"...', self._device_address)
        self._device.connect(wait=True, channel_type=self._channel_type,
                             security_level=self._security_level)

        _log.debug('Discovering characteristics for "%s"...',
                   self._device_address)
        chars = self._device.discover_characteristics(0x0001, 0xffff, '')
        _log.debug('Discovered characteristics for "%s": %r',
                   self._device_address, chars)
        self._chars = dict(
                (char_data['uuid'], char_data['value_handle'])
                for char_data in chars)

        if self._pin is not None:
            try:
                self.set_pin(self._pin)
            except RuntimeError as exc:
                raise RuntimeError('Invalid PIN', exc)

        _log.info('Connected to device "%s"', self._device_address)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._device.is_connected():
            _log.info('Disconnecting from device "%s"...', self._device_address)
            self._device.disconnect()
            _log.info('Disconnected from device "%s"', self._device_address)

    def get_days(self):
        return list(map(self.get_day, range(7)))

    def get_holidays(self):
        return list(map(self.get_holiday, range(8)))

    def backup(self):
        _log.info('Saving all supported values from "%s"...',
                  self._device_address)

        data = {}

        for val_name, val_conf in six.iteritems(self.SUPPORTED_VALUES):
            if ('decode' not in val_conf) or ('encode' not in val_conf):
                # Skip read-only or write-only value.
                continue
            if val_name in ('datetime', ):
                # Restoring this from backup makes no sense.
                continue

            data[val_name] = getattr(self, 'get_' + val_name)()

        for val_name in 'days', 'holidays':
            data[val_name] = getattr(self, 'get_' + val_name)()

        _log.info('All supported values from "%s" saved', self._device_address)

        return data

    def set_days(self, value):
        for day_n, day in zip(itertools.count(), value):
            self.set_day(day_n, day)

    def set_holidays(self, value):
        for holiday_n, holiday in zip(itertools.count(), value):
            self.set_holiday(holiday_n, holiday)

    def restore(self, data):
        _log.info('Restoring values from backup for "%s"...',
                  self._device_address)
        _log.debug('Backup data: %r', data)

        for val_name, val_data in six.iteritems(data):
            getattr(self, 'set_' + val_name)(val_data)

        if 'datetime' not in data:
            self.set_datetime(datetime.datetime.now())

        _log.info('Values from backup for "%s" successfully restored',
                  self._device_address)
