from __future__ import absolute_import

import datetime
import functools
import logging
import struct

import gattlib
import six


_PIN_STRUCT = '<I'
_DATETIME_STRUCT = '<BBBBB'
_TEMPERATURES_STRUCT = '<bbbbbbb'
_LCD_TIMER_STRUCT = '<BB'

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
