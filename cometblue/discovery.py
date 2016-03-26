from __future__ import absolute_import

import logging

import gattlib


_log = logging.getLogger(__name__)


def discover(adapter='hci0', timeout=10):
    _log.info('Starting discovery on adapter "%s" with %u seconds timeout...',
              adapter, timeout)
    # TODO: Python3
    service = gattlib.DiscoveryService(str(adapter))
    devices = service.discover(timeout)
    _log.info('Discovery finished')
    return devices
