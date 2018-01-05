# vim: tabstop=4 shiftwidth=4 expandtab
from __future__ import absolute_import

import logging

import gatt
import time
import six

import cometblue.device


_SUPPORTED_DEVICES = (
    ('eurotronic gmbh', 'comet blue'),
)

_log = logging.getLogger(__name__)


def discover(manager, timeout=10):
    _log.info('Starting discovery on adapter "%s" with %u seconds timeout...',
              manager.adapter_name, timeout)

    manager.start_discovery()
    time.sleep(timeout)
    manager.stop_discovery()

    devices = manager.devices()
    _log.debug('All discovered devices: %r', [(device.mac_address, str(device.alias())) for device in devices])

    filtered_devices = {}

    for _device in devices:
        name = _device.alias()
        address = _device.mac_address
        try:
            with _device as device:
                manufacturer_name = device.get_manufacturer_name().lower()
                model_number = device.get_model_number().lower()

                if (manufacturer_name, model_number) in _SUPPORTED_DEVICES:
                    filtered_devices[device.mac_address] = name

        except RuntimeError as exc:
            _log.debug('Skipping device "%s" ("%s") because of '
                       'exception: %r' % (name, address, exc))

    _log.info('Discovery finished')
    return filtered_devices
