# vim: tabstop=4 shiftwidth=4 expandtab
from __future__ import absolute_import

import datetime
import functools
import itertools
import logging
import struct
import uuid as uuid_module

import gatt
import time
import six


_PIN_STRUCT_PACKING = '<I'
_BATTERY_STRUCT_PACKING = '<B'
_DATETIME_STRUCT_PACKING = '<BBBBB'
_STATUS_STRUCT_PACKING = '<BBB'
_TEMPERATURES_STRUCT_PACKING = '<bbbbbbb'
_LCD_TIMER_STRUCT_PACKING = '<BB'
_DAY_STRUCT_PACKING = '<BBBBBBBB'
_HOLIDAY_STRUCT_PACKING = '<BBBBBBBBb'

_log = logging.getLogger(__name__)


def _encode_pin(pin):
    return struct.pack(_PIN_STRUCT_PACKING, pin)


def _decode_datetime(value):
    mi, ho, da, mo, ye = struct.unpack(_DATETIME_STRUCT_PACKING, value)
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
            _DATETIME_STRUCT_PACKING,
            dt.minute,
            dt.hour,
            dt.day,
            dt.month,
            dt.year - 2000)

_STATUS_BITMASKS = {
    'childlock': 0x80,
    'manual_mode': 0x1,
    'adapting': 0x400,
    'not_ready': 0x200,
    'installing': 0x400 | 0x200 | 0x100,
    'motor_moving': 0x100,
    'antifrost_activated': 0x10,
    'satisfied': 0x80000,
    'low_battery': 0x800
}

def _decode_status(value):
    state_bytes = struct.unpack(_STATUS_STRUCT_PACKING, value)
    state_dword = struct.unpack('<I', value + b'\x00')[0]

    report = {}
    masked_out = 0
    for key, mask in _STATUS_BITMASKS.items():
        report[key] = bool(state_dword & mask == mask)
        masked_out |= mask

    report['state_as_dword'] = state_dword
    report['unused_bits'] = state_dword & ~masked_out

    return report


def _encode_status(value):
    status_dword = 0
    for key, state in value.items():
        if not state:
            continue

        if not key in _STATUS_BITMASKS:
            _log.error('Unknown flag ' + key)
            continue

        status_dword |= _STATUS_BITMASKS[key]

    value = struct.pack('<I', status_dword)
    # downcast to 3 bytes
    return struct.pack(_STATUS_STRUCT_PACKING, *[int(byte) for byte in value[:3]])


def _decode_temperatures(value):
    cur_temp, manual_temp, target_low, target_high, offset_temp, \
            window_open_detect, window_open_minutes = struct.unpack(
                    _TEMPERATURES_STRUCT_PACKING, value)
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
            _TEMPERATURES_STRUCT_PACKING,
            -128,  # current_temp
            _temp_float_to_int(temps, 'manual_temp'),
            _temp_float_to_int(temps, 'target_temp_l'),
            _temp_float_to_int(temps, 'target_temp_h'),
            _temp_float_to_int(temps, 'offset_temp'),
            _temp_int_to_int(temps, 'window_open_detection'),
            _temp_int_to_int(temps, 'window_open_minutes'))


def _decode_str(value):
    return value.decode()


def _decode_battery(value):
    value = struct.unpack(_BATTERY_STRUCT_PACKING, value)[0]
    if value == 255:
        return None
    return value


def _decode_lcd_timer(value):
    preload, current = struct.unpack(_LCD_TIMER_STRUCT_PACKING, value)
    return {
        'preload': preload,
        'current': current,
    }


def _encode_lcd_timer(lcd_timer):
    return struct.pack(
            _LCD_TIMER_STRUCT_PACKING,
            lcd_timer['preload'],
            0)


class _day_period_cmp(object):
    def __init__(self, period):
        self.period = period

    def __lt__(self, other):
        if self.period['start'] is None:
            return False
        if other.period['start'] is None:
            return True
        return self.period['start'] < other.period['start']

    def __gt__(self, other):
        return other < self

    def __eq__(self, other):
        return self.period['start'] == other.period['start']

    def __le__(self, other):
        return self == other or self < other

    def __ge__(self, other):
        return self == toher or self > other

    def __ne__(self, other):
        return not self == other

