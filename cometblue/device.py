from __future__ import absolute_import

import functools
import logging

import gattlib
import six


_log = logging.getLogger(__name__)


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
    }

    def _read_value(self, uuid, decode):
        if not self._device.is_connected():
            raise RuntimeError('Not connected')

        _log.debug('Reading value "%s" from "%s"...',
                   uuid, self._device_address)
        value = self._device.read_by_uuid(uuid)
        _log.debug('Read value "%s" from "%s": %r',
                   uuid, self._device_address, value)
        if len(value) != 1:
            raise RuntimeError('Got more than one value')
        return decode(value[0])

    def __init__(self, address, adapter='hci0', channel_type='public',
                 security_level='low'):
        self._device_address = address
        self._device = gattlib.GATTRequester(str(address), False, str(adapter))
        self._channel_type = channel_type
        self._security_level = security_level

        for val_name, val_conf in six.iteritems(self.SUPPORTED_VALUES):
            setattr(
                    self,
                    'get_' + val_name,
                    functools.partial(
                            self._read_value,
                            str(val_conf['uuid']),
                            val_conf['decode']))

    def __enter__(self):
        _log.info('Connecting to device "%s"...', self._device_address)
        self._device.connect(wait=True, channel_type=self._channel_type,
                             security_level=self._security_level)
        _log.info('Connected to device "%s"', self._device_address)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._device.is_connected():
            _log.info('Disconnecting from device "%s"...', self._device_address)
            self._device.disconnect()
            _log.info('Disconnected from device "%s"', self._device_address)
