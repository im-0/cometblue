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

def probe_candidate(_device):
    name = _device.alias()
    address = _device.mac_address
    try:
        with _device as device:
            manufacturer_name = device.get_manufacturer_name().lower()
            model_number = device.get_model_number().lower()

            if (manufacturer_name, model_number) in _SUPPORTED_DEVICES:
                return (device.mac_address, str(name))

    except RuntimeError as exc:
        _log.debug('Skipping device "%s" ("%s"), reason: %r' % (name, address, str(exc)))
    return None


def discover_candidates(manager, timeout=10):
    _log.info('Probing for candidate devices...')

    manager.start_discovery()
    time.sleep(timeout)
    manager.stop_discovery()

    devices = manager.devices()
    _log.debug('All discovered devices: %r', [(device.mac_address, str(device.alias())) for device in devices])
    return devices

def discover(manager, timeout=10):
    _log.info('Starting discovery on adapter "%s" with %u seconds timeout...',
              manager.adapter_name, timeout)
    devices = discover_candidates(manager, timeout)

    filtered_devices = {}
    for device in devices:
        device_entry = cometblue.discovery.probe_candidate(device)
        if not device_entry is None:
            filtered_devices.update(dict([device_entry]))
    _log.info('Discovery finished')
    return filtered_devices