def _decode_day(value):
    max_raw_time = ((23 * 60) + 59) / 10

    raw_time_values = list(struct.unpack(_DAY_STRUCT_PACKING, value))
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
                start = datetime.time(hour=raw_start // 60,
                                      minute=raw_start % 60)

            if raw_end > max_raw_time:
                end = datetime.time(23, 59, 59)
            else:
                raw_end *= 10
                end = datetime.time(hour=raw_end // 60,
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

    day.sort(key=_day_period_cmp)

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
            start = (period['start'].hour * 60 + period['start'].minute) // 10
            end = (period['end'].hour * 60 + period['end'].minute) // 10

        if start == 0:
            start = 255
        if end == 0:
            end = 255

        values.append(start)
        values.append(end)

    return struct.pack(_DAY_STRUCT_PACKING, *values)


def _decode_holiday(value):
    ho_start, da_start, mo_start, ye_start, \
            ho_end, da_end, mo_end, ye_end, \
            temp = struct.unpack(_HOLIDAY_STRUCT_PACKING, value)

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
        return struct.pack(_HOLIDAY_STRUCT_PACKING,
                           128, 128, 128, 128, 128, 128, 128, 128, -128)

    if (holiday['start'].year < 2000) or (holiday['end'].year < 2000):
        raise RuntimeError('Invalid year')

    return struct.pack(
            _HOLIDAY_STRUCT_PACKING,
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

class CometBlueManager(gatt.DeviceManager):
    def __init__(self, adapter_name):
        super().__init__(adapter_name)

    def make_device(self, mac_address):
        return CometBlue(mac_address = mac_address, manager = self)

class CometBlue(gatt.Device):
    SUPPORTED_VALUES = {
        'device_name': {
            'description': 'device name',
            'uuid': '00002a00-0000-1000-8000-00805f9b34fb',
            'decode': _decode_str,
        },

        'model_number': {
            'description': 'model number',
            'uuid': '00002a24-0000-1000-8000-00805f9b34fb',
            'decode': _decode_str,
        },

        'firmware_revision': {
            'description': 'firmware revision',
            'uuid': '00002a26-0000-1000-8000-00805f9b34fb',
            'decode': _decode_str,
        },

        'software_revision': {
            'description': 'software revision',
            'uuid': '00002a28-0000-1000-8000-00805f9b34fb',
            'decode': _decode_str,
        },

        'manufacturer_name': {
            'description': 'manufacturer name',
            'uuid': '00002a29-0000-1000-8000-00805f9b34fb',
            'decode': _decode_str,
        },

        'datetime': {
            'description': 'time and date',
            'uuid': '47e9ee01-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_datetime,
            'encode': _encode_datetime,
        },

        'status': {
            'description': 'status',
            'uuid': '47e9ee2a-47e9-11e4-8939-164230d1df67',
            'read_requires_pin': True,
            'decode': _decode_status,
            'encode': _encode_status,
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
            'decode': _decode_str,
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

    def _cb_read_value(self, uuid, decode, pin_required):
        if not self.is_connected():
            raise RuntimeError('Not connected')

        if pin_required and (self._pin is None):
            raise RuntimeError('PIN required')

        if pin_required:
            self._cb_wait_pinok()

        if self.aborter():
            raise StopIteration('Operation aborted due to external request')

        _log.debug('Reading value "%s" from "%s"...',
                   uuid, self.mac_address)

        characteristics_handle = self._cb_chars.get(uuid, None)
        if characteristics_handle is None:
            raise RuntimeError('Handle for uuid "%s" not found, perhaps sync issue?' % (uuid))

        value = characteristics_handle.read_value()

        _log.debug('Read value "%s" from "%s": %r',
                   uuid, self.mac_address, value)
        if len(value.signature) != 1:
            raise RuntimeError('Got more than one value')

        value = bytes(int(byte) for byte in value)
        value = decode(value)
        _log.debug('Decoded value "%s" from "%s": %r',
                   uuid, self.mac_address, value)
        return value

    def _cb_read_value_n(self, uuid, decode, pin_required, max_n, n):
        if (n < 0) or (n >= max_n):
            raise RuntimeError('Invalid table row number')
        return self._cb_read_value(_increase_uuid(uuid, n), decode, pin_required)

    def characteristic_write_value_succeeded(self, characteristic):
        _log.debug("write for " + characteristic.uuid + " succeeded")
        self._cb_writes[characteristic.uuid] = True

    def characteristic_write_value_failed(self, characteristic, error):
        self._cb_writes[characteristic.uuid] = False
        _log.error('Value write failed for characteristic "%s" with error "%s"' % (characteristic.uuid, error))

    def _cb_wait_write_result(self, uuid):
        iterations_limit = self._cb_complete_timeout / self._cb_complete_sleep
        i = 0
        while i < iterations_limit and not self.aborter():
            i += 1
            if not self.is_connected():
                raise StopIteration('Device disconnected while waiting for reply')

            if not self._cb_writes.get(uuid, None) is None:
                return self._cb_writes[uuid]
            time.sleep(self._cb_complete_sleep)

        if self.aborter():
            raise StopIteration('Operation aborted due to external request')

        raise StopIteration('Operation has not been completed within timeout')


    def _cb_wait_pinok(self):
        uuid = self.SUPPORTED_VALUES['pin']['uuid']
        if not self._cb_wait_write_result(uuid):
            _log.debug('Failed to write pin characteristic')
            raise StopIteration('Failed to write pin to device')

    def _cb_write_value(self, uuid, encode, value):
        if not self.is_connected():
            raise RuntimeError('Not connected')

        if self._pin is None:
            raise RuntimeError('PIN required')

        # precaution - glib main loop runs in the same thread as services_discovered,
        # therefore waiting for pin confirmation inside write would cause livelock, as there
        # would be no main loop available for dbus call
        pin_uuid = self.SUPPORTED_VALUES['pin']['uuid']
        if pin_uuid != uuid:
            self._cb_wait_pinok()

        _log.debug('Writing value "%s" to "%s": %r...',
                   uuid, self.mac_address, value)

        characteristics_handle = self._cb_chars.get(uuid, None)
        if characteristics_handle is None:
            if self._cb_chars:
                raise NotImplementedError('Device does not offer characteristics with uuid "%s", required to fulfill the request' % (uuid))
            else:
                raise RuntimeError('Handle for characteristics uuid "%s" not found, perhaps sync issue?' % (uuid))

        self._cb_writes[uuid] = None
        value = encode(value)
        characteristics_handle.write_value(value)

        if not self.blocking:
            _log.debug('Assuming successfull write "%s" to "%s": %r', uuid, self.mac_address, value)
            return

        if self._cb_wait_write_result(uuid):
            _log.debug('Confirmed write value "%s" to "%s": %r', uuid, self.mac_address, value)
            return

        _log.debug('Write failed for "%s" to "%s": %r', uuid, self.mac_address, value)


    def _cb_write_value_n(self, uuid, encode, max_n, n, value):
        if (n < 0) or (n >= max_n):
            raise RuntimeError('Invalid table row number')
        return self._cb_write_value(_increase_uuid(uuid, n), encode, value)

    @property
    def blocking(self):
        return self._blocking

    @blocking.setter
    def blocking(self, blocking):
        self._blocking = blocking

    @property
    def aborter(self):
        return self._aborter

    @aborter.setter
    def aborter(self, aborter):
        if aborter is None:
            aborter = lambda: False
        self._aborter = aborter

    @property
    def pin(self):
        return self._pin

    @pin.setter
    def pin(self, _pin):
        self._pin = _pin
        return self._pin

    def __init__(self, mac_address, manager, pin=None, aborter=None):
        super().__init__(mac_address, manager)

        self._cb_chars = None
        self._cb_writes = {}
        self._pin = pin
        # for manual connect + disconnect vs. __enter__ vs. __exit__
        self._enter_nesting = 0
        self._cb_enter_managed_connection = True
        self._cb_setup_methods()
        self._blocking = True
        self.aborter = aborter
        self._cb_complete_timeout = 60
        self._cb_complete_sleep = 0.050

    def _cb_setup_methods(self):
        for val_name, val_conf in six.iteritems(self.SUPPORTED_VALUES):
            if 'decode' in val_conf:
                setattr(
                        self,
                        'get_' + val_name,
                        functools.partial(
                                self._cb_read_value,
                                str(val_conf['uuid']),
                                val_conf['decode'],
                                val_conf.get('read_requires_pin', False)))
            if 'encode' in val_conf:
                setattr(
                        self,
                        'set_' + val_name,
                        functools.partial(
                                self._cb_write_value,
                                str(val_conf['uuid']),
                                val_conf['encode']))

        for val_name, val_conf in six.iteritems(self.SUPPORTED_TABLE_VALUES):
            if 'decode' in val_conf:
                setattr(
                        self,
                        'get_' + val_name,
                        functools.partial(
                                self._cb_read_value_n,
                                str(val_conf['uuid']),
                                val_conf['decode'],
                                val_conf.get('read_requires_pin', False),
                                val_conf['num']))
            if 'encode' in val_conf:
                setattr(
                        self,
                        'set_' + val_name,
                        functools.partial(
                                self._cb_write_value_n,
                                str(val_conf['uuid']),
                                val_conf['encode'],
                                val_conf['num']))

    def __str__(self):
        return \
            "device_" + self.alias() \
            + "@" + self.mac_address + "_[" \
            + ("connected" if self.is_connected() else "disconnected") \
            + ", " \
            + ("services resolved" if self.is_services_resolved() else "pending service resolution") + "]"

    def enumerate_unhandled_characteristics(self):
        handled = []
        for _, simple in self.SUPPORTED_VALUES.items():
            handled.append(simple['uuid'])
        for _, tabbed in self.SUPPORTED_TABLE_VALUES.items():
            for i in range(tabbed['num']):
                handled.append(_increase_uuid(tabbed['uuid'], i))

        unhandled_characteristics = []
        for characteristics in self._cb_chars.keys():
            if characteristics not in handled:
                unhandled_characteristics.append(characteristics)
        return unhandled_characteristics

    def services_resolved(self):
        super().services_resolved()
        self._cb_chars = dict(
                (str(characteristics_handle.uuid), characteristics_handle)
                for service_handle in self.services
                for characteristics_handle in service_handle.characteristics )

        _log.debug('Discovered characteristics for "%s": %r',
                   self.mac_address, self._cb_chars.keys())

        if self._pin is not None:
            try:
                self.blocking = False
                self.set_pin(self._pin)
            except RuntimeError as exc:
                raise RuntimeError('Invalid PIN', exc)
            finally:
                self.blocking = True

        unhandled_characteristics = self.enumerate_unhandled_characteristics()
        if unhandled_characteristics:
            _log.info('Unknown characteristics discovered on "%s": %r',
                self.mac_address, unhandled_characteristics)


    def __enter__(self):
        self._enter_nesting += 1
        if not self.is_connected():
            self.connect()

        self.attempt_to_get_ready()
        if not self.ready():
            raise RuntimeError("Unable to connect & resolve the device")

        return self

    def connect(self):
        # if connect() is called before __enter__, make it not managed
        if self._enter_nesting == 0:
            self._cb_enter_managed_connection = False

        _log.info('Connecting to device "%s"...', self.mac_address)
        super().connect()

        if not self.is_connected():
            raise RuntimeError('Failed to connect the device')

    def attempt_to_get_ready(self):
        iterations_limit = self._cb_complete_timeout / self._cb_complete_sleep
        i = 0
        while not self.ready() and i < iterations_limit:
                i += 1
                time.sleep(self._cb_complete_sleep)
        return self.ready()

    def ready(self):
        return self.is_connected() and self.is_services_resolved() and bool(self._cb_chars)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._enter_nesting -= 1

        if self._enter_nesting == 0 and self._cb_enter_managed_connection:
            self.disconnect()

    def disconnect(self):
        if not self.is_connected():
            return

        _log.info('Disconnecting from device "%s"...', self.mac_address)
        try:
            super().disconnect()
            _log.info('Disconnected from device "%s"', self.mac_address)
        except:
            _log.error('Failed disconnect from device "%s", considering disconnected anyway', self.mac_address)

    def get_days(self):
        return list(map(self.get_day, range(7)))

    def get_holidays(self):
        return list(map(self.get_holiday, range(8)))

    def backup(self):
        _log.info('Saving all supported values from "%s"...',
                  self.mac_address)

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

        _log.info('All supported values from "%s" saved', self.mac_address)

        return data

    def set_days(self, value):
        for day_n, day in zip(itertools.count(), value):
            self.set_day(day_n, day)

    def set_holidays(self, value):
        for holiday_n, holiday in zip(itertools.count(), value):
            self.set_holiday(holiday_n, holiday)

    def restore(self, data):
        _log.info('Restoring values from backup for "%s"...',
                  self.mac_address)
        _log.debug('Backup data: %r', data)

        for val_name, val_data in six.iteritems(data):
            getattr(self, 'set_' + val_name)(val_data)

        if 'datetime' not in data:
            self.set_datetime(datetime.datetime.now())

        _log.info('Values from backup for "%s" successfully restored',
                  self.mac_address)
