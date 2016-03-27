from __future__ import absolute_import

import logging

import gattlib
import six

import cometblue.device


_SUPPORTED_DEVICES = (
    ('eurotronic gmbh', 'comet blue'),
)

_log = logging.getLogger(__name__)


def discover(adapter='hci0', timeout=10, channel_type='public',
             security_level='low'):
    _log.info('Starting discovery on adapter "%s" with %u seconds timeout...',
              adapter, timeout)
    # TODO: Python3
    service = gattlib.DiscoveryService(str(adapter))
    devices = service.discover(timeout)
    _log.debug('All discovered devices: %r', devices)

    filtered_devices = {}
    for address, name in six.iteritems(devices):
        try:
            with cometblue.device.CometBlue(
                    address,
                    adapter=adapter,
                    channel_type=channel_type,
                    security_level=security_level) as device:
                manufacturer_name = device.get_manufacturer_name().lower()
                model_number = device.get_model_number().lower()
                if (manufacturer_name, model_number) in _SUPPORTED_DEVICES:
                    filtered_devices[address] = name
        except RuntimeError as exc:
            _log.debug('Skipping device "%s" ("%s") because of '
                       'exception: %r',
                       name, address, exc)

    _log.info('Discovery finished')
    return filtered_devices
